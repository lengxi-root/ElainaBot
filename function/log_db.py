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
from contextlib import contextmanager

from config import LOG_DB_CONFIG, DB_CONFIG

logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('log_db')

DEFAULT_BATCH_SIZE = 100
DEFAULT_INSERT_INTERVAL = 10
LOG_TYPES = ['received', 'plugin', 'framework', 'error', 'unmatched', 'dau', 'id']
TABLE_SUFFIX = {
    'received': 'message',
    'plugin': 'plugin',
    'framework': 'framework',
    'error': 'error',
    'unmatched': 'unmatched',
    'dau': 'dau',
    'id': 'id'
}

class LogDatabasePool:
    """日志数据库连接池"""
    _instance = None
    _lock = threading.Lock()
    _pool = []
    _busy_connections = {}
    _initialized = False
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(LogDatabasePool, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        with self._lock:
            if not self._initialized:
                self._thread_pool = ThreadPoolExecutor(
                    max_workers=LOG_DB_CONFIG.get('pool_size', 3),
                    thread_name_prefix="LogDBPool"
                )
                self._init_pool()
                
                self._maintenance_thread = threading.Thread(
                    target=self._maintain_pool,
                    daemon=True,
                    name="LogDBPoolMaintenance"
                )
                self._maintenance_thread.start()
                
                self._initialized = True

    def _safe_execute(self, operation, error_msg="操作失败"):
        """安全执行操作"""
        try:
            return operation()
        except Exception as e:
            logger.error(f"{error_msg}: {str(e)}")
            return None

    def _create_connection_with_retry(self):
        """带重试的连接创建"""
        db_config = DB_CONFIG if LOG_DB_CONFIG.get('use_main_db', False) else LOG_DB_CONFIG
        retry_count = LOG_DB_CONFIG.get('max_retry', 3)
        retry_delay = LOG_DB_CONFIG.get('retry_interval', 2)
        
        for i in range(retry_count):
            try:
                return pymysql.connect(
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
            except Exception as e:
                if i < retry_count - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(f"创建连接失败: {str(e)}")
        return None
    
    def _init_pool(self):
        """初始化连接池"""
        min_connections = LOG_DB_CONFIG.get('min_pool_size', 2)
        
        for _ in range(min_connections):
            connection = self._create_connection_with_retry()
            if connection:
                self._pool.append({
                    'connection': connection,
                    'created_at': time.time(),
                    'last_used': time.time()
                })
    
    def _create_connection(self):
        """创建新连接"""
        return self._safe_execute(
            self._create_connection_with_retry,
            "创建数据库连接失败"
        )
    
    def _check_connection(self, connection):
        """检查连接有效性"""
        return self._safe_execute(
            lambda: connection.ping(reconnect=True) or True,
            "检查连接失败"
        ) is not None
    
    def _close_connection_safely(self, connection):
        """安全关闭连接"""
        self._safe_execute(lambda: connection.close(), "关闭连接失败")
    
    def get_connection(self):
        """获取数据库连接"""
        connection_id = threading.get_ident()
        
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        
        with self._lock:
            if self._pool:
                conn_info = self._pool.pop(0)
                connection = conn_info['connection']
                
                if not self._check_connection(connection):
                    self._close_connection_safely(connection)
                    connection = self._create_connection()
                    if not connection:
                        return None
                
                self._busy_connections[connection_id] = {
                    'connection': connection,
                    'acquired_at': time.time(),
                    'created_at': conn_info.get('created_at', time.time())
                }
                return connection
            
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
        """释放连接回池"""
        connection_id = threading.get_ident()
        
        with self._lock:
            if connection is not None:
                found_id = None
                for conn_id, conn_info in list(self._busy_connections.items()):
                    if conn_info['connection'] is connection:
                        found_id = conn_id
                        break
                
                if found_id:
                    connection_id = found_id
                else:
                    self._close_connection_safely(connection)
                    return
            
            conn_info = self._busy_connections.get(connection_id)
            if not conn_info:
                return
            
            connection = conn_info['connection']
            del self._busy_connections[connection_id]
            
            if self._check_connection(connection):
                self._pool.append({
                    'connection': connection,
                    'created_at': conn_info.get('created_at', time.time()),
                    'last_used': time.time()
                })
            else:
                self._close_connection_safely(connection)
    
    def _maintain_pool(self):
        """维护连接池"""
        while True:
            time.sleep(60)
            
            with self._lock:
                current_time = time.time()
                
                # 清理超时连接
                for i in range(len(self._pool) - 1, -1, -1):
                    if i < len(self._pool):
                        conn_info = self._pool[i]
                        if current_time - conn_info['last_used'] > 300:
                            self._safe_execute(
                                lambda: conn_info['connection'].close(),
                                "维护过程中关闭连接失败"
                            )
                            del self._pool[i]
                
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

class LogDatabaseManager:
    """日志数据库管理器"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(LogDatabaseManager, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        self.pool = LogDatabasePool()
        self.tables_created = set()
        self.log_queues = {log_type: queue.Queue() for log_type in LOG_TYPES}
        # ID缓存字典，存储格式：{(chat_type, chat_id): message_id}
        self.id_cache = {}
        self.id_cache_lock = threading.Lock()
        
        if LOG_DB_CONFIG.get('create_tables', True):
            self._create_all_tables()
        
        self._save_interval = LOG_DB_CONFIG.get('insert_interval', DEFAULT_INSERT_INTERVAL)
        self._batch_size = LOG_DB_CONFIG.get('batch_size', DEFAULT_BATCH_SIZE)
        
        self._stop_event = threading.Event()
        self._save_thread = threading.Thread(
            target=self._periodic_save,
            daemon=True,
            name="LogDBSaveThread"
        )
        self._save_thread.start()
        
        if LOG_DB_CONFIG.get('auto_cleanup', False):
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_old_tables,
                daemon=True,
                name="LogDBCleanupThread"
            )
            self._cleanup_thread.start()
        
        # ID清理任务已集成到DAU分析调度器中

    def _safe_execute(self, operation, error_msg="操作失败"):
        """安全执行操作"""
        try:
            return operation()
        except Exception as e:
            logger.error(f"{error_msg}: {str(e)}")
            return None

    @contextmanager
    def _with_cursor(self, cursor_class=DictCursor):
        """数据库cursor上下文管理器"""
        connection = self.pool.get_connection()
        if not connection:
            raise Exception("无法获取数据库连接")
        
        cursor = None
        try:
            cursor = connection.cursor(cursor_class)
            yield cursor, connection
        finally:
            if cursor:
                cursor.close()
            self.pool.release_connection(connection)

    def _format_timestamp(self, dt=None):
        """格式化时间戳"""
        if dt is None:
            dt = datetime.datetime.now()
        return dt.strftime('%Y-%m-%d %H:%M:%S')

    def _get_table_name(self, log_type):
        """获取表名"""
        prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
        suffix = TABLE_SUFFIX.get(log_type, log_type)
        
        # DAU表和ID表不按日期分表，使用统一表名
        if log_type in ('dau', 'id'):
            return f"{prefix}{suffix}"
        
        if LOG_DB_CONFIG.get('table_per_day', True):
            today = datetime.datetime.now().strftime('%Y%m%d')
            return f"{prefix}{today}_{suffix}"
        else:
            return f"{prefix}{suffix}"

    def _table_exists(self, table_name, cursor):
        """检查表是否存在"""
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM information_schema.tables 
            WHERE table_schema = DATABASE() 
            AND table_name = %s
        """, (table_name,))
        result = cursor.fetchone()
        return result and result['count'] > 0

    def _get_create_table_sql(self, table_name, log_type):
        """获取创建表SQL"""
        if log_type in ('dau', 'id'):
            # DAU表和ID表特殊处理，不需要默认的id和timestamp字段
            base_sql = f"""
                CREATE TABLE IF NOT EXISTS `{table_name}` (
            """
        else:
            base_sql = f"""
                CREATE TABLE IF NOT EXISTS `{table_name}` (
                    `id` bigint(20) NOT NULL AUTO_INCREMENT,
                    `timestamp` datetime NOT NULL,
            """
        
        if log_type == 'received':
            specific_fields = """
                `user_id` varchar(255) NOT NULL COMMENT '用户ID',
                `group_id` varchar(255) DEFAULT 'c2c' COMMENT '群聊ID',
                `content` text NOT NULL COMMENT '消息内容',
                `raw_message` text COMMENT '原始消息数据',
            """
        elif log_type == 'unmatched':
            specific_fields = """
                `user_id` varchar(255) NOT NULL COMMENT '用户ID',
                `group_id` varchar(255) DEFAULT 'c2c' COMMENT '群聊ID',
                `content` text NOT NULL COMMENT '消息内容',
                `raw_message` text COMMENT '原始消息数据',
            """
        elif log_type == 'error':
            specific_fields = """
                `content` text NOT NULL,
                `traceback` text,
                `resp_obj` text COMMENT '响应对象',
                `send_payload` text COMMENT '发送载荷',
                `raw_message` text COMMENT '原始消息',
            """
        elif log_type == 'dau':
            specific_fields = """
                `date` date NOT NULL COMMENT '日期' PRIMARY KEY,
                `active_users` int(11) DEFAULT 0 COMMENT '活跃用户数',
                `active_groups` int(11) DEFAULT 0 COMMENT '活跃群聊数',
                `total_messages` int(11) DEFAULT 0 COMMENT '消息总数',
                `private_messages` int(11) DEFAULT 0 COMMENT '私聊消息总数',
                `group_join_count` int(11) DEFAULT 0 COMMENT '今日进群数',
                `group_leave_count` int(11) DEFAULT 0 COMMENT '今日退群数',
                `group_count_change` int(11) DEFAULT 0 COMMENT '群数量增减',
                `friend_add_count` int(11) DEFAULT 0 COMMENT '今日加好友数',
                `friend_remove_count` int(11) DEFAULT 0 COMMENT '今日删好友数',
                `friend_count_change` int(11) DEFAULT 0 COMMENT '好友数增减',
                `message_stats_detail` json COMMENT '详细消息统计数据(JSON)',
                `user_stats_detail` json COMMENT '详细用户统计数据(JSON)',
                `command_stats_detail` json COMMENT '详细命令统计数据(JSON)',
            """
        elif log_type == 'id':
            specific_fields = """
                `chat_type` varchar(10) NOT NULL COMMENT '聊天类型:group/user',
                `chat_id` varchar(255) NOT NULL COMMENT '聊天ID:群ID或用户ID',
                `last_message_id` varchar(255) NOT NULL COMMENT '最后一个消息ID',
                PRIMARY KEY (`chat_type`, `chat_id`),
            """
        else:
            specific_fields = """
                `content` text NOT NULL,
            """
        
        if log_type == 'dau':
            end_sql = """
                    `updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        elif log_type == 'id':
            end_sql = """
                    `timestamp` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间'
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        else:
            end_sql = """
                    `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (`id`)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        
        return base_sql + specific_fields + end_sql

    def _create_table(self, log_type):
        """创建日志表"""
        table_name = self._get_table_name(log_type)
        
        if table_name in self.tables_created:
            return True
        
        try:
            with self._with_cursor() as (cursor, connection):
                if self._table_exists(table_name, cursor):
                    self.tables_created.add(table_name)
                    return True
                
                create_table_sql = self._get_create_table_sql(table_name, log_type)
                cursor.execute(create_table_sql)
                
                # DAU表和ID表不创建时间索引，因为没有timestamp字段
                if log_type not in ('dau', 'id'):
                    cursor.execute(f"CREATE INDEX idx_{table_name}_time ON {table_name} (timestamp)")
                
                if log_type == 'unmatched':
                    cursor.execute(f"CREATE INDEX idx_{table_name}_user ON {table_name} (user_id)")
                    cursor.execute(f"CREATE INDEX idx_{table_name}_group ON {table_name} (group_id)")
                
                connection.commit()
                self.tables_created.add(table_name)
                return True
                
        except Exception as e:
            logger.error(f"创建表 {table_name} 失败: {str(e)}")
            return False
    
    def _create_all_tables(self):
        """创建所有日志表"""
        for log_type in LOG_TYPES:
            self._create_table(log_type)
    
    def add_log(self, log_type, log_data):
        """添加日志到队列"""
        if not LOG_DB_CONFIG.get('enabled', False):
            return False
            
        if log_type not in LOG_TYPES:
            logger.error(f"无效的日志类型: {log_type}")
            return False
            
        self.log_queues[log_type].put(log_data)
        
        if self._save_interval == 0:
            self._save_logs_to_db()
            
        return True
    
    def update_id_cache(self, chat_type, chat_id, message_id):
        """更新ID缓存"""
        if not message_id:
            return False
            
        with self.id_cache_lock:
            cache_key = (chat_type, chat_id)
            self.id_cache[cache_key] = message_id
        return True
    
    def _save_id_cache_to_db(self):
        """保存ID缓存到数据库"""
        if not self.id_cache:
            return
            
        with self.id_cache_lock:
            # 复制缓存并清空原缓存
            cache_to_save = self.id_cache.copy()
            self.id_cache.clear()
        
        if not cache_to_save:
            return
        
        # 转换为日志格式并加入队列
        for (chat_type, chat_id), message_id in cache_to_save.items():
            id_data = {
                'chat_type': chat_type,
                'chat_id': chat_id,
                'last_message_id': message_id
            }
            self.log_queues['id'].put(id_data)
        
        # 立即保存ID日志
        self._save_log_type_to_db('id')
    
    def _periodic_save(self):
        """定期保存日志"""
        while not self._stop_event.is_set():
            try:
                if self._save_interval > 0:
                    self._stop_event.wait(self._save_interval)
                    if self._stop_event.is_set():
                        break
                
                self._save_logs_to_db()
                # 保存ID缓存
                self._save_id_cache_to_db()
                
            except Exception as e:
                logger.error(f"定期保存失败: {str(e)}")
                time.sleep(5)
    
    def _save_logs_to_db(self):
        """保存日志到数据库"""
        for log_type in LOG_TYPES:
            self._save_log_type_to_db(log_type)

    def _build_insert_sql_and_values(self, log_type, table_name, logs):
        """构建插入SQL和数据"""
        if log_type == 'received':
            sql = f"""
                INSERT INTO `{table_name}` 
                (timestamp, user_id, group_id, content, raw_message) 
                VALUES (%s, %s, %s, %s, %s)
            """
            values = [(
                log.get('timestamp'),
                log.get('user_id', '未知用户'),
                log.get('group_id', 'c2c'),
                log.get('content'),
                log.get('raw_message', '')
            ) for log in logs]
            
        elif log_type == 'unmatched':
            sql = f"""
                INSERT INTO `{table_name}` 
                (timestamp, user_id, group_id, content, raw_message) 
                VALUES (%s, %s, %s, %s, %s)
            """
            values = [(
                log.get('timestamp'),
                log.get('user_id', '未知用户'),
                log.get('group_id', 'c2c'),
                log.get('content'),
                log.get('raw_message', '')
            ) for log in logs]
            
        elif log_type == 'error':
            sql = f"""
                INSERT INTO `{table_name}` 
                (timestamp, content, traceback, resp_obj, send_payload, raw_message) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            values = [(
                log.get('timestamp'),
                log.get('content'),
                log.get('traceback', ''),
                log.get('resp_obj', ''),
                log.get('send_payload', ''),
                log.get('raw_message', '')
            ) for log in logs]
        elif log_type == 'dau':
            sql = f"""
                INSERT INTO `{table_name}` 
                (`date`, `active_users`, `active_groups`, `total_messages`, `private_messages`, 
                 `group_join_count`, `group_leave_count`, `group_count_change`, 
                 `friend_add_count`, `friend_remove_count`, `friend_count_change`,
                 `message_stats_detail`, `user_stats_detail`, `command_stats_detail`) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                `active_users` = VALUES(`active_users`),
                `active_groups` = VALUES(`active_groups`),
                `total_messages` = VALUES(`total_messages`),
                `private_messages` = VALUES(`private_messages`),
                `group_join_count` = `group_join_count` + VALUES(`group_join_count`),
                `group_leave_count` = `group_leave_count` + VALUES(`group_leave_count`),
                `group_count_change` = `group_count_change` + VALUES(`group_count_change`),
                `friend_add_count` = `friend_add_count` + VALUES(`friend_add_count`),
                `friend_remove_count` = `friend_remove_count` + VALUES(`friend_remove_count`),
                `friend_count_change` = `friend_count_change` + VALUES(`friend_count_change`),
                `message_stats_detail` = VALUES(`message_stats_detail`),
                `user_stats_detail` = VALUES(`user_stats_detail`),
                `command_stats_detail` = VALUES(`command_stats_detail`)
            """
            values = [(
                log.get('date'),
                log.get('active_users', 0),
                log.get('active_groups', 0),
                log.get('total_messages', 0),
                log.get('private_messages', 0),
                log.get('group_join_count', 0),
                log.get('group_leave_count', 0),
                log.get('group_count_change', 0),
                log.get('friend_add_count', 0),
                log.get('friend_remove_count', 0),
                log.get('friend_count_change', 0),
                json.dumps(log.get('message_stats_detail', {}), ensure_ascii=False) if log.get('message_stats_detail') else None,
                json.dumps(log.get('user_stats_detail', {}), ensure_ascii=False) if log.get('user_stats_detail') else None,
                json.dumps(log.get('command_stats_detail', []), ensure_ascii=False) if log.get('command_stats_detail') else None
            ) for log in logs]
        elif log_type == 'id':
            sql = f"""
                INSERT INTO `{table_name}` 
                (`chat_type`, `chat_id`, `last_message_id`) 
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                `last_message_id` = VALUES(`last_message_id`),
                `timestamp` = CURRENT_TIMESTAMP
            """
            values = [(
                log.get('chat_type'),
                log.get('chat_id'),
                log.get('last_message_id')
            ) for log in logs]
        else:
            sql = f"""
                INSERT INTO `{table_name}` 
                (timestamp, content) 
                VALUES (%s, %s)
            """
            values = [(
                log.get('timestamp'),
                log.get('content')
            ) for log in logs]
        
        return sql, values
    
    def _save_log_type_to_db(self, log_type):
        """保存特定类型日志"""
        queue_size = self.log_queues[log_type].qsize()
        if queue_size == 0:
            return
            
        batch_size = min(queue_size, self._batch_size)
        
        if not self._create_table(log_type):
            logger.error(f"无法保存{log_type}日志: 表创建失败")
            return
            
        table_name = self._get_table_name(log_type)
        logs_to_insert = []
        
        # 收集日志数据
        for _ in range(batch_size):
            try:
                log_data = self.log_queues[log_type].get_nowait()
                logs_to_insert.append(log_data)
            except queue.Empty:
                break
        
        if not logs_to_insert:
            return
            
        try:
            with self._with_cursor(cursor_class=None) as (cursor, connection):
                sql, values = self._build_insert_sql_and_values(log_type, table_name, logs_to_insert)
                cursor.executemany(sql, values)
                connection.commit()
                
        except Exception as e:
            logger.error(f"保存{log_type}日志失败: {str(e)}")
            
            if LOG_DB_CONFIG.get('fallback_to_file', True):
                self._fallback_to_file(log_type, logs_to_insert)
                
        finally:
            for _ in range(len(logs_to_insert)):
                self.log_queues[log_type].task_done()
    
    def _fallback_to_file(self, log_type, logs):
        """回退到文件记录"""
        try:
            fallback_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
            os.makedirs(fallback_dir, exist_ok=True)
                
            today = datetime.datetime.now().strftime('%Y-%m-%d')
            fallback_file = os.path.join(fallback_dir, f"{log_type}_{today}.log")
            
            with open(fallback_file, 'a', encoding='utf-8') as f:
                f.write(f"\n--- 数据库写入失败，回退记录 {self._format_timestamp()} ---\n")
                for log in logs:
                    timestamp = log.get('timestamp', '')
                    content = log.get('content', '')
                    f.write(f"[{timestamp}] {content}\n")
                    if log_type == 'error':
                        if 'traceback' in log and log['traceback']:
                            f.write(f"调用栈信息:\n{log['traceback']}\n")
                        if 'resp_obj' in log and log['resp_obj']:
                            f.write(f"响应对象:\n{log['resp_obj']}\n")
                        if 'send_payload' in log and log['send_payload']:
                            f.write(f"发送载荷:\n{log['send_payload']}\n")
                        if 'raw_message' in log and log['raw_message']:
                            f.write(f"原始消息:\n{log['raw_message']}\n")
                    elif log_type == 'received':
                        user_id = log.get('user_id', '未知用户')
                        group_id = log.get('group_id', 'c2c')
                        f.write(f"用户ID: {user_id}, 群聊ID: {group_id}\n")
                        if 'raw_message' in log and log['raw_message']:
                            f.write(f"原始消息: {log['raw_message']}\n")
                    elif log_type == 'unmatched':
                        user_id = log.get('user_id', '未知用户')
                        group_id = log.get('group_id', 'c2c')
                        f.write(f"用户ID: {user_id}, 群聊ID: {group_id}\n")
                        if 'raw_message' in log and log['raw_message']:
                            f.write(f"原始消息: {log['raw_message']}\n")
            
        except Exception as e:
            logger.error(f"回退到文件保存失败: {str(e)}")
    
    def _cleanup_old_tables(self):
        """清理过期表"""
        if not LOG_DB_CONFIG.get('retention_days', 0) > 0:
            return
            
        while not self._stop_event.is_set():
            time.sleep(24 * 60 * 60)
            
            if self._stop_event.is_set():
                break
                
            retention_days = LOG_DB_CONFIG.get('retention_days', 90)
            
            try:
                with self._with_cursor() as (cursor, connection):
                    prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
                    cursor.execute("""
                        SELECT table_name 
                        FROM information_schema.tables 
                        WHERE table_schema = DATABASE() 
                        AND table_name LIKE %s
                    """, (f"{prefix}%",))
                    
                    tables = cursor.fetchall()
                    
                    for table in tables:
                        table_name = table['table_name']
                        parts = table_name.split('_')
                        for part in parts:
                            if len(part) == 8 and part.isdigit():
                                table_date = datetime.datetime.strptime(part, '%Y%m%d')
                                if (datetime.datetime.now() - table_date).days > retention_days:
                                    cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
                                break
                    
                    connection.commit()
                    
            except Exception as e:
                logger.error(f"清理过期表失败: {str(e)}")

    def _cleanup_yesterday_ids(self):
        """清理昨天的ID记录"""
        try:
            with self._with_cursor() as (cursor, connection):
                table_name = self._get_table_name('id')
                yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
                
                # 删除昨天的记录（timestamp字段是DATE类型时的比较）
                cursor.execute(f"""
                    DELETE FROM `{table_name}` 
                    WHERE DATE(`timestamp`) = %s
                """, (yesterday,))
                
                deleted_count = cursor.rowcount
                connection.commit()
                
                if deleted_count > 0:
                    logger.info(f"已清理昨天({yesterday})的ID记录: {deleted_count}条")
                    
        except Exception as e:
            logger.error(f"清理昨天ID记录失败: {str(e)}")

    def shutdown(self):
        """关闭管理器"""
        self._stop_event.set()
        self._save_logs_to_db()
        # 保存剩余的ID缓存
        self._save_id_cache_to_db()
        
        if hasattr(self, '_save_thread') and self._save_thread.is_alive():
            self._save_thread.join(timeout=5)
        
        if hasattr(self, '_cleanup_thread') and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5)

