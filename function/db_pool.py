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
import os

logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('db_pool')

try:
    from web.app import add_framework_log
except ImportError:
    def add_framework_log(msg):
        pass

POOL_CONFIG = {
    'connection_timeout': DB_CONFIG['connect_timeout'],
    'read_timeout': DB_CONFIG['read_timeout'],
    'write_timeout': DB_CONFIG['write_timeout'],
    'connection_lifetime': DB_CONFIG['connection_lifetime'],
    'thread_pool_size': max(50, (os.cpu_count() or 4) * 4),
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
    
    _min_connections = 10
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
                thread_pool_size = POOL_CONFIG['thread_pool_size']
                logger.info(f"初始化动态数据库线程池，线程数: {thread_pool_size}（CPU核心数: {os.cpu_count() or 'unknown'}）")
                
                self._thread_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=thread_pool_size,
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

    def _safe_execute(self, func, error_msg="操作失败", *args, **kwargs):
        """统一的安全执行和错误处理"""
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"{error_msg}: {str(e)}")
            return None

    def _retry_with_backoff(self, func, max_retries=None, base_delay=None, error_msg="操作"):
        """统一的重试逻辑"""
        max_retries = max_retries or self._retry_count
        delay = base_delay or self._retry_interval
        
        last_error = None
        
        for i in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_error = e
                if i < max_retries - 1:
                    logger.warning(f"{error_msg}失败(尝试 {i+1}/{max_retries}): {str(e)}")
                    time.sleep(delay)
                    delay = min(delay * 1.5, 10)
        
        logger.error(f"{error_msg}失败，已达到最大重试次数: {str(last_error)}")
        return None

    def _check_connection_health(self, connection, created_at=None):
        """简化的连接健康检查"""
        if not connection:
            return False
        
        try:
            connection.ping(reconnect=True)
            # 检查连接生命周期
            if created_at:
                current_time = time.time()
                return current_time - created_at <= self._connection_lifetime
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
        """初始化动态连接池 - 最少保持5个连接"""
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
            thread_pool_size = POOL_CONFIG['thread_pool_size']
            logger.info(f"动态数据库连接池初始化完成，连接数: {successful_connections}，线程池: {thread_pool_size}")
            add_framework_log(f"动态数据库连接池初始化完成，最少{self._min_connections}个连接，{thread_pool_size}个并发线程")
    
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
        """获取数据库连接（异步）"""
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
        """获取数据库连接"""
        max_wait_time = max_wait_time or self._request_timeout
        connection_id = threading.get_ident()
        
        # 检查是否已有连接
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        
        attempts = 0
        max_attempts = int(max_wait_time * 10)  # 每100ms一次尝试
        
        while attempts < max_attempts:
            connection = self._try_get_or_create_connection(connection_id)
            if connection:
                return connection
            
            time.sleep(0.1)  # 固定100ms等待
            attempts += 1
        
        logger.warning(f"无法获取数据库连接，等待超时({max_wait_time}秒)")
        raise TimeoutError(f"获取数据库连接超时({max_wait_time}秒)")
    
    def _try_get_or_create_connection(self, connection_id):
        """尝试获取或创建连接 - 动态扩展策略"""
        # 策略1：从池中获取现有连接（优先使用剩余时间最多的）
        connection = self._get_pooled_connection(connection_id)
        if connection:
            return connection
        
        # 策略2：动态创建新连接（无最大数量限制）
        return self._create_and_register_connection(connection_id)
    
    def _get_pooled_connection(self, connection_id):
        """从连接池获取连接"""
        current_time = time.time()
        
        with self._pool_lock:
            if not self._pool:
                return None
            while self._pool:
                try:
                    conn_info = self._pool.pop(0)
                    connection = conn_info['connection']
                    
                    # 快速健康检查
                    if self._check_connection_health(connection, conn_info.get('created_at')):
                        # 在pool_lock保护下获取busy_lock，符合锁顺序约定
                        with self._busy_lock:
                            self._busy_connections[connection_id] = {
                                'connection': connection,
                                'acquired_at': current_time,
                                'created_at': conn_info.get('created_at', current_time)
                            }
                        return connection
                    else:
                        # 不健康连接直接关闭
                        self._close_connection_safely(connection)
                        
                except IndexError:
                    # 池为空，跳出循环
                    break
        
        return None
    
    def _create_and_register_connection(self, connection_id):
        """创建并注册新连接 - 使用颗粒锁保护"""
        connection = self._create_connection()
        if not connection:
            logger.warning("动态创建数据库连接失败")
            return None
        
        current_time = time.time()
        # 使用busy_lock保护忙碌连接映射
        with self._busy_lock:
            self._busy_connections[connection_id] = {
                'connection': connection,
                'acquired_at': current_time,
                'created_at': current_time
            }
        
        return connection
    
    def _check_and_cleanup_dead_connections(self):
        """检查并清理可能死锁的连接"""
        current_time = time.time()
        max_connection_hold_time = 20  # 连接占用超过20秒认为可能死锁
        
        with self._busy_lock:
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
        
        try:
            # 处理指定连接的情况
            if connection is not None:
                # 使用锁保护查找连接ID
                with self._busy_lock:
                    connection_id = self._find_connection_id(connection)
                    if connection_id is None:
                        self._close_connection_safely(connection)
                        return
            
            # 使用busy_lock移除忙碌连接
            with self._busy_lock:
                conn_info = self._busy_connections.pop(connection_id, None)
                if not conn_info:
                    return
            
            connection = conn_info['connection']
            
            # 检查连接是否应该回收
            if self._should_recycle_connection(conn_info):
                conn_info['last_used'] = time.time()
                # 使用pool_lock保护池操作
                with self._pool_lock:
                    self._pool.append(conn_info)
            else:
                self._close_connection_safely(connection)
                
        except Exception as e:
            logger.error(f"释放连接过程中出错: {str(e)}")
    
    def _find_connection_id(self, connection):
        """快速查找连接ID（调用时需要在_busy_lock保护下）"""
        for conn_id, conn_info in self._busy_connections.items():
            if conn_info['connection'] is connection:
                return conn_id
        return None
    
    def _should_recycle_connection(self, conn_info):
        """检查连接是否应该回收到池中"""
        current_time = time.time()
        max_usage_time = 120
        
        connection_age = current_time - conn_info.get('created_at', current_time)
        usage_time = current_time - conn_info.get('acquired_at', current_time)
        
        # 只有在连接确实不健康或生命周期结束时才不回收
        is_healthy = self._check_connection_health(conn_info['connection'], conn_info.get('created_at'))
        is_within_lifetime = connection_age <= self._connection_lifetime
        is_reasonable_usage = usage_time <= max_usage_time
        
        return is_healthy and is_within_lifetime and is_reasonable_usage
    
    def execute_query(self, sql, params=None, fetchall=False):
        """执行查询SQL"""
        return execute_query(sql, params, fetchall)
    
    def execute_update(self, sql, params=None):
        """执行更新SQL"""
        return execute_update(sql, params)
    
    def execute_transaction(self, operations):
        """执行事务"""
        return execute_transaction(operations)
    
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
        sleep_interval = 15
        
        maintenance_tasks = [
            ('cleanup', self._cleanup_expired_connections, 1),
            ('ensure_min', self._ensure_min_connections, 1),
            ('check_long_running', self._check_long_running_connections, 2),
            ('gc', self._perform_gc_if_needed, 10),
            ('stats', self._log_pool_stats, 20)
        ]
        
        cycle_count = 0
        
        while True:
            time.sleep(sleep_interval)
            cycle_count += 1
            current_time = time.time()
            
            try:
                with self._maintenance_lock:
                    for task_name, task_func, interval in maintenance_tasks:
                        if cycle_count % interval == 0:
                            self._safe_execute(task_func, f"{task_name}维护任务失败", current_time)
                        
            except Exception as e:
                error_msg = f"动态连接池维护过程中发生错误: {str(e)}"
                logger.error(error_msg)
                add_framework_log(f"数据库连接池错误：{error_msg}")
                
    def _log_pool_stats(self, current_time):
        """简化的连接池统计"""
        with self._pool_lock:
            pool_size = len(self._pool)
        with self._busy_lock:
            busy_size = len(self._busy_connections)
        add_framework_log(f"动态连接池: 空闲{pool_size}/忙碌{busy_size}")
    
    def _cleanup_expired_connections(self, current_time):
        """清理过期连接"""
        with self._pool_lock:
            if not self._pool:
                return
            
            # 直接遍历并移除，避免索引计算
            kept_connections = []
            cleaned_count = 0
            current_pool_size = len(self._pool)
            
            for conn_info in self._pool:
                idle_time = current_time - conn_info['last_used']
                lifetime = current_time - conn_info['created_at']
                
                # 清理策略：超过生命周期 或 (空闲超时且超过最小连接数)
                should_remove = (lifetime > self._connection_lifetime or 
                               (idle_time > self._idle_timeout and 
                                current_pool_size - cleaned_count > self._min_connections))
                
                if should_remove:
                    self._safe_execute(conn_info['connection'].close, "关闭过期连接失败")
                    cleaned_count += 1
                else:
                    kept_connections.append(conn_info)
            
            self._pool[:] = kept_connections
            
            if cleaned_count > 0:
                pass  # 静默清理过期连接
    
    def _ensure_min_connections(self, current_time=None):
        """确保维持最小连接数 - 动态补充策略"""
        with self._pool_lock:
            current_pool_size = len(self._pool)
            needed_connections = max(0, self._min_connections - current_pool_size)
            
            if needed_connections == 0:
                return
            
            # 批量创建连接补充到最小数量
            created_count = 0
            new_connections = []
            
            for _ in range(needed_connections):
                conn = self._create_connection()
                if conn:
                    new_connections.append(self._create_connection_info(conn))
                    created_count += 1
                else:
                    logger.warning(f"补充最小连接数时创建连接失败，已创建{created_count}/{needed_connections}个")
                    break
            
            # 批量添加到池中
            if new_connections:
                self._pool.extend(new_connections)
                # 静默补充连接，不记录日志
    
    def _check_long_running_connections(self, current_time):
        """检查长时间运行的连接"""
        long_query_warning_time = 60  # 查询超过60秒发出警告
        
        with self._busy_lock:
            long_running_conns = [
                (conn_id, current_time - conn_info['acquired_at'])
                for conn_id, conn_info in self._busy_connections.items()
                if current_time - conn_info['acquired_at'] > long_query_warning_time
            ]
        
        # 批量记录警告
        for conn_id, duration in long_running_conns:
            warning_msg = f"连接ID {conn_id} 已使用超过{duration:.1f}秒"
            logger.warning(warning_msg)
            add_framework_log(f"数据库连接池：{warning_msg}")
    
    def _perform_gc_if_needed(self, current_time):
        """简化的清理任务"""
        pass  # 让Python自动管理内存

