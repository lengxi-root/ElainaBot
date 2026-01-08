#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pymysql, threading, time, logging, queue
from pymysql.cursors import DictCursor
from config import DB_CONFIG
from concurrent.futures import ThreadPoolExecutor, Future

logger = logging.getLogger('ElainaBot.function.db_pool')

try:
    from web.app import add_framework_log
except:
    def add_framework_log(msg):
        pass

_CONNECTION_TIMEOUT = DB_CONFIG['connect_timeout']
_READ_TIMEOUT = DB_CONFIG['read_timeout']
_WRITE_TIMEOUT = DB_CONFIG['write_timeout']
_CONNECTION_LIFETIME = DB_CONFIG['connection_lifetime']
_RETRY_COUNT = DB_CONFIG['retry_count']
_RETRY_INTERVAL = DB_CONFIG['retry_interval']
_MIN_CONNECTIONS = 6
_IDLE_TIMEOUT = 180
_REQUEST_TIMEOUT = 5.0
_MAX_RETRY_DELAY = 10
_LONG_RUNNING_THRESHOLD = 60
_MAINTENANCE_INTERVAL = 15

_DB_HOST = DB_CONFIG.get('host', 'localhost')
_DB_PORT = DB_CONFIG.get('port', 3306)
_DB_USER = DB_CONFIG.get('user', 'root')
_DB_PASSWORD = DB_CONFIG.get('password', '')
_DB_DATABASE = DB_CONFIG.get('database', '')
_DB_AUTOCOMMIT = DB_CONFIG.get('autocommit', True)

