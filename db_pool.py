#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pymysql
from pymysql.cursors import DictCursor
from config import DB_CONFIG
import threading
import time
import gc  # 垃圾回收模块
import weakref  # 弱引用管理
import logging  # 添加日志记录

# 配置基本日志
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('db_pool')

class DatabasePool:
    _instance = None
    _lock = threading.Lock()
    _pool = []
    _max_connections = 5  # 降低最大连接数，减轻数据库负担
    _min_connections = 2   # 保持最小连接数
    _timeout = 15          # 进一步减少连接超时时间
    _busy_connections = {}  # 正在使用的连接
    _initialized = False
    _last_gc_time = 0      # 上次垃圾回收时间
    _gc_interval = 120     # 更频繁地执行垃圾回收(秒)
    _connection_lifetime = 1800  # 连接最长生命周期(30分钟)
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DatabasePool, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        # 单例模式下只初始化一次
        with self._lock:
            if not self._initialized:
                self._init_pool()
                # 启动维护线程
                maintenance_thread = threading.Thread(target=self._maintain_pool, daemon=True)
                maintenance_thread.start()
                
                self._initialized = True
    
    def _init_pool(self):
        """初始化连接池"""
        try:
            # 创建初始连接
            for _ in range(self._min_connections):
                connection = self._create_connection()
                if connection:
                    self._pool.append({
                        'connection': connection,
                        'created_at': time.time(),
                        'last_used': time.time()
                    })
            logger.info(f"数据库连接池初始化成功，创建了{len(self._pool)}个连接")
        except Exception as e:
            logger.error(f"初始化数据库连接池失败: {str(e)}")
    
    def _create_connection(self):
        """创建新的数据库连接"""
        retry_count = 3
        retry_delay = 1
        last_error = None
        
        for i in range(retry_count):
            try:
                connection = pymysql.connect(
                    host=DB_CONFIG.get('host', 'localhost'),
                    port=DB_CONFIG.get('port', 3306),
                    user=DB_CONFIG.get('user', 'root'),
                    password=DB_CONFIG.get('password', ''),
                    database=DB_CONFIG.get('database', ''),
                    charset=DB_CONFIG.get('charset', 'utf8mb4'),
                    cursorclass=DictCursor,
                    connect_timeout=10,
                    autocommit=True  # 自动提交模式避免事务泄漏
                )
                return connection
            except Exception as e:
                last_error = e
                if i < retry_count - 1:
                    logger.warning(f"创建数据库连接失败(尝试 {i+1}/{retry_count}): {str(e)}")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # 指数退避
        
        logger.error(f"创建数据库连接失败，已达到最大重试次数: {str(last_error)}")
        return None
    
    def _check_connection(self, connection):
        """检查连接是否有效"""
        try:
            connection.ping(reconnect=True)
            return True
        except Exception:
            return False
    
    def get_connection(self):
        """获取数据库连接"""
        connection_id = threading.get_ident()
        
        # 如果当前线程已经有连接，直接返回
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        
        max_wait_time = 3  # 最长等待时间(秒)
        wait_start = time.time()
        
        while time.time() - wait_start < max_wait_time:
            with self._lock:
                # 尝试从池中获取连接
                if self._pool:
                    conn_info = self._pool.pop(0)
                    connection = conn_info['connection']
                    
                    # 检查连接是否有效，无效则创建新连接
                    if not self._check_connection(connection):
                        try:
                            connection.close()
                        except:
                            pass
                        connection = self._create_connection()
                        if not connection:
                            continue  # 尝试下一个连接
                    
                    # 检查连接是否超过最大生命周期，超过则创建新连接
                    if time.time() - conn_info['created_at'] > self._connection_lifetime:
                        try:
                            connection.close()
                        except:
                            pass
                        connection = self._create_connection()
                        if not connection:
                            continue  # 尝试下一个连接
                    
                    # 标记为忙碌状态
                    self._busy_connections[connection_id] = {
                        'connection': connection,
                        'acquired_at': time.time()
                    }
                    return connection
                
                # 如果池中没有连接但未达到最大连接数，创建新连接
                if len(self._busy_connections) < self._max_connections:
                    connection = self._create_connection()
                    if connection:
                        self._busy_connections[connection_id] = {
                            'connection': connection,
                            'acquired_at': time.time()
                        }
                        return connection
            
            # 达到最大连接数或创建连接失败，等待50ms后重试
            time.sleep(0.05)
            
            # 检查是否有长时间未释放的连接，强制释放
            self._check_and_cleanup_dead_connections()
        
        logger.warning(f"无法获取数据库连接，等待超时({max_wait_time}秒)")
        return None
    
    def _check_and_cleanup_dead_connections(self):
        """检查并清理可能死锁的连接"""
        current_time = time.time()
        with self._lock:
            for conn_id in list(self._busy_connections.keys()):
                conn_info = self._busy_connections[conn_id]
                # 连接使用超过30秒视为可能死锁
                if current_time - conn_info['acquired_at'] > 30:
                    logger.warning(f"警告: 强制释放可能死锁的连接ID {conn_id}，使用时间: {int(current_time - conn_info['acquired_at'])}秒")
                    try:
                        conn_info['connection'].close()
                    except:
                        pass
                    del self._busy_connections[conn_id]
    
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
                        try:
                            connection.close()
                        except:
                            pass
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
                    # 检查连接是否超过最大生命周期
                    usage_time = time.time() - conn_info.get('acquired_at', time.time())
                    created_time = time.time() - conn_info.get('created_at', time.time())
                    
                    # 连接已使用太久 或 总生命周期过长，直接关闭
                    if usage_time > 300 or created_time > self._connection_lifetime:
                        try:
                            connection.close()
                        except:
                            pass
                        return
                    
                    # 只有当池中连接数少于最大值时才放回
                    if len(self._pool) < self._max_connections:
                        self._pool.append({
                            'connection': connection,
                            'created_at': conn_info.get('created_at', time.time()),
                            'last_used': time.time()
                        })
                    else:
                        # 池已满，关闭连接
                        try:
                            connection.close()
                        except:
                            pass
                else:
                    try:
                        connection.close()
                    except:
                        pass
            except Exception as e:
                logger.error(f"释放连接过程中出错: {str(e)}")
    
    def _maintain_pool(self):
        """维护连接池，定期检查并处理过期连接"""
        while True:
            time.sleep(20)  # 每20秒检查一次
            
            try:
                with self._lock:
                    current_time = time.time()
                    
                    # 检查并清理池中过期的连接
                    for i in range(len(self._pool) - 1, -1, -1):
                        if i >= len(self._pool):
                            continue  # 防止索引越界
                            
                        conn_info = self._pool[i]
                        # 超过超时时间且池中连接数大于最小值，关闭连接
                        if (current_time - conn_info['last_used'] > self._timeout and 
                            len(self._pool) > self._min_connections):
                            try:
                                conn_info['connection'].close()
                            except Exception as e:
                                logger.debug(f"关闭过期连接失败: {str(e)}")
                            try:
                                del self._pool[i]
                            except IndexError:
                                pass  # 防止并发修改导致的问题
                        
                        # 检查连接是否超过最大生命周期
                        elif current_time - conn_info['created_at'] > self._connection_lifetime:
                            try:
                                conn_info['connection'].close()
                            except Exception as e:
                                logger.debug(f"关闭过期连接失败: {str(e)}")
                            try:
                                del self._pool[i]
                            except IndexError:
                                pass
                    
                    # 确保保持最小连接数
                    try:
                        while len(self._pool) < self._min_connections:
                            conn = self._create_connection()
                            if conn:
                                self._pool.append({
                                    'connection': conn,
                                    'created_at': time.time(),
                                    'last_used': time.time()
                                })
                            else:
                                break  # 无法创建连接时退出循环
                    except Exception as e:
                        logger.error(f"维护最小连接数失败: {str(e)}")
                    
                    # 检查并处理长时间未释放的连接
                    for conn_id in list(self._busy_connections.keys()):
                        conn_info = self._busy_connections[conn_id]
                        # 连接使用超过2分钟视为异常
                        if current_time - conn_info['acquired_at'] > 120:
                            logger.warning(f"警告: 连接ID {conn_id} 已使用超过2分钟，可能存在未释放的情况")
                    
                    # 定期执行垃圾回收
                    if current_time - self._last_gc_time > self._gc_interval:
                        collected = gc.collect()
                        self._last_gc_time = current_time
                        logger.info(f"执行垃圾回收完成，回收了 {collected} 个对象")
                        
                    # 定期记录连接池状态
                    logger.info(f"连接池状态：空闲 {len(self._pool)}，忙碌 {len(self._busy_connections)}")
            except Exception as e:
                logger.error(f"连接池维护过程中发生错误: {str(e)}")

