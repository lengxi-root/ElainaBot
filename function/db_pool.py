#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pymysql
from pymysql.cursors import DictCursor
from config import DB_CONFIG
import threading
import time
import gc
import logging
import queue
import concurrent.futures

logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('db_pool')

try:
    from web.app import add_framework_log
except ImportError:
    def add_framework_log(msg):
        pass

# 连接池配置 - 必须从config.py获取，不允许默认值
POOL_CONFIG = {
    'max_connections': DB_CONFIG['pool_size'],
    'min_connections': DB_CONFIG['min_pool_size'],
    'connection_timeout': DB_CONFIG['connect_timeout'],
    'read_timeout': DB_CONFIG['read_timeout'],
    'write_timeout': DB_CONFIG['write_timeout'],
    'connection_lifetime': DB_CONFIG['connection_lifetime'],
    'gc_interval': DB_CONFIG['gc_interval'],
    'idle_timeout': DB_CONFIG['idle_timeout'],
    'thread_pool_size': DB_CONFIG['thread_pool_size'],
    'request_timeout': DB_CONFIG['request_timeout'],
    'retry_count': DB_CONFIG['retry_count'],
    'retry_interval': DB_CONFIG['retry_interval']
}

class DatabasePool:
    _instance = None
    _lock = threading.Lock()
    _pool = []
    _busy_connections = {}
    _initialized = False
    _last_gc_time = 0
    _connection_requests = queue.Queue()
    _thread_pool = None
    
    _max_connections = POOL_CONFIG['max_connections']
    _min_connections = POOL_CONFIG['min_connections']
    _timeout = POOL_CONFIG['idle_timeout']
    _connection_lifetime = POOL_CONFIG['connection_lifetime']
    _gc_interval = POOL_CONFIG['gc_interval']
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DatabasePool, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        with self._lock:
            if not self._initialized:
                self._thread_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=POOL_CONFIG['thread_pool_size'],
                    thread_name_prefix="DBPool"
                )
                
                self._conn_request_thread = threading.Thread(
                    target=self._process_connection_requests,
                    daemon=True,
                    name="DBConnRequestProcessor"
                )
                self._conn_request_thread.start()
                
                self._init_pool()
                maintenance_thread = threading.Thread(
                    target=self._maintain_pool,
                    daemon=True,
                    name="DBPoolMaintenance"
                )
                maintenance_thread.start()
                
                self._initialized = True

    def _safe_execute(self, func, error_msg, *args, **kwargs):
        """统一的安全执行和错误处理"""
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"{error_msg}: {str(e)}")
            return None

    def _retry_with_backoff(self, func, max_retries=None, base_delay=None, error_msg="操作"):
        """统一的重试逻辑，支持指数退避"""
        if max_retries is None:
            max_retries = POOL_CONFIG['retry_count']
        if base_delay is None:
            base_delay = POOL_CONFIG['retry_interval']
        
        last_error = None
        delay = base_delay
        
        for i in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_error = e
                if i < max_retries - 1:
                    logger.warning(f"{error_msg}失败(尝试 {i+1}/{max_retries}): {str(e)}")
                    time.sleep(delay)
                    delay *= 2  # 指数退避
        
        logger.error(f"{error_msg}失败，已达到最大重试次数: {str(last_error)}")
        return None

    def _check_connection_health(self, connection, created_at=None):
        """统一的连接健康检查"""
        try:
            # 检查连接是否有效
            connection.ping(reconnect=True)
            
            # 检查连接生命周期
            if created_at and time.time() - created_at > self._connection_lifetime:
                return False
                
            return True
        except Exception:
            return False

    def _close_connection_safely(self, connection):
        """安全关闭连接"""
        try:
            connection.close()
        except Exception:
            pass

    def _create_connection_info(self, connection):
        """创建连接信息字典"""
        current_time = time.time()
        return {
            'connection': connection,
            'created_at': current_time,
            'last_used': current_time
        }

    def _init_pool(self):
        """初始化连接池"""
        try:
            successful_connections = 0
            for _ in range(self._min_connections):
                connection = self._create_connection()
                if connection:
                    successful_connections += 1
                    self._pool.append(self._create_connection_info(connection))
        except Exception as e:
            error_msg = f"初始化数据库连接池失败: {str(e)}"
            logger.error(error_msg)
            add_framework_log(f"数据库连接池错误：{error_msg}")
        
        if successful_connections > 0:
            add_framework_log("数据库连接池初始化完成")
    
    def _create_connection(self):
        """创建新的数据库连接"""
        def create_func():
            return pymysql.connect(
                host=DB_CONFIG.get('host', 'localhost'),
                port=DB_CONFIG.get('port', 3306),
                user=DB_CONFIG.get('user', 'root'),
                password=DB_CONFIG.get('password', ''),
                database=DB_CONFIG.get('database', ''),
                charset=DB_CONFIG.get('charset', 'utf8mb4'),
                cursorclass=DictCursor,
                connect_timeout=POOL_CONFIG['connection_timeout'],
                read_timeout=POOL_CONFIG['read_timeout'],
                write_timeout=POOL_CONFIG['write_timeout'],
                autocommit=DB_CONFIG.get('autocommit', True)
            )
        
        return self._retry_with_backoff(create_func, error_msg="创建数据库连接")
    
    def _process_connection_requests(self):
        """处理连接请求队列"""
        while True:
            try:
                request = self._connection_requests.get()
                if request is None:
                    break
                    
                future, timeout = request
                
                try:
                    connection = self._get_connection_internal(timeout)
                    future.set_result(connection)
                except Exception as e:
                    future.set_exception(e)
                    
            except Exception as e:
                logger.error(f"处理连接请求时出错: {str(e)}")
            finally:
                try:
                    self._connection_requests.task_done()
                except:
                    pass
    
    def get_connection(self):
        """获取数据库连接"""
        connection_id = threading.get_ident()
        
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        
        return self._get_connection_internal()
    
    def get_connection_async(self, timeout=None):
        """获取数据库连接 - 异步版本"""
        if timeout is None:
            timeout = POOL_CONFIG['request_timeout']
            
        connection_id = threading.get_ident()
        
        if connection_id in self._busy_connections:
            future = concurrent.futures.Future()
            future.set_result(self._busy_connections[connection_id]['connection'])
            return future
        
        future = concurrent.futures.Future()
        self._connection_requests.put((future, timeout))
        
        return future
    
    def _get_connection_internal(self, max_wait_time=None):
        """内部方法：获取数据库连接"""
        if max_wait_time is None:
            max_wait_time = POOL_CONFIG['request_timeout']
            
        connection_id = threading.get_ident()
        
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        
        wait_start = time.time()
        
        while time.time() - wait_start < max_wait_time:
            with self._lock:
                # 尝试从池中获取连接
                if self._pool:
                    conn_info = self._pool.pop(0)
                    connection = conn_info['connection']
                    
                    # 检查连接健康状况
                    if not self._check_connection_health(connection, conn_info['created_at']):
                        self._close_connection_safely(connection)
                        connection = self._create_connection()
                        if not connection:
                            continue
                    
                    # 标记为忙碌状态
                    self._busy_connections[connection_id] = {
                        'connection': connection,
                        'acquired_at': time.time(),
                        'created_at': conn_info.get('created_at', time.time())
                    }
                    return connection
                
                # 创建新连接
                if len(self._busy_connections) < self._max_connections:
                    connection = self._create_connection()
                    if connection:
                        current_time = time.time()
                        self._busy_connections[connection_id] = {
                            'connection': connection,
                            'acquired_at': current_time,
                            'created_at': current_time
                        }
                        return connection
            
            time.sleep(0.02)
            self._check_and_cleanup_dead_connections()
        
        logger.warning(f"无法获取数据库连接，等待超时({max_wait_time}秒)")
        raise TimeoutError(f"获取数据库连接超时({max_wait_time}秒)")
    
    def _check_and_cleanup_dead_connections(self):
        """检查并清理可能死锁的连接"""
        current_time = time.time()
        max_connection_hold_time = DB_CONFIG['max_connection_hold_time']
        
        with self._lock:
            for conn_id in list(self._busy_connections.keys()):
                conn_info = self._busy_connections[conn_id]
                if current_time - conn_info['acquired_at'] > max_connection_hold_time:
                    warning_msg = f"强制释放可能死锁的连接ID {conn_id}，使用时间: {int(current_time - conn_info['acquired_at'])}秒"
                    logger.warning(warning_msg)
                    add_framework_log(f"数据库连接池：{warning_msg}")
                    self._close_connection_safely(conn_info['connection'])
                    del self._busy_connections[conn_id]
    
    def release_connection(self, connection=None):
        """释放数据库连接回连接池"""
        connection_id = threading.get_ident()
        max_usage_time = DB_CONFIG['max_usage_time']
        
        with self._lock:
            try:
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
                
                # 检查连接健康状况和使用时间
                usage_time = time.time() - conn_info.get('acquired_at', time.time())
                created_time = time.time() - conn_info.get('created_at', time.time())
                
                if (self._check_connection_health(connection, conn_info.get('created_at')) and
                    usage_time <= max_usage_time and
                    created_time <= self._connection_lifetime and
                    len(self._pool) < self._max_connections):
                    
                    conn_info['last_used'] = time.time()
                    self._pool.append(conn_info)
                else:
                    self._close_connection_safely(connection)
                    
            except Exception as e:
                logger.error(f"释放连接过程中出错: {str(e)}")
    
    def execute_async(self, sql, params=None):
        """异步执行SQL查询"""
        return self._thread_pool.submit(self._execute_query, sql, params)
        
    def _execute_query(self, sql, params=None):
        """内部方法：执行SQL查询"""
        connection = self.get_connection()
        if not connection:
            raise ValueError("无法获取数据库连接")
            
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(sql, params)
            result = cursor.fetchall() if cursor.rowcount > 0 else []
            return result
        except Exception as e:
            logger.error(f"执行查询失败: {str(e)}")
            raise
        finally:
            if cursor:
                cursor.close()
            self.release_connection(connection)
    
    def batch_execute_async(self, queries):
        """批量异步执行多个查询"""
        futures = []
        for sql, params in queries:
            future = self.execute_async(sql, params)
            futures.append(future)
        return futures
    
    def _maintain_pool(self):
        """维护连接池"""
        while True:
            sleep_interval = DB_CONFIG['pool_maintenance_interval']
            time.sleep(sleep_interval)
            
            try:
                with self._lock:
                    current_time = time.time()
                    
                    self._cleanup_expired_connections(current_time)
                    self._ensure_min_connections()
                    self._check_long_running_connections(current_time)
                    
                    if current_time - self._last_gc_time > self._gc_interval:
                        gc.collect()
                        self._last_gc_time = current_time
                        
            except Exception as e:
                error_msg = f"连接池维护过程中发生错误: {str(e)}"
                logger.error(error_msg)
                add_framework_log(f"数据库连接池错误：{error_msg}")
    
    def _cleanup_expired_connections(self, current_time):
        """清理过期连接"""
        for i in range(len(self._pool) - 1, -1, -1):
            if i >= len(self._pool):
                continue
                
            conn_info = self._pool[i]
            if ((current_time - conn_info['last_used'] > self._timeout and 
                len(self._pool) > self._min_connections) or
                current_time - conn_info['created_at'] > self._connection_lifetime):
                try:
                    conn_info['connection'].close()
                    del self._pool[i]
                except Exception:
                    try:
                        del self._pool[i]
                    except IndexError:
                        pass
    
    def _ensure_min_connections(self):
        """确保维持最小连接数"""
        try:
            while len(self._pool) < self._min_connections:
                conn = self._create_connection()
                if conn:
                    self._pool.append(self._create_connection_info(conn))
                else:
                    break
        except Exception as e:
            logger.error(f"维护最小连接数失败: {str(e)}")
    
    def _check_long_running_connections(self, current_time):
        """检查长时间运行的连接"""
        long_query_warning_time = DB_CONFIG['long_query_warning_time']
        
        for conn_id in list(self._busy_connections.keys()):
            conn_info = self._busy_connections[conn_id]
            if current_time - conn_info['acquired_at'] > long_query_warning_time:
                warning_msg = f"连接ID {conn_id} 已使用超过{long_query_warning_time}秒"
                logger.warning(warning_msg)
                add_framework_log(f"数据库连接池：{warning_msg}")