_TABLE_EXISTS_SQL = "SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s"

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
    
    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super(DatabasePool, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        with self._init_lock:
            if not self._initialized:
                self._thread_pool = ThreadPoolExecutor(max_workers=300, thread_name_prefix="DBPool")
                threading.Thread(target=self._process_connection_requests, daemon=True, name="DBConnRequestProcessor").start()
                self._init_pool()
                threading.Thread(target=self._maintain_pool, daemon=True, name="DBPoolMaintenance").start()
                self._initialized = True

    def _retry_with_backoff(self, func, max_retries=_RETRY_COUNT, base_delay=_RETRY_INTERVAL):
        delay = base_delay
        for i in range(max_retries):
            try:
                return func()
            except:
                if i < max_retries - 1:
                    time.sleep(delay)
                    delay = min(delay * 1.5, _MAX_RETRY_DELAY)
        return None

    def _check_connection_health(self, connection, created_at=None):
        if not connection:
            return False
        try:
            connection.ping(reconnect=True)
            return created_at is None or time.time() - created_at <= _CONNECTION_LIFETIME
        except:
            return False

    def _close_connection_safely(self, connection):
        try:
            connection.close()
        except:
            pass

    def _init_pool(self):
        successful = 0
        for _ in range(_MIN_CONNECTIONS):
            conn = self._create_connection()
            if conn:
                successful += 1
                current_time = time.time()
                self._pool.append({'connection': conn, 'created_at': current_time, 'last_used': current_time})
        if successful > 0:
            add_framework_log(f"动态数据库连接池初始化完成，最少{_MIN_CONNECTIONS}个连接")
    
    def _create_connection(self):
        def create_func():
            return pymysql.connect(
                host=_DB_HOST, port=_DB_PORT, user=_DB_USER, password=_DB_PASSWORD,
                database=_DB_DATABASE, charset='utf8mb4', cursorclass=DictCursor,
                connect_timeout=_CONNECTION_TIMEOUT, read_timeout=_READ_TIMEOUT,
                write_timeout=_WRITE_TIMEOUT, autocommit=_DB_AUTOCOMMIT
            )
        return self._retry_with_backoff(create_func)
    
    def _process_connection_requests(self):
        while True:
            try:
                request = self._connection_requests.get()
                if request is None:
                    break
                future, timeout = request
                try:
                    future.set_result(self._get_connection_internal(timeout))
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
        busy_conn = self._busy_connections.get(connection_id)
        if busy_conn:
            return busy_conn['connection']
        return self._get_connection_internal()
    
    def get_connection_async(self, timeout=None):
        connection_id = threading.get_ident()
        busy_conn = self._busy_connections.get(connection_id)
        if busy_conn:
            future = Future()
            future.set_result(busy_conn['connection'])
            return future
        future = Future()
        self._connection_requests.put((future, timeout or _REQUEST_TIMEOUT))
        return future
    
    def _get_connection_internal(self, max_wait_time=None):
        max_wait_time = max_wait_time or _REQUEST_TIMEOUT
        connection_id = threading.get_ident()
        busy_conn = self._busy_connections.get(connection_id)
        if busy_conn:
            return busy_conn['connection']
        max_attempts = int(max_wait_time * 10)
        for _ in range(max_attempts):
            conn = self._get_pooled_connection(connection_id) or self._create_and_register_connection(connection_id)
            if conn:
                return conn
            time.sleep(0.1)
        raise TimeoutError(f"获取数据库连接超时({max_wait_time}秒)")
    
    def _get_pooled_connection(self, connection_id):
        current_time = time.time()
        with self._pool_lock:
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
    
    def release_connection(self, connection=None):
        connection_id = threading.get_ident()
        try:
            if connection is not None:
                with self._busy_lock:
                    for cid, info in self._busy_connections.items():
                        if info['connection'] is connection:
                            connection_id = cid
                            break
                    else:
                        self._close_connection_safely(connection)
                        return
            with self._busy_lock:
                conn_info = self._busy_connections.pop(connection_id, None)
            if not conn_info:
                return
            connection = conn_info['connection']
            current_time = time.time()
            age = current_time - conn_info.get('created_at', current_time)
            usage = current_time - conn_info.get('acquired_at', current_time)
            if (self._check_connection_health(connection, conn_info.get('created_at')) and 
                age <= _CONNECTION_LIFETIME and usage <= 120):
                conn_info['last_used'] = current_time
                with self._pool_lock:
                    self._pool.append(conn_info)
            else:
                self._close_connection_safely(connection)
        except:
            pass
    
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
        cycle_count = 0
        while True:
            time.sleep(_MAINTENANCE_INTERVAL)
            cycle_count += 1
            current_time = time.time()
            try:
                with self._maintenance_lock:
                    self._cleanup_expired_connections(current_time)
                    self._ensure_min_connections()
                    if cycle_count % 2 == 0:
                        self._check_long_running_connections(current_time)
            except:
                pass
                
    def _cleanup_expired_connections(self, current_time):
        with self._pool_lock:
            if not self._pool:
                return
            kept = []
            pool_size = len(self._pool)
            cleaned = 0
            for conn_info in self._pool:
                idle = current_time - conn_info['last_used']
                age = current_time - conn_info['created_at']
                if age > _CONNECTION_LIFETIME or (idle > _IDLE_TIMEOUT and pool_size - cleaned > _MIN_CONNECTIONS):
                    try:
                        conn_info['connection'].close()
                    except:
                        pass
                    cleaned += 1
                else:
                    kept.append(conn_info)
            self._pool[:] = kept
    
    def _ensure_min_connections(self):
        with self._pool_lock:
            needed = _MIN_CONNECTIONS - len(self._pool)
            if needed <= 0:
                return
            for _ in range(needed):
                conn = self._create_connection()
                if conn:
                    current_time = time.time()
                    self._pool.append({'connection': conn, 'created_at': current_time, 'last_used': current_time})
                else:
                    break
    
    def _check_long_running_connections(self, current_time):
        with self._busy_lock:
            for conn_id, conn_info in self._busy_connections.items():
                duration = current_time - conn_info['acquired_at']
                if duration > _LONG_RUNNING_THRESHOLD:
                    add_framework_log(f"数据库连接池：连接ID {conn_id} 已使用超过{duration:.1f}秒")


class ConnectionManager:
    __slots__ = ('pool', 'connection', 'cursor')
    
    def __init__(self):
        self.pool = DatabasePool()
        self.connection = None
        self.cursor = None
    
    def __enter__(self):
        delay = _RETRY_INTERVAL
        for i in range(_RETRY_COUNT):
            try:
                self.connection = self.pool.get_connection()
                if self.connection:
                    self.connection.ping(reconnect=True)
                    self.cursor = self.connection.cursor(DictCursor)
                    self.cursor.execute("SELECT 1")
                    self.cursor.fetchone()
                    return self
            except:
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
            if i < _RETRY_COUNT - 1:
                time.sleep(delay)
                delay = min(delay * 1.5, _MAX_RETRY_DELAY)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.cursor:
            try:
                self.cursor.close()
            except:
                pass
            self.cursor = None
        if self.connection:
            if exc_type:
                try:
                    self.connection.rollback()
                except:
                    pass
            try:
                self.pool.release_connection(self.connection)
            except:
                pass
            self.connection = None
    
    def execute(self, sql, params=None):
        if not self.cursor:
            return False
        try:
            self.cursor.execute(sql, params)
            return True
        except:
            return False
    
    def fetchone(self):
        return self.cursor.fetchone() if self.cursor else None
    
    def fetchall(self):
        return self.cursor.fetchall() if self.cursor else []
    
    def commit(self):
        if not self.connection:
            return False
        try:
            self.connection.commit()
            return True
        except:
            return False
    
    def rollback(self):
        if not self.connection:
            return False
        try:
            self.connection.rollback()
            return True
        except:
            return False


db_pool = DatabasePool()

def execute_query(sql, params=None, fetchall=False):
    try:
        with ConnectionManager() as m:
            if m.execute(sql, params):
                return m.fetchall() if fetchall else m.fetchone()
    except:
        pass
    return [] if fetchall else None

def execute_update(sql, params=None):
    try:
        with ConnectionManager() as m:
            if m.execute(sql, params):
                return m.commit()
    except:
        pass
    return False

def execute_transaction(sql_list):
    if not sql_list:
        return True
    with ConnectionManager() as m:
        try:
            for item in sql_list:
                sql = item.get('sql')
                if sql and not m.execute(sql, item.get('params')):
                    m.rollback()
                    return False
            return m.commit()
        except:
            try:
                m.rollback()
            except:
                pass
            return False

def execute_query_async(sql, params=None, fetchall=False):
    return db_pool._thread_pool.submit(lambda: execute_query(sql, params, fetchall))

def execute_update_async(sql, params=None):
    return db_pool._thread_pool.submit(lambda: execute_update(sql, params))

def execute_concurrent_queries(query_list):
    futures = [execute_query_async(q[0], q[1], q[2] if len(q) > 2 else False) for q in query_list]
    results = []
    for f in futures:
        try:
            results.append(f.result(timeout=3.0))
        except:
            results.append(None)
    return results


class DatabaseService:
    execute_query = staticmethod(execute_query)
    execute_update = staticmethod(execute_update)
    execute_transaction = staticmethod(execute_transaction)
    execute_query_async = staticmethod(execute_query_async)
    execute_update_async = staticmethod(execute_update_async)
    execute_concurrent_queries = staticmethod(execute_concurrent_queries)
    
    @staticmethod
    def is_table_exists(table_name):
        result = execute_query(_TABLE_EXISTS_SQL, (table_name,))
        return result and result.get('count', 0) > 0