class ConnectionManager:
    """数据库连接管理器，提供便捷的上下文管理接口"""
    
    def __init__(self):
        self.pool = DatabasePool()
        self.connection = None
        self.cursor = None
    
    def __enter__(self):
        # 尝试获取连接，如果立即获取不到则进行有限次数重试
        retry_count = 3
        retry_interval = 1  # 重试间隔(秒)
        
        for i in range(retry_count):
            self.connection = self.pool.get_connection()
            if self.connection:
                try:
                    self.cursor = self.connection.cursor()
                    return self
                except Exception as e:
                    logger.error(f"创建游标失败: {str(e)}")
                    self.pool.release_connection(self.connection)
                    self.connection = None
                    
            # 如果这不是最后一次尝试，则等待后重试
            if i < retry_count - 1:
                logger.warning(f"尝试获取数据库连接失败，{retry_interval}秒后重试 ({i+1}/{retry_count})")
                time.sleep(retry_interval)
                retry_interval *= 2  # 指数退避
        
        logger.error("无法获取数据库连接，已达到最大重试次数")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.cursor:
                self.cursor.close()
                self.cursor = None
        except Exception as e:
            logger.error(f"关闭游标失败: {str(e)}")
        
        try:
            if self.connection:
                # 如果出现异常，回滚事务
                if exc_type:
                    try:
                        self.connection.rollback()
                    except:
                        pass
                self.pool.release_connection(self.connection)
                self.connection = None
        except Exception as e:
            logger.error(f"释放连接失败: {str(e)}")
    
    def execute(self, sql, params=None):
        """执行SQL语句"""
        if not self.cursor:
            return False
        
        try:
            self.cursor.execute(sql, params)
            return True
        except Exception as e:
            logger.error(f"执行SQL失败: {str(e)}")
            return False
    
    def fetchone(self):
        """获取一行结果"""
        if not self.cursor:
            return None
        return self.cursor.fetchone()
    
    def fetchall(self):
        """获取所有结果"""
        if not self.cursor:
            return []
        return self.cursor.fetchall()
    
    def commit(self):
        """提交事务"""
        if self.connection:
            try:
                self.connection.commit()
                return True
            except Exception as e:
                logger.error(f"提交事务失败: {str(e)}")
                return False
        return False
    
    def rollback(self):
        """回滚事务"""
        if self.connection:
            try:
                self.connection.rollback()
                return True
            except Exception as e:
                logger.error(f"回滚事务失败: {str(e)}")
                return False
        return False