# 全局单例
log_db_manager = LogDatabaseManager() if LOG_DB_CONFIG.get('enabled', False) else None

def add_log_to_db(log_type, log_data):
    """添加日志到数据库"""
    if log_db_manager:
        if not isinstance(log_data, dict):
            logger.error("日志数据必须是字典类型")
            return False
        
        if log_type == 'dau':
            # DAU类型只需要date字段
            if 'date' not in log_data:
                log_data['date'] = datetime.datetime.now().strftime('%Y-%m-%d')
        else:
            if 'timestamp' not in log_data:
                log_data['timestamp'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            if 'content' not in log_data:
                logger.error("日志数据必须包含'content'字段")
                return False
            
        return log_db_manager.add_log(log_type, log_data)
    return False


def add_dau_event_to_db(event_type, count=1, date=None):
    """添加DAU事件到数据库
    
    Args:
        event_type: 事件类型 'group_join', 'group_leave', 'friend_add', 'friend_remove'
        count: 增量计数，默认为1
        date: 日期，默认为今天
    """
    if not log_db_manager:
        return False
        
    if date is None:
        date = datetime.datetime.now().strftime('%Y-%m-%d')
    
    dau_data = {
        'date': date,
        'active_users': 0,
        'active_groups': 0,
        'total_messages': 0,
        'private_messages': 0,
        'group_join_count': count if event_type == 'group_join' else 0,
        'group_leave_count': count if event_type == 'group_leave' else 0,
        'group_count_change': count if event_type == 'group_join' else (-count if event_type == 'group_leave' else 0),
        'friend_add_count': count if event_type == 'friend_add' else 0,
        'friend_remove_count': count if event_type == 'friend_remove' else 0,
        'friend_count_change': count if event_type == 'friend_add' else (-count if event_type == 'friend_remove' else 0)
    }
    
    return add_log_to_db('dau', dau_data)


def save_daily_dau_data(date, active_users, active_groups, total_messages, private_messages, 
                       message_stats_detail=None, user_stats_detail=None, command_stats_detail=None):
    """保存每日DAU统计数据
    
    Args:
        date: 日期
        active_users: 活跃用户数
        active_groups: 活跃群聊数
        total_messages: 消息总数
        private_messages: 私聊消息总数
        message_stats_detail: 详细消息统计数据(dict)
        user_stats_detail: 详细用户统计数据(dict)
        command_stats_detail: 详细命令统计数据(list)
    """
    if not log_db_manager:
        return False
    
    dau_data = {
        'date': date,
        'active_users': active_users,
        'active_groups': active_groups,
        'total_messages': total_messages,
        'private_messages': private_messages,
        'group_join_count': 0,
        'group_leave_count': 0,
        'group_count_change': 0,
        'friend_add_count': 0,
        'friend_remove_count': 0,
        'friend_count_change': 0,
        'message_stats_detail': message_stats_detail,
        'user_stats_detail': user_stats_detail,
        'command_stats_detail': command_stats_detail
    }
    
    return add_log_to_db('dau', dau_data)


def save_complete_dau_data(dau_data_dict):
    """保存完整的DAU数据（包含所有详细信息）
    
    Args:
        dau_data_dict: 完整的DAU数据字典
    """
    if not log_db_manager:
        return False
    
    # 从完整数据中提取基础统计
    message_stats = dau_data_dict.get('message_stats', {})
    user_stats = dau_data_dict.get('user_stats', {})
    command_stats = dau_data_dict.get('command_stats', [])
    
    date = dau_data_dict.get('date')
    active_users = message_stats.get('active_users', 0)
    active_groups = message_stats.get('active_groups', 0)
    total_messages = message_stats.get('total_messages', 0)
    private_messages = message_stats.get('private_messages', 0)
    
    # 构建数据库记录
    db_data = {
        'date': date,
        'active_users': active_users,
        'active_groups': active_groups,
        'total_messages': total_messages,
        'private_messages': private_messages,
        'group_join_count': 0,  # 事件统计数据会通过add_dau_event_to_db累加
        'group_leave_count': 0,
        'group_count_change': 0,
        'friend_add_count': 0,
        'friend_remove_count': 0,
        'friend_count_change': 0,
        'message_stats_detail': message_stats,
        'user_stats_detail': user_stats,
        'command_stats_detail': command_stats
    }
    
    return add_log_to_db('dau', db_data)


def record_last_message_id(chat_type, chat_id, message_id):
    """记录最后一个消息ID到缓存（定期批量保存）
    
    Args:
        chat_type: 聊天类型，'group' 或 'user'
        chat_id: 聊天ID，群ID或用户ID
        message_id: 消息ID
    """
    if not log_db_manager:
        return False
    
    return log_db_manager.update_id_cache(chat_type, chat_id, message_id)


def cleanup_yesterday_ids():
    """手动清理昨天的ID记录
    
    Returns:
        bool: 清理是否成功
    """
    if not log_db_manager:
        return False
    
    log_db_manager._cleanup_yesterday_ids()
    return True