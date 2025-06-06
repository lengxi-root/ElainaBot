#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
日志数据库中间件
负责将日志记录到数据库中而不是文件系统
"""

import os
import sys
import time
import json
import queue
import threading
import logging
import datetime
import traceback
import pymysql
from pymysql.cursors import DictCursor
from concurrent.futures import ThreadPoolExecutor

# 导入配置
from config import LOG_DB_CONFIG, DB_CONFIG

# 设置日志记录器
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('log_db')

# 全局常量
DEFAULT_BATCH_SIZE = 100
DEFAULT_INSERT_INTERVAL = 10  # 秒
LOG_TYPES = ['received', 'plugin', 'framework', 'error']
TABLE_SUFFIX = {
    'received': 'message',
    'plugin': 'plugin',
    'framework': 'framework',
    'error': 'error'
}

class LogDatabasePool:
    """日志数据库连接池，专门用于日志写入"""
    _instance = None
    _lock = threading.Lock()
    _pool = []
    _busy_connections = {}
    _initialized = False
    
    def __new__(cls):
        """单例模式"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(LogDatabasePool, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        """初始化连接池"""
        with self._lock:
            if not self._initialized:
                self._thread_pool = ThreadPoolExecutor(
                    max_workers=LOG_DB_CONFIG.get('pool_size', 3),
                    thread_name_prefix="LogDBPool"
                )
                self._init_pool()
                
                # 启动池维护线程
                self._maintenance_thread = threading.Thread(
                    target=self._maintain_pool,
                    daemon=True,
                    name="LogDBPoolMaintenance"
                )
                self._maintenance_thread.start()
                
                self._initialized = True
                logger.info("日志数据库连接池初始化完成")
    
    def _init_pool(self):
        """初始化连接池，创建初始连接"""
        try:
            # 创建初始连接
            min_connections = LOG_DB_CONFIG.get('min_pool_size', 2)
            successful_connections = 0
            
            for _ in range(min_connections):
                connection = self._create_connection()
                if connection:
                    successful_connections += 1
                    self._pool.append({
                        'connection': connection,
                        'created_at': time.time(),
                        'last_used': time.time()
                    })
            
            logger.info(f"日志数据库连接池初始化成功，创建了{successful_connections}个连接")
        except Exception as e:
            error_msg = f"初始化日志数据库连接池失败: {str(e)}"
            logger.error(error_msg)
            # 如果初始化失败，记录到标准错误输出
            print(error_msg, file=sys.stderr)
    
    def _create_connection(self):
        """创建新的数据库连接"""
        # 根据配置确定使用主数据库还是独立日志数据库
        db_config = DB_CONFIG if LOG_DB_CONFIG.get('use_main_db', False) else LOG_DB_CONFIG
        
        retry_count = LOG_DB_CONFIG.get('max_retry', 3)
        retry_delay = LOG_DB_CONFIG.get('retry_interval', 2)
        last_error = None
        
        for i in range(retry_count):
            try:
                connection = pymysql.connect(
                    host=db_config.get('host', 'localhost'),
                    port=db_config.get('port', 3306),
                    user=db_config.get('user', 'root'),
                    password=db_config.get('password', ''),
                    database=db_config.get('database', ''),
                    charset=db_config.get('charset', 'utf8mb4'),
                    cursorclass=DictCursor,
                    connect_timeout=db_config.get('connect_timeout', 3),
                    read_timeout=db_config.get('read_timeout', 10),
                    write_timeout=db_config.get('write_timeout', 10),
                    autocommit=db_config.get('autocommit', True)
                )
                return connection
            except Exception as e:
                last_error = e
                if i < retry_count - 1:
                    logger.warning(f"创建日志数据库连接失败(尝试 {i+1}/{retry_count}): {str(e)}")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
        
        logger.error(f"创建日志数据库连接失败，已达到最大重试次数: {str(last_error)}")
        return None
    
    def _check_connection(self, connection):
        """检查连接是否有效"""
        try:
            connection.ping(reconnect=True)
            return True
        except Exception:
            return False
    
    def _close_connection_safely(self, connection):
        """安全关闭连接"""
        try:
            connection.close()
        except Exception:
            pass
    
    def get_connection(self):
        """获取数据库连接"""
        connection_id = threading.get_ident()
        
        # 如果当前线程已经有连接，直接返回
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        
        with self._lock:
            # 尝试从池中获取连接
            if self._pool:
                conn_info = self._pool.pop(0)
                connection = conn_info['connection']
                
                # 检查连接是否有效，无效则创建新连接
                if not self._check_connection(connection):
                    self._close_connection_safely(connection)
                    connection = self._create_connection()
                    if not connection:
                        return None
                
                # 标记为忙碌状态
                self._busy_connections[connection_id] = {
                    'connection': connection,
                    'acquired_at': time.time(),
                    'created_at': conn_info.get('created_at', time.time())
                }
                return connection
            
            # 如果池中没有连接，创建新连接
            connection = self._create_connection()
            if connection:
                self._busy_connections[connection_id] = {
                    'connection': connection,
                    'acquired_at': time.time(),
                    'created_at': time.time()
                }
                return connection
        
        return None
    
    def release_connection(self, connection=None):
        """释放数据库连接回连接池"""
        connection_id = threading.get_ident()
        
        with self._lock:
            try:
                # 如果提供了具体连接对象，查找对应的连接ID
                if connection is not None:
                    found_id = None
                    for conn_id, conn_info in list(self._busy_connections.items()):
                        if conn_info['connection'] is connection:
                            found_id = conn_id
                            break
                    
                    if found_id:
                        connection_id = found_id
                    else:
                        # 连接不在忙碌列表中，可能是已释放或外部连接
                        self._close_connection_safely(connection)
                        return
                
                # 获取当前线程的连接
                conn_info = self._busy_connections.get(connection_id)
                if not conn_info:
                    return
                
                connection = conn_info['connection']
                
                # 从忙碌连接中移除
                del self._busy_connections[connection_id]
                
                # 检查连接是否有效，有效则放回池中
                if self._check_connection(connection):
                    # 放回连接池
                    self._pool.append({
                        'connection': connection,
                        'created_at': conn_info.get('created_at', time.time()),
                        'last_used': time.time()
                    })
                else:
                    self._close_connection_safely(connection)
            except Exception as e:
                logger.error(f"释放连接过程中出错: {str(e)}")
    
    def _maintain_pool(self):
        """维护连接池，定期检查连接状态"""
        while True:
            try:
                time.sleep(60)  # 每60秒检查一次
                
                with self._lock:
                    current_time = time.time()
                    
                    # 清理长时间未使用的连接
                    for i in range(len(self._pool) - 1, -1, -1):
                        if i < len(self._pool):  # 防止索引越界
                            conn_info = self._pool[i]
                            # 连接超过5分钟未使用，关闭它
                            if current_time - conn_info['last_used'] > 300:
                                try:
                                    conn_info['connection'].close()
                                    del self._pool[i]
                                except:
                                    pass
                    
                    # 确保最小连接数
                    min_connections = LOG_DB_CONFIG.get('min_pool_size', 2)
                    while len(self._pool) < min_connections:
                        conn = self._create_connection()
                        if conn:
                            self._pool.append({
                                'connection': conn,
                                'created_at': time.time(),
                                'last_used': time.time()
                            })
                        else:
                            break
            except Exception as e:
                logger.error(f"连接池维护过程中出错: {str(e)}")


class LogDatabaseManager:
    """日志数据库管理器，负责创建表和写入日志"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """单例模式"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(LogDatabaseManager, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        """初始化日志数据库管理器"""
        self.pool = LogDatabasePool()
        self.tables_created = set()  # 已创建表的集合
        
        # 日志队列，按类型分开
        self.log_queues = {log_type: queue.Queue() for log_type in LOG_TYPES}
        
        # 初始化表结构
        if LOG_DB_CONFIG.get('create_tables', True):
            self._create_all_tables()
        
        # 启动定期保存线程
        self._save_interval = LOG_DB_CONFIG.get('insert_interval', DEFAULT_INSERT_INTERVAL)
        self._batch_size = LOG_DB_CONFIG.get('batch_size', DEFAULT_BATCH_SIZE)
        
        self._stop_event = threading.Event()
        self._save_thread = threading.Thread(
            target=self._periodic_save,
            daemon=True,
            name="LogDBSaveThread"
        )
        self._save_thread.start()
        
        # 启动清理过期日志表的线程
        if LOG_DB_CONFIG.get('auto_cleanup', False):
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_old_tables,
                daemon=True,
                name="LogDBCleanupThread"
            )
            self._cleanup_thread.start()
        
        logger.info("日志数据库管理器初始化完成")
    
    def _get_table_name(self, log_type):
        """获取日志表名称"""
        prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
        suffix = TABLE_SUFFIX.get(log_type, log_type)
        
        if LOG_DB_CONFIG.get('table_per_day', True):
            # 按天分表，格式：prefix_YYYYMMDD_suffix
            today = datetime.datetime.now().strftime('%Y%m%d')
            return f"{prefix}{today}_{suffix}"
        else:
            # 不分表，固定表名
            return f"{prefix}{suffix}"
    
    def _create_table(self, log_type):
        """创建日志表"""
        table_name = self._get_table_name(log_type)
        
        # 如果表已经创建过，不再创建
        if table_name in self.tables_created:
            return True
        
        connection = self.pool.get_connection()
        if not connection:
            logger.error(f"创建日志表失败: 无法获取数据库连接")
            return False
        
        cursor = None
        try:
            cursor = connection.cursor()
            
            # 检查表是否存在
            check_query = f"""
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """
            cursor.execute(check_query, (table_name,))
            result = cursor.fetchone()
            
            if result and result['count'] > 0:
                # 表已存在
                self.tables_created.add(table_name)
                return True
            
            # 创建表
            create_table_sql = self._get_create_table_sql(table_name, log_type)
            cursor.execute(create_table_sql)
            
            # 添加索引
            cursor.execute(f"CREATE INDEX idx_{table_name}_time ON {table_name} (timestamp)")
            
            connection.commit()
            self.tables_created.add(table_name)
            logger.info(f"创建日志表 {table_name} 成功")
            return True
            
        except Exception as e:
            logger.error(f"创建日志表 {table_name} 失败: {str(e)}")
            return False
        finally:
            if cursor:
                cursor.close()
            self.pool.release_connection(connection)
    
    def _get_create_table_sql(self, table_name, log_type):
        """获取创建表的SQL语句"""
        # 基础字段
        base_sql = f"""
            CREATE TABLE IF NOT EXISTS `{table_name}` (
                `id` bigint(20) NOT NULL AUTO_INCREMENT,
                `timestamp` datetime NOT NULL,
        """
        
        # 根据日志类型添加特定字段
        if log_type == 'received':
            # 消息日志增加用户ID和群聊ID字段
            specific_fields = """
                `user_id` varchar(255) NOT NULL COMMENT '用户ID',
                `group_id` varchar(255) DEFAULT 'c2c' COMMENT '群聊ID，私聊为c2c',
                `content` text NOT NULL COMMENT '消息内容',
            """
        elif log_type == 'error':
            # 错误日志包含traceback
            specific_fields = """
                `content` text NOT NULL,
                `traceback` text,
            """
        else:
            # 其他日志类型
            specific_fields = """
                `content` text NOT NULL,
            """
        
        end_sql = """
                `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (`id`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """
        
        return base_sql + specific_fields + end_sql
    
    def _create_all_tables(self):
        """创建所有日志表"""
        for log_type in LOG_TYPES:
            self._create_table(log_type)
    
    def add_log(self, log_type, log_data):
        """添加日志到队列"""
        if not LOG_DB_CONFIG.get('enabled', False):
            return False
            
        # 确保日志类型有效
        if log_type not in LOG_TYPES:
            logger.error(f"无效的日志类型: {log_type}")
            return False
            
        # 添加日志到对应队列
        self.log_queues[log_type].put(log_data)
        
        # 如果配置为立即写入，则立即执行保存
        if self._save_interval == 0:
            self._save_logs_to_db()
            
        return True
    
    def _periodic_save(self):
        """定期保存日志到数据库的线程函数"""
        while not self._stop_event.is_set():
            try:
                # 等待一定时间后保存
                if self._save_interval > 0:
                    self._stop_event.wait(self._save_interval)
                    if self._stop_event.is_set():
                        break
                
                # 保存日志
                self._save_logs_to_db()
                
            except Exception as e:
                logger.error(f"定期保存日志线程出错: {str(e)}")
                # 出错后休息一段时间
                time.sleep(5)
    
    def _save_logs_to_db(self):
        """保存日志到数据库"""
        # 为每种日志类型保存
        for log_type in LOG_TYPES:
            self._save_log_type_to_db(log_type)
    
    def _save_log_type_to_db(self, log_type):
        """保存特定类型的日志到数据库"""
        queue_size = self.log_queues[log_type].qsize()
        if queue_size == 0:
            return
            
        # 限制一次批量插入的数量
        batch_size = min(queue_size, self._batch_size)
        
        # 确保表已创建
        if not self._create_table(log_type):
            logger.error(f"无法保存{log_type}日志: 表创建失败")
            return
            
        table_name = self._get_table_name(log_type)
        connection = self.pool.get_connection()
        if not connection:
            logger.error(f"无法保存{log_type}日志: 获取数据库连接失败")
            return
            
        cursor = None
        logs_to_insert = []
        
        try:
            # 准备日志数据
            for _ in range(batch_size):
                try:
                    log_data = self.log_queues[log_type].get_nowait()
                    logs_to_insert.append(log_data)
                except queue.Empty:
                    break
            
            if not logs_to_insert:
                return
                
            # 根据日志类型构建插入SQL
            cursor = connection.cursor()
            
            if log_type == 'received':
                # 消息日志，添加用户ID和群聊ID
                sql = f"""
                    INSERT INTO `{table_name}` 
                    (timestamp, user_id, group_id, content) 
                    VALUES (%s, %s, %s, %s)
                """
                values = [(
                    log.get('timestamp'),
                    log.get('user_id', '未知用户'),
                    log.get('group_id', 'c2c'),
                    log.get('content')
                ) for log in logs_to_insert]
                
            elif log_type == 'error':
                # 错误日志包含traceback
                sql = f"""
                    INSERT INTO `{table_name}` 
                    (timestamp, content, traceback) 
                    VALUES (%s, %s, %s)
                """
                values = [(
                    log.get('timestamp'),
                    log.get('content'),
                    log.get('traceback', '')
                ) for log in logs_to_insert]
            else:
                # 其他日志类型
                sql = f"""
                    INSERT INTO `{table_name}` 
                    (timestamp, content) 
                    VALUES (%s, %s)
                """
                values = [(
                    log.get('timestamp'),
                    log.get('content')
                ) for log in logs_to_insert]
            
            # 执行批量插入
            cursor.executemany(sql, values)
            connection.commit()
            
            logger.debug(f"成功保存{len(logs_to_insert)}条{log_type}日志到表{table_name}")
            
        except Exception as e:
            logger.error(f"保存{log_type}日志失败: {str(e)}")
            
            # 回滚
            if connection:
                try:
                    connection.rollback()
                except:
                    pass
                    
            # 如果配置了在数据库不可用时回退到文件，则执行回退
            if LOG_DB_CONFIG.get('fallback_to_file', True):
                self._fallback_to_file(log_type, logs_to_insert)
                
        finally:
            if cursor:
                cursor.close()
            if connection:
                self.pool.release_connection(connection)
            
            # 标记队列任务完成
            for _ in range(len(logs_to_insert)):
                self.log_queues[log_type].task_done()
    
    def _fallback_to_file(self, log_type, logs):
        """当数据库不可用时，回退到文件记录"""
        try:
            fallback_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
            if not os.path.exists(fallback_dir):
                os.makedirs(fallback_dir)
                
            today = datetime.datetime.now().strftime('%Y-%m-%d')
            fallback_file = os.path.join(fallback_dir, f"{log_type}_{today}.log")
            
            with open(fallback_file, 'a', encoding='utf-8') as f:
                f.write(f"\n--- 数据库写入失败，回退记录 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                for log in logs:
                    timestamp = log.get('timestamp', '')
                    content = log.get('content', '')
                    f.write(f"[{timestamp}] {content}\n")
                    if log_type == 'error' and 'traceback' in log:
                        f.write(f"调用栈信息:\n{log['traceback']}\n")
            
            logger.info(f"日志已回退保存到文件 {fallback_file}")
        except Exception as e:
            logger.error(f"回退到文件保存失败: {str(e)}")
    
    def _cleanup_old_tables(self):
        """清理过期的日志表"""
        if not LOG_DB_CONFIG.get('retention_days', 0) > 0:
            return
            
        while not self._stop_event.is_set():
            try:
                # 每天检查一次
                time.sleep(24 * 60 * 60)
                
                if self._stop_event.is_set():
                    break
                    
                # 获取要保留的最早日期
                retention_days = LOG_DB_CONFIG.get('retention_days', 90)
                earliest_date = datetime.datetime.now() - datetime.timedelta(days=retention_days)
                earliest_date_str = earliest_date.strftime('%Y%m%d')
                
                connection = self.pool.get_connection()
                if not connection:
                    continue
                    
                cursor = None
                try:
                    cursor = connection.cursor()
                    
                    # 获取所有表
                    prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
                    cursor.execute("""
                        SELECT table_name 
                        FROM information_schema.tables 
                        WHERE table_schema = DATABASE() 
                        AND table_name LIKE %s
                    """, (f"{prefix}%",))
                    
                    tables = cursor.fetchall()
                    
                    # 检查并删除过期表
                    for table in tables:
                        table_name = table['table_name']
                        
                        # 检查表名是否包含日期格式(yyyymmdd)
                        parts = table_name.split('_')
                        for part in parts:
                            if len(part) == 8 and part.isdigit():
                                table_date = part
                                if table_date < earliest_date_str:
                                    logger.info(f"删除过期日志表: {table_name}")
                                    cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
                                break
                    
                    connection.commit()
                    logger.info(f"清理过期日志表完成, 保留{retention_days}天内的日志")
                    
                except Exception as e:
                    logger.error(f"清理过期日志表失败: {str(e)}")
                    if connection:
                        try:
                            connection.rollback()
                        except:
                            pass
                finally:
                    if cursor:
                        cursor.close()
                    if connection:
                        self.pool.release_connection(connection)
                        
            except Exception as e:
                logger.error(f"清理日志表线程出错: {str(e)}")
    
    def shutdown(self):
        """关闭日志数据库管理器"""
        self._stop_event.set()
        
        # 确保所有日志都写入数据库
        self._save_logs_to_db()
        
        logger.info("日志数据库管理器已关闭")


# 全局单例
log_db_manager = LogDatabaseManager() if LOG_DB_CONFIG.get('enabled', False) else None

# 导出的API函数
def add_log_to_db(log_type, log_data):
    """添加日志到数据库
    
    Args:
        log_type: 日志类型，可选值: 'received', 'plugin', 'framework', 'error'
        log_data: 日志数据字典，必须包含'timestamp'和'content'键
                 对于'received'类型，可以包含'user_id'和'group_id'
    
    Returns:
        成功返回True，失败返回False
    """
    if log_db_manager:
        # 确保日志数据格式正确
        if not isinstance(log_data, dict):
            logger.error("日志数据必须是字典类型: %s" % str(log_data))
            return False
        # 确保日志包含必要字段
        if 'timestamp' not in log_data:
            log_data['timestamp'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if 'content' not in log_data:
            logger.error("日志数据必须包含'content'字段: %s" % str(log_data))
            return False
        result = log_db_manager.add_log(log_type, log_data)
        if not result:
            logger.error(f"日志写入数据库失败: {log_data}")
        return result
    logger.error("日志数据库管理器未启用，日志未写入: %s" % str(log_data))
    return False 