# 全局单例
db_pool = DatabasePool()

# 简化的数据库操作函数
def execute_query(sql, params=None, fetchall=False):
    """执行查询SQL并返回结果"""
    with ConnectionManager() as manager:
        if manager.execute(sql, params):
            if fetchall:
                return manager.fetchall()
            return manager.fetchone()
    return None if not fetchall else []

def execute_update(sql, params=None):
    """执行更新SQL并提交"""
    with ConnectionManager() as manager:
        if manager.execute(sql, params):
            return manager.commit()
    return False

def execute_transaction(sql_list):
    """执行事务，包含多条SQL语句"""
    if not sql_list:  # 避免空事务
        return True
        
    with ConnectionManager() as manager:
        try:
            for sql_item in sql_list:
                sql = sql_item.get('sql')
                params = sql_item.get('params')
                if not sql:  # 跳过无效的SQL
                    continue
                    
                if not manager.execute(sql, params):
                    manager.rollback()
                    return False
            return manager.commit()
        except Exception as e:
            try:
                manager.rollback()
            except:
                pass
            logger.error(f"事务执行失败: {str(e)}")
            return False

# 初始化数据库表结构的函数
def init_db_tables():
    """初始化数据库表结构，确保所需表存在"""
    # 使用事务创建表，确保原子性
    tables_sql = []
    
    # 创建doro_records表
    tables_sql.append({
        'sql': """
            CREATE TABLE IF NOT EXISTS doro_records (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(100) NOT NULL,
                date DATE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX (user_id, date)
            )
        """
    })
    
    # 创建tklj表 (图库链接记录)
    tables_sql.append({
        'sql': """
            CREATE TABLE IF NOT EXISTS tklj (
                id INT AUTO_INCREMENT PRIMARY KEY,
                originalUrl VARCHAR(255) NOT NULL,
                newUrl VARCHAR(255) NOT NULL,
                px VARCHAR(100) NOT NULL,
                domainAfterLastDash VARCHAR(100),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX (originalUrl(191))
            )
        """
    })
    
    # 创建tplj表 (塔罗牌链接记录)
    tables_sql.append({
        'sql': """
            CREATE TABLE IF NOT EXISTS tplj (
                id INT AUTO_INCREMENT PRIMARY KEY,
                originalUrl VARCHAR(255) NOT NULL,
                newUrl VARCHAR(255) NOT NULL,
                px VARCHAR(100) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX (originalUrl(191))
            )
        """
    })
    
    # 执行表创建事务
    return execute_transaction(tables_sql)