class ConnectionManager:
    """数据库连接管理器"""
    
    def __init__(self):
        self.pool = DatabasePool()
        self.connection = None
        self.cursor = None
    
    def __enter__(self):
        retry_count = POOL_CONFIG['retry_count']
        retry_interval = POOL_CONFIG['retry_interval']
        
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
                    
            if i < retry_count - 1:
                time.sleep(retry_interval)
                retry_interval *= 1.5
        
        logger.error("无法获取数据库连接")
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

def execute_query(sql, params=None, fetchall=False):
    """执行查询SQL"""
    with ConnectionManager() as manager:
        if manager.execute(sql, params):
            if fetchall:
                return manager.fetchall()
            return manager.fetchone()
    return None if not fetchall else []

def execute_update(sql, params=None):
    """执行更新SQL"""
    with ConnectionManager() as manager:
        if manager.execute(sql, params):
            return manager.commit()
    return False

def execute_transaction(sql_list):
    """执行事务"""
    if not sql_list:
        return True
        
    with ConnectionManager() as manager:
        try:
            for sql_item in sql_list:
                sql = sql_item.get('sql')
                params = sql_item.get('params')
                if not sql:
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

def execute_query_async(sql, params=None, fetchall=False):
    """异步执行查询SQL"""
    def query_func():
        return execute_query(sql, params, fetchall)
        
    return db_pool._thread_pool.submit(query_func)