class ConnectionManager:
    """数据库连接管理器"""
    
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
                # 获取连接
                self.connection = self.pool.get_connection()
                if self.connection:
                    # 验证连接是否有效
                    self.connection.ping(reconnect=True)
                    
                    # 创建游标 - 使用DictCursor返回字典结果
                    self.cursor = self.connection.cursor(DictCursor)
                    
                    # 简单测试查询以确认连接可用
                    self.cursor.execute("SELECT 1")
                    self.cursor.fetchone()
                    
                    return self
                    
            except Exception as e:
                last_error = e
                error_msg = f"获取数据库连接失败（第{i+1}/{retry_count}次）: {str(e)}"
                
                if i < retry_count - 1:
                    logger.warning(error_msg)
                else:
                    logger.error(error_msg)
                
                # 清理失败的连接
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
            
            # 等待重试
            if i < retry_count - 1:
                time.sleep(retry_interval)
                retry_interval = min(retry_interval * 1.5, 10)  # 最大延迟10秒
        
        logger.error(f"所有数据库连接尝试均失败，最后错误: {str(last_error)}")
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
            logger.error("数据库游标未初始化")
            return False
        
        try:
            self.cursor.execute(sql, params)
            return True
        except Exception as e:
            error_msg = str(e) if str(e).strip() else f"执行SQL时发生未知错误"
            logger.error(f"执行SQL失败: {error_msg} | SQL: {sql} | 参数: {params}")
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
    try:
        with ConnectionManager() as manager:
            if manager.execute(sql, params):
                if fetchall:
                    return manager.fetchall()
                return manager.fetchone()
        return None if not fetchall else []
    except Exception as e:
        logger.error(f"执行查询SQL失败: {sql} | 参数: {params} | 错误: {str(e)}")
        return None if not fetchall else []

def execute_update(sql, params=None):
    """执行更新SQL"""
    try:
        with ConnectionManager() as manager:
            if manager.execute(sql, params):
                return manager.commit()
        return False
    except Exception as e:
        logger.error(f"执行更新SQL失败: {sql} | 参数: {params} | 错误: {str(e)}")
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
    request_timeout = 3.0  # 请求超时3秒
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