class DatabaseService:
    """数据库服务类，为插件提供数据库操作接口"""
    
    @staticmethod
    def execute_query(sql, params=None, fetch_all=False):
        """
        执行查询SQL并返回结果
        
        参数:
            sql (str): SQL查询语句
            params (tuple|dict): 参数化查询的参数
            fetch_all (bool): 是否返回所有结果行，默认False只返回第一行
            
        返回:
            dict|list|None: 查询结果，可能是单行(dict)、多行(list of dict)或None(无结果)
        """
        return execute_query(sql, params, fetch_all)
    
    @staticmethod
    def execute_update(sql, params=None):
        """
        执行更新SQL并提交(INSERT, UPDATE, DELETE等)
        
        参数:
            sql (str): SQL更新语句
            params (tuple|dict): 参数化查询的参数
            
        返回:
            bool: 操作是否成功
        """
        return execute_update(sql, params)
    
    @staticmethod
    def execute_transaction(operations):
        """
        执行事务，包含多条SQL语句
        
        参数:
            operations (list): SQL操作列表，每项为包含'sql'和'params'的字典
                例如: [
                    {'sql': 'INSERT INTO table_name(col1) VALUES(%s)', 'params': ('value1',)},
                    {'sql': 'UPDATE table_name SET col1=%s WHERE col2=%s', 'params': ('new_value', 'condition')}
                ]
                
        返回:
            bool: 事务是否成功完成
        """
        return execute_transaction(operations)
    
    @staticmethod
    def check_daily_usage(user_id, table_name, limit=3):
        """
        检查用户每日使用次数，常用于限制功能使用频率
        
        参数:
            user_id (str): 用户ID
            table_name (str): 记录表名称
            limit (int): 每日使用次数限制
            
        返回:
            dict: {
                'can_use': True|False,  # 是否可以使用
                'count': int,           # 今日已使用次数
                'remaining': int        # 剩余可用次数
            }
        """
        # 检查记录表是否存在该用户今日的记录
        sql = f"""
            SELECT COUNT(*) as count 
            FROM {table_name} 
            WHERE user_id = %s 
            AND date = CURDATE()
        """
        result = execute_query(sql, (user_id,))
        
        if not result:
            count = 0
        else:
            count = result.get('count', 0)
        
        remaining = max(0, limit - count)
        can_use = remaining > 0
        
        return {
            'can_use': can_use,
            'count': count,
            'remaining': remaining
        }
    
    @staticmethod
    def add_usage_record(user_id, table_name):
        """
        添加用户功能使用记录
        
        参数:
            user_id (str): 用户ID
            table_name (str): 记录表名称
            
        返回:
            bool: 操作是否成功
        """
        sql = f"""
            INSERT INTO {table_name} (user_id, date)
            VALUES (%s, CURDATE())
        """
        return execute_update(sql, (user_id,))
    
    @staticmethod
    def find_image_url(original_url, table_name):
        """
        查找图片URL的映射关系
        
        参数:
            original_url (str): 原始URL
            table_name (str): 存储映射关系的表名
            
        返回:
            dict|None: 包含映射信息的字典或None(未找到)
        """
        if not original_url:  # 避免空URL查询
            return None
            
        sql = f"""
            SELECT * FROM {table_name}
            WHERE originalUrl = %s
            LIMIT 1
        """
        return execute_query(sql, (original_url,))
    
    @staticmethod
    def save_image_url(original_url, new_url, px, domain=None, table_name="tklj"):
        """
        保存图片URL的映射关系
        
        参数:
            original_url (str): 原始URL
            new_url (str): 新URL
            px (str): 图片尺寸
            domain (str, optional): 域名信息
            table_name (str): 存储映射关系的表名
            
        返回:
            bool: 操作是否成功
        """
        if not original_url or not new_url:  # 避免无效数据
            return False
            
        # 先检查是否已存在记录，避免重复插入
        existing = DatabaseService.find_image_url(original_url, table_name)
        if existing:
            return True  # 已存在记录，视为成功
            
        if table_name == "tklj" and domain is not None:
            sql = """
                INSERT INTO tklj (originalUrl, newUrl, px, domainAfterLastDash)
                VALUES (%s, %s, %s, %s)
            """
            return execute_update(sql, (original_url, new_url, px, domain))
        else:
            sql = f"""
                INSERT INTO {table_name} (originalUrl, newUrl, px)
                VALUES (%s, %s, %s)
            """
            return execute_update(sql, (original_url, new_url, px)) 