def execute_update_async(sql, params=None):
    """异步执行更新SQL"""
    def update_func():
        return execute_update(sql, params)
        
    return db_pool._thread_pool.submit(update_func)

def execute_concurrent_queries(query_list):
    """并发执行多个查询"""
    futures = []
    
    for query in query_list:
        sql, params = query[0], query[1]
        fetchall = query[2] if len(query) > 2 else False
        
        future = execute_query_async(sql, params, fetchall)
        futures.append(future)
    
    results = []
    request_timeout = POOL_CONFIG['request_timeout']
    for future in futures:
        try:
            result = future.result(timeout=request_timeout)
            results.append(result)
        except Exception as e:
            logger.error(f"并发查询出错: {str(e)}")
            results.append(None)
    
    return results

class DatabaseService:
    """数据库服务类"""
    
    @staticmethod
    def execute_query(sql, params=None, fetch_all=False):
        """执行查询SQL"""
        return execute_query(sql, params, fetch_all)
    
    @staticmethod
    def execute_update(sql, params=None):
        """执行更新SQL"""
        return execute_update(sql, params)
    
    @staticmethod
    def execute_transaction(operations):
        """执行事务"""
        return execute_transaction(operations)
    
    @staticmethod
    def is_table_exists(table_name):
        """检查表是否存在"""
        sql = """
            SELECT COUNT(*) as count FROM information_schema.tables 
            WHERE table_schema = DATABASE() AND table_name = %s
        """
        result = execute_query(sql, (table_name,))
        return result and result.get('count', 0) > 0
        
    @staticmethod
    def execute_query_async(sql, params=None, fetch_all=False):
        """异步执行查询SQL"""
        return execute_query_async(sql, params, fetch_all)
    
    @staticmethod
    def execute_update_async(sql, params=None):
        """异步执行更新SQL"""
        return execute_update_async(sql, params)
    
    @staticmethod
    def execute_concurrent_queries(query_list):
        """并发执行多个查询"""
        return execute_concurrent_queries(query_list) 