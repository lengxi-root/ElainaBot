#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pymysql, threading, time, gc, logging, queue, os
from pymysql.cursors import DictCursor
from config import DB_CONFIG
import concurrent.futures

# 移除 basicConfig 调用，使用框架统一的日志配置
logger = logging.getLogger('ElainaBot.function.db_pool')

try:
    from web.app import add_framework_log
except:
    def add_framework_log(msg):
        pass

POOL_CONFIG = {
    'connection_timeout': DB_CONFIG['connect_timeout'],
    'read_timeout': DB_CONFIG['read_timeout'],
    'write_timeout': DB_CONFIG['write_timeout'],
    'connection_lifetime': DB_CONFIG['connection_lifetime'],
    'thread_pool_size': None,
    'retry_count': DB_CONFIG['retry_count'],
    'retry_interval': DB_CONFIG['retry_interval']
}

class DatabasePool:
    _instance = None
    _init_lock = threading.Lock()
    _pool_lock = threading.RLock()
    _busy_lock = threading.RLock()
    _maintenance_lock = threading.Lock()
    _pool = []
    _busy_connections = {}
    _initialized = False
    _connection_requests = queue.Queue()
    _thread_pool = None
    _min_connections = 6
    _idle_timeout = 180
    _connection_lifetime = POOL_CONFIG['connection_lifetime']
    _request_timeout = 5.0
    _retry_count = POOL_CONFIG['retry_count']
    _retry_interval = POOL_CONFIG['retry_interval']
    
    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super(DatabasePool, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        with self._init_lock:
            if not self._initialized:
                from concurrent.futures import ThreadPoolExecutor
                self._thread_pool = ThreadPoolExecutor(max_workers=300, thread_name_prefix="DBPool")
                self._conn_request_thread = threading.Thread(target=self._process_connection_requests, daemon=True, name="DBConnRequestProcessor")
                self._conn_request_thread.start()
                self._init_pool()
                maintenance_thread = threading.Thread(target=self._maintain_pool, daemon=True, name="DBPoolMaintenance")
                maintenance_thread.start()
                self._initialized = True

    def _safe_execute(self, func, error_msg="操作失败", *args, **kwargs):
        try:
            return func(*args, **kwargs)
        except:
            return None

    def _retry_with_backoff(self, func, max_retries=None, base_delay=None, error_msg="操作"):
        max_retries = max_retries or self._retry_count
        delay = base_delay or self._retry_interval
        last_error = None
        for i in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_error = e
                if i < max_retries - 1:
                    time.sleep(delay)
                    delay = min(delay * 1.5, 10)
        return None

    def _check_connection_health(self, connection, created_at=None):
        if not connection:
            return False
        try:
            connection.ping(reconnect=True)
            if created_at:
                return time.time() - created_at <= self._connection_lifetime
            return True
        except:
            return False

    def _close_connection_safely(self, connection):
        try:
            connection.close()
        except:
            pass

    def _create_connection_info(self, connection):
        current_time = time.time()
        return {'connection': connection, 'created_at': current_time, 'last_used': current_time}

    def _init_pool(self):
        try:
            successful_connections = 0
            for _ in range(self._min_connections):
                connection = self._create_connection()
                if connection:
                    successful_connections += 1
                    self._pool.append(self._create_connection_info(connection))
        except:
            pass
        if successful_connections > 0:
            add_framework_log(f"动态数据库连接池初始化完成，最少{self._min_connections}个连接，{POOL_CONFIG['thread_pool_size']}个并发线程")
    
    def _create_connection(self):
        def create_func():
            return pymysql.connect(
                host=DB_CONFIG.get('host', 'localhost'), port=DB_CONFIG.get('port', 3306),
                user=DB_CONFIG.get('user', 'root'), password=DB_CONFIG.get('password', ''),
                database=DB_CONFIG.get('database', ''), charset=DB_CONFIG.get('charset', 'utf8mb4'),
                cursorclass=DictCursor, connect_timeout=POOL_CONFIG['connection_timeout'],
                read_timeout=POOL_CONFIG['read_timeout'], write_timeout=POOL_CONFIG['write_timeout'],
                autocommit=DB_CONFIG.get('autocommit', True)
            )
        return self._retry_with_backoff(create_func, error_msg="创建数据库连接")
    
    def _process_connection_requests(self):
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
            except:
                pass
            finally:
                try:
                    self._connection_requests.task_done()
                except:
                    pass
    
    def get_connection(self):
        connection_id = threading.get_ident()
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        return self._get_connection_internal()
    
    def get_connection_async(self, timeout=None):
        if timeout is None:
            timeout = self._request_timeout
        connection_id = threading.get_ident()
        if connection_id in self._busy_connections:
            future = concurrent.futures.Future()
            future.set_result(self._busy_connections[connection_id]['connection'])
            return future
        future = concurrent.futures.Future()
        self._connection_requests.put((future, timeout))
        return future
    
    def _get_connection_internal(self, max_wait_time=None):
        max_wait_time = max_wait_time or self._request_timeout
        connection_id = threading.get_ident()
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        attempts = 0
        max_attempts = int(max_wait_time * 10)
        while attempts < max_attempts:
            connection = self._try_get_or_create_connection(connection_id)
            if connection:
                return connection
            time.sleep(0.1)
            attempts += 1
        raise TimeoutError(f"获取数据库连接超时({max_wait_time}秒)")
    
    def _try_get_or_create_connection(self, connection_id):
        connection = self._get_pooled_connection(connection_id)
        if connection:
            return connection
        return self._create_and_register_connection(connection_id)
    
    def _get_pooled_connection(self, connection_id):
        current_time = time.time()
        with self._pool_lock:
            if not self._pool:
                return None
            while self._pool:
                try:
                    conn_info = self._pool.pop(0)
                    connection = conn_info['connection']
                    if self._check_connection_health(connection, conn_info.get('created_at')):
                        with self._busy_lock:
                            self._busy_connections[connection_id] = {
                                'connection': connection, 'acquired_at': current_time,
                                'created_at': conn_info.get('created_at', current_time)
                            }
                        return connection
                    else:
                        self._close_connection_safely(connection)
                except IndexError:
                    break
        return None
    
    def _create_and_register_connection(self, connection_id):
        connection = self._create_connection()
        if not connection:
            return None
        current_time = time.time()
        with self._busy_lock:
            self._busy_connections[connection_id] = {
                'connection': connection, 'acquired_at': current_time, 'created_at': current_time
            }
        return connection
    
    def _check_and_cleanup_dead_connections(self):
        current_time = time.time()
        max_connection_hold_time = 20
        with self._busy_lock:
            for conn_id in list(self._busy_connections.keys()):
                conn_info = self._busy_connections[conn_id]
                if current_time - conn_info['acquired_at'] > max_connection_hold_time:
                    self._close_connection_safely(conn_info['connection'])
                    del self._busy_connections[conn_id]
    
    def release_connection(self, connection=None):
        connection_id = threading.get_ident()
        try:
            if connection is not None:
                with self._busy_lock:
                    connection_id = self._find_connection_id(connection)
                    if connection_id is None:
                        self._close_connection_safely(connection)
                        return
            with self._busy_lock:
                conn_info = self._busy_connections.pop(connection_id, None)
                if not conn_info:
                    return
            connection = conn_info['connection']
            if self._should_recycle_connection(conn_info):
                conn_info['last_used'] = time.time()
                with self._pool_lock:
                    self._pool.append(conn_info)
            else:
                self._close_connection_safely(connection)
        except:
            pass
    
    def _find_connection_id(self, connection):
        for conn_id, conn_info in self._busy_connections.items():
            if conn_info['connection'] is connection:
                return conn_id
        return None
    
    def _should_recycle_connection(self, conn_info):
        current_time = time.time()
        connection_age = current_time - conn_info.get('created_at', current_time)
        usage_time = current_time - conn_info.get('acquired_at', current_time)
        is_healthy = self._check_connection_health(conn_info['connection'], conn_info.get('created_at'))
        is_within_lifetime = connection_age <= self._connection_lifetime
        is_reasonable_usage = usage_time <= 120
        return is_healthy and is_within_lifetime and is_reasonable_usage
    
    def execute_query(self, sql, params=None, fetchall=False):
        return execute_query(sql, params, fetchall)
    
    def execute_update(self, sql, params=None):
        return execute_update(sql, params)
    
    def execute_transaction(self, operations):
        return execute_transaction(operations)
    
    def execute_async(self, sql, params=None):
        return self._thread_pool.submit(self._execute_query, sql, params)
        
    def _execute_query(self, sql, params=None):
        connection = self.get_connection()
        if not connection:
            raise ValueError("无法获取数据库连接")
        cursor = None
        try:
            cursor = connection.cursor()
            cursor.execute(sql, params)
            return cursor.fetchall() if cursor.rowcount > 0 else []
        finally:
            if cursor:
                cursor.close()
            self.release_connection(connection)
    
    def batch_execute_async(self, queries):
        return [self.execute_async(sql, params) for sql, params in queries]
    
    def _maintain_pool(self):
        maintenance_tasks = [
            ('cleanup', self._cleanup_expired_connections, 1),
            ('ensure_min', self._ensure_min_connections, 1),
            ('check_long_running', self._check_long_running_connections, 2),
            ('gc', self._perform_gc_if_needed, 10),
            ('stats', self._log_pool_stats, 20)
        ]
        cycle_count = 0
        while True:
            time.sleep(15)
            cycle_count += 1
            current_time = time.time()
            try:
                with self._maintenance_lock:
                    for task_name, task_func, interval in maintenance_tasks:
                        if cycle_count % interval == 0:
                            self._safe_execute(task_func, f"{task_name}维护任务失败", current_time)
            except:
                pass
                
    def _log_pool_stats(self, current_time):
        with self._pool_lock:
            pool_size = len(self._pool)
        with self._busy_lock:
            busy_size = len(self._busy_connections)
        add_framework_log(f"动态连接池: 空闲{pool_size}/忙碌{busy_size}")
    
    def _cleanup_expired_connections(self, current_time):
        with self._pool_lock:
            if not self._pool:
                return
            kept_connections = []
            cleaned_count = 0
            current_pool_size = len(self._pool)
            for conn_info in self._pool:
                idle_time = current_time - conn_info['last_used']
                lifetime = current_time - conn_info['created_at']
                should_remove = (lifetime > self._connection_lifetime or 
                               (idle_time > self._idle_timeout and current_pool_size - cleaned_count > self._min_connections))
                if should_remove:
                    self._safe_execute(conn_info['connection'].close, "关闭过期连接失败")
                    cleaned_count += 1
                else:
                    kept_connections.append(conn_info)
            self._pool[:] = kept_connections
    
    def _ensure_min_connections(self, current_time=None):
        with self._pool_lock:
            needed_connections = max(0, self._min_connections - len(self._pool))
            if needed_connections == 0:
                return
            new_connections = []
            for _ in range(needed_connections):
                conn = self._create_connection()
                if conn:
                    new_connections.append(self._create_connection_info(conn))
                else:
                    break
            if new_connections:
                self._pool.extend(new_connections)
    
    def _check_long_running_connections(self, current_time):
        with self._busy_lock:
            long_running_conns = [
                (conn_id, current_time - conn_info['acquired_at'])
                for conn_id, conn_info in self._busy_connections.items()
                if current_time - conn_info['acquired_at'] > 60
            ]
        for conn_id, duration in long_running_conns:
            add_framework_log(f"数据库连接池：连接ID {conn_id} 已使用超过{duration:.1f}秒")
    
    def _perform_gc_if_needed(self, current_time):
        pass

class ConnectionManager:
    def __init__(self):
        self.pool = DatabasePool()
        self.connection = None
        self.cursor = None
    
    def __enter__(self):
        retry_count = DB_CONFIG['retry_count']
        retry_interval = DB_CONFIG['retry_interval']
        last_error = None
        for i in range(retry_count):
            try:
                self.connection = self.pool.get_connection()
                if self.connection:
                    self.connection.ping(reconnect=True)
                    self.cursor = self.connection.cursor(DictCursor)
                    self.cursor.execute("SELECT 1")
                    self.cursor.fetchone()
                    return self
            except Exception as e:
                last_error = e
                if self.connection:
                    try:
                        self.pool.release_connection(self.connection)
                    except:
                        pass
                    self.connection = None
                if self.cursor:
                    try:
                        self.cursor.close()
                    except:
                        pass
                    self.cursor = None
            if i < retry_count - 1:
                time.sleep(retry_interval)
                retry_interval = min(retry_interval * 1.5, 10)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.cursor:
                self.cursor.close()
                self.cursor = None
        except:
            pass
        try:
            if self.connection:
                if exc_type:
                    try:
                        self.connection.rollback()
                    except:
                        pass
                self.pool.release_connection(self.connection)
                self.connection = None
        except:
            pass
    
    def execute(self, sql, params=None):
        if not self.cursor:
            return False
        try:
            self.cursor.execute(sql, params)
            return True
        except:
            return False
    
    def fetchone(self):
        if not self.cursor:
            return None
        return self.cursor.fetchone()
    
    def fetchall(self):
        if not self.cursor:
            return []
        return self.cursor.fetchall()
    
    def commit(self):
        if self.connection:
            try:
                self.connection.commit()
                return True
            except:
                return False
        return False
    
    def rollback(self):
        if self.connection:
            try:
                self.connection.rollback()
                return True
            except:
                return False
        return False


db_pool = DatabasePool()

def execute_query(sql, params=None, fetchall=False):
    try:
        with ConnectionManager() as manager:
            if manager.execute(sql, params):
                return manager.fetchall() if fetchall else manager.fetchone()
        return [] if fetchall else None
    except:
        return [] if fetchall else None

def execute_update(sql, params=None):
    try:
        with ConnectionManager() as manager:
            if manager.execute(sql, params):
                return manager.commit()
        return False
    except:
        return False

def execute_transaction(sql_list):
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
        except:
            try:
                manager.rollback()
            except:
                pass
            return False

def execute_query_async(sql, params=None, fetchall=False):
    return db_pool._thread_pool.submit(lambda: execute_query(sql, params, fetchall))

def execute_update_async(sql, params=None):
    return db_pool._thread_pool.submit(lambda: execute_update(sql, params))

def execute_concurrent_queries(query_list):
    futures = [execute_query_async(query[0], query[1], query[2] if len(query) > 2 else False) for query in query_list]
    results = []
    for future in futures:
        try:
            results.append(future.result(timeout=3.0))
        except:
            results.append(None)
    return results

class DatabaseService:
    @staticmethod
    def execute_query(sql, params=None, fetch_all=False):
        return execute_query(sql, params, fetch_all)
    
    @staticmethod
    def execute_update(sql, params=None):
        return execute_update(sql, params)
    
    @staticmethod
    def execute_transaction(operations):
        return execute_transaction(operations)
    
    @staticmethod
    def is_table_exists(table_name):
        result = execute_query("SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (table_name,))
        return result and result.get('count', 0) > 0
        
    @staticmethod
    def execute_query_async(sql, params=None, fetch_all=False):
        return execute_query_async(sql, params, fetch_all)
    
    @staticmethod
    def execute_update_async(sql, params=None):
        return execute_update_async(sql, params)
    
    @staticmethod
    def execute_concurrent_queries(query_list):
        return execute_concurrent_queries(query_list) 