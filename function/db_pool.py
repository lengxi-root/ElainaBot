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
import warnings
import asyncio  # 添加异步支持
import httpx    # 替换urllib3
import queue  # 队列模块，用于连接请求管理
import concurrent.futures  # 线程池支持
import functools  # 函数工具

# 设置日志记录器
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('db_pool')

# 创建异步HTTP客户端
async_client = None

def get_async_client():
    """获取全局异步HTTP客户端"""
    global async_client
    if async_client is None or async_client.is_closed:
        async_client = httpx.AsyncClient(
            verify=False,  # 禁用SSL验证，相当于原来的不安全请求警告
            timeout=30.0,
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0
            )
        )
    return async_client

def run_async(coroutine):
    """在同步环境中运行异步函数"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()

async def close_async_client():
    """关闭异步HTTP客户端"""
    global async_client
    if async_client is not None and not async_client.is_closed:
        await async_client.aclose()
        async_client = None

# 当应用退出时关闭客户端
import atexit
atexit.register(lambda: run_async(close_async_client()))

# 异步HTTP请求方法
async def async_request(method, url, **kwargs):
    """执行异步HTTP请求"""
    client = get_async_client()
    try:
        response = await client.request(method, url, **kwargs)
        return response
    except Exception as e:
        logger.error(f"HTTP请求错误: {str(e)}")
        raise

# 导入框架日志记录功能，用于重要日志
try:
    from web.app import add_framework_log
except ImportError:
    # 如果导入失败，创建一个空函数来避免错误
    def add_framework_log(msg):
        pass

# 从配置中获取连接池设置，如果不存在则使用默认值
POOL_CONFIG = {
    'max_connections': DB_CONFIG.get('pool_size', 15),  # 增加最大连接数
    'min_connections': DB_CONFIG.get('min_pool_size', max(3, DB_CONFIG.get('pool_size', 15) // 3)),
    'connection_timeout': DB_CONFIG.get('connect_timeout', 10),  # 增加连接超时
    'read_timeout': DB_CONFIG.get('read_timeout', 30),
    'write_timeout': DB_CONFIG.get('write_timeout', 30),
    'connection_lifetime': DB_CONFIG.get('connection_lifetime', 1200),  # 连接生命周期(秒)
    'gc_interval': DB_CONFIG.get('gc_interval', 60),  # 垃圾回收间隔(秒)
    'idle_timeout': DB_CONFIG.get('idle_timeout', 10),  # 空闲连接超时(秒)
    'thread_pool_size': DB_CONFIG.get('thread_pool_size', 8),  # 增加线程池大小
    'request_timeout': DB_CONFIG.get('request_timeout', 10.0),  # 增加请求超时时间到10秒
    'retry_count': DB_CONFIG.get('retry_count', 3),  # 重试次数
    'retry_interval': DB_CONFIG.get('retry_interval', 0.5)  # 重试间隔(秒)
}

class DatabasePool:
    _instance = None
    _lock = threading.Lock()
    _pool = []
    _busy_connections = {}  # 正在使用的连接
    _initialized = False
    _last_gc_time = 0      # 上次垃圾回收时间
    _connection_requests = queue.Queue()  # 连接请求队列
    _thread_pool = None    # 线程池
    
    # 从配置加载参数
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
        # 单例模式下只初始化一次
        with self._lock:
            if not self._initialized:
                # 创建线程池
                self._thread_pool = concurrent.futures.ThreadPoolExecutor(
                    max_workers=POOL_CONFIG['thread_pool_size'],
                    thread_name_prefix="DBPool"
                )
                
                # 启动连接请求处理线程
                self._conn_request_thread = threading.Thread(
                    target=self._process_connection_requests,
                    daemon=True,
                    name="DBConnRequestProcessor"
                )
                self._conn_request_thread.start()
                
                self._init_pool()
                # 启动维护线程
                maintenance_thread = threading.Thread(
                    target=self._maintain_pool,
                    daemon=True,
                    name="DBPoolMaintenance"
                )
                maintenance_thread.start()
                
                self._initialized = True
    
    def _init_pool(self):
        """初始化连接池"""
        try:
            # 创建初始连接
            successful_connections = 0
            for _ in range(self._min_connections):
                connection = self._create_connection()
                if connection:
                    successful_connections += 1
                    self._pool.append({
                        'connection': connection,
                        'created_at': time.time(),
                        'last_used': time.time()
                    })
        except Exception as e:
            error_msg = f"初始化数据库连接池失败: {str(e)}"
            logger.error(error_msg)
            add_framework_log(f"数据库连接池错误：{error_msg}")
        
        if successful_connections > 0:
            logger.info("数据库连接池初始化完成")
            add_framework_log("数据库连接池初始化完成")
    
    def _create_connection(self):
        """创建新的数据库连接"""
        retry_count = POOL_CONFIG['retry_count']
        retry_delay = POOL_CONFIG['retry_interval']
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
                    connect_timeout=POOL_CONFIG['connection_timeout'],
                    read_timeout=POOL_CONFIG['read_timeout'],
                    write_timeout=POOL_CONFIG['write_timeout'],
                    autocommit=DB_CONFIG.get('autocommit', True)  # 自动提交模式避免事务泄漏
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
    
    def _close_connection_safely(self, connection):
        """安全关闭连接"""
        try:
            connection.close()
        except Exception:
            pass
    
    def _process_connection_requests(self):
        """处理连接请求队列"""
        while True:
            try:
                # 从队列中获取请求
                request = self._connection_requests.get()
                if request is None:  # 停止信号
                    break
                    
                future, timeout = request
                
                # 尝试获取连接
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
        """获取数据库连接 - 同步版本"""
        connection_id = threading.get_ident()
        
        # 如果当前线程已经有连接，直接返回
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        
        # 直接调用内部方法获取连接
        return self._get_connection_internal()
    
    def get_connection_async(self, timeout=None):
        """获取数据库连接 - 异步版本，返回Future"""
        if timeout is None:
            timeout = POOL_CONFIG['request_timeout']
            
        connection_id = threading.get_ident()
        
        # 如果当前线程已经有连接，直接返回已完成的Future
        if connection_id in self._busy_connections:
            future = concurrent.futures.Future()
            future.set_result(self._busy_connections[connection_id]['connection'])
            return future
        
        # 创建Future对象
        future = concurrent.futures.Future()
        
        # 将请求添加到队列
        self._connection_requests.put((future, timeout))
        
        return future
    
    def _get_connection_internal(self, max_wait_time=None):
        """内部方法：获取数据库连接"""
        if max_wait_time is None:
            max_wait_time = POOL_CONFIG['request_timeout']
            
        connection_id = threading.get_ident()
        
        # 如果当前线程已经有连接，直接返回
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        
        wait_start = time.time()
        
        while time.time() - wait_start < max_wait_time:
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
                            continue  # 尝试下一个连接
                    
                    # 检查连接是否超过最大生命周期，超过则创建新连接
                    if time.time() - conn_info['created_at'] > self._connection_lifetime:
                        self._close_connection_safely(connection)
                        connection = self._create_connection()
                        if not connection:
                            continue  # 尝试下一个连接
                    
                    # 标记为忙碌状态
                    self._busy_connections[connection_id] = {
                        'connection': connection,
                        'acquired_at': time.time(),
                        'created_at': conn_info.get('created_at', time.time())
                    }
                    return connection
                
                # 如果池中没有连接但未达到最大连接数，创建新连接
                if len(self._busy_connections) < self._max_connections:
                    connection = self._create_connection()
                    if connection:
                        self._busy_connections[connection_id] = {
                            'connection': connection,
                            'acquired_at': time.time(),
                            'created_at': time.time()
                        }
                        return connection
            
            # 达到最大连接数或创建连接失败，等待20ms后重试
            time.sleep(0.02)
            
            # 检查是否有长时间未释放的连接，强制释放
            self._check_and_cleanup_dead_connections()
        
        logger.warning(f"无法获取数据库连接，等待超时({max_wait_time}秒)")
        raise TimeoutError(f"获取数据库连接超时({max_wait_time}秒)")
    
    def _check_and_cleanup_dead_connections(self):
        """检查并清理可能死锁的连接"""
        current_time = time.time()
        max_connection_hold_time = DB_CONFIG.get('max_connection_hold_time', 20)  # 默认20秒
        
        with self._lock:
            for conn_id in list(self._busy_connections.keys()):
                conn_info = self._busy_connections[conn_id]
                # 连接使用超过指定秒数视为可能死锁
                if current_time - conn_info['acquired_at'] > max_connection_hold_time:
                    warning_msg = f"警告: 强制释放可能死锁的连接ID {conn_id}，使用时间: {int(current_time - conn_info['acquired_at'])}秒"
                    logger.warning(warning_msg)
                    add_framework_log(f"数据库连接池：{warning_msg}")
                    self._close_connection_safely(conn_info['connection'])
                    del self._busy_connections[conn_id]
    
    def release_connection(self, connection=None):
        """释放数据库连接回连接池"""
        connection_id = threading.get_ident()
        max_usage_time = DB_CONFIG.get('max_usage_time', 30)  # 默认最长使用30秒
        
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
                    # 检查连接是否超过最大生命周期
                    usage_time = time.time() - conn_info.get('acquired_at', time.time())
                    created_time = time.time() - conn_info.get('created_at', time.time())
                    
                    # 连接已使用太久 或 总生命周期过长，直接关闭
                    if usage_time > max_usage_time or created_time > self._connection_lifetime:
                        self._close_connection_safely(connection)
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
                        self._close_connection_safely(connection)
                else:
                    self._close_connection_safely(connection)
            except Exception as e:
                logger.error(f"释放连接过程中出错: {str(e)}")
    
    def execute_async(self, sql, params=None):
        """异步执行SQL查询，返回Future"""
        return self._thread_pool.submit(self._execute_query, sql, params)
        
    def _execute_query(self, sql, params=None):
        """内部方法：执行SQL查询并返回结果"""
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
        """批量异步执行多个查询，返回Future列表
        
        参数:
            queries: 包含SQL查询的列表，每项是(sql, params)元组
            
        返回:
            包含Future对象的列表
        """
        futures = []
        for sql, params in queries:
            future = self.execute_async(sql, params)
            futures.append(future)
        return futures
    
    def _maintain_pool(self):
        """维护连接池，定期检查并处理过期连接"""
        pool_status_interval = DB_CONFIG.get('pool_status_interval', 180)  # 池状态记录间隔(秒)
        last_pool_status_time = 0  # 上次记录池状态的时间
        
        while True:
            sleep_interval = DB_CONFIG.get('pool_maintenance_interval', 15)  # 维护间隔(秒)
            time.sleep(sleep_interval)  
            
            try:
                with self._lock:
                    current_time = time.time()
                    
                    # 检查并清理池中过期的连接
                    self._cleanup_expired_connections(current_time)
                    
                    # 确保保持最小连接数
                    self._ensure_min_connections()
                    
                    # 检查并处理长时间未释放的连接
                    self._check_long_running_connections(current_time)
                    
                    # 定期执行垃圾回收
                    if current_time - self._last_gc_time > self._gc_interval:
                        collected = gc.collect()
                        self._last_gc_time = current_time
                    
                    # 每几分钟记录一次连接池状态
                    if current_time - last_pool_status_time > pool_status_interval:
                        last_pool_status_time = current_time
            except Exception as e:
                error_msg = f"连接池维护过程中发生错误: {str(e)}"
                logger.error(error_msg)
                add_framework_log(f"数据库连接池错误：{error_msg}")
                
                # 尝试进行垃圾回收
                try:
                    gc.collect()
                except:
                    pass
    
    def _cleanup_expired_connections(self, current_time):
        """清理过期连接"""
        for i in range(len(self._pool) - 1, -1, -1):
            if i >= len(self._pool):
                continue  # 防止索引越界
                
            conn_info = self._pool[i]
            # 超过超时时间且池中连接数大于最小值，关闭连接
            if ((current_time - conn_info['last_used'] > self._timeout and 
                len(self._pool) > self._min_connections) or
                current_time - conn_info['created_at'] > self._connection_lifetime):
                try:
                    conn_info['connection'].close()
                    del self._pool[i]
                except Exception as e:
                    try:
                        del self._pool[i]
                    except IndexError:
                        pass  # 防止并发修改导致的问题
    
    def _ensure_min_connections(self):
        """确保维持最小连接数"""
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
    
    def _check_long_running_connections(self, current_time):
        """检查长时间运行的连接"""
        long_query_warning_time = DB_CONFIG.get('long_query_warning_time', 60)  # 默认60秒
        
        for conn_id in list(self._busy_connections.keys()):
            conn_info = self._busy_connections[conn_id]
            # 连接使用超过指定时间视为异常
            if current_time - conn_info['acquired_at'] > long_query_warning_time:
                warning_msg = f"警告: 连接ID {conn_id} 已使用超过{long_query_warning_time}秒，可能存在未释放的情况"
                logger.warning(warning_msg)
                add_framework_log(f"数据库连接池：{warning_msg}")

    # 添加异步执行方法
    async def execute_async_db(self, sql, params=None, fetch_all=False):
        """异步执行SQL查询"""
        future = self.execute_async(sql, params, fetch_all)
        
        # 创建异步任务等待Future完成
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, 
            lambda: future.result(timeout=POOL_CONFIG['request_timeout'])
        )
        return result
    
    async def batch_execute_async_db(self, queries):
        """批量异步执行SQL查询"""
        futures = self.batch_execute_async(queries)
        
        # 创建异步任务等待所有Future完成
        loop = asyncio.get_event_loop()
        results = []
        
        for future in futures:
            try:
                result = await loop.run_in_executor(
                    None, 
                    lambda f=future: f.result(timeout=POOL_CONFIG['request_timeout'])
                )
                results.append(result)
            except Exception as e:
                logger.error(f"批量异步查询出错: {str(e)}")
                results.append(None)
                
        return results

class ConnectionManager:
    """数据库连接管理器，提供便捷的上下文管理接口"""
    
    def __init__(self):
        self.pool = DatabasePool()
        self.connection = None
        self.cursor = None
    
    def __enter__(self):
        # 尝试获取连接，如果立即获取不到则进行有限次数重试
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
                    
            # 如果这不是最后一次尝试，则等待后重试
            if i < retry_count - 1:
                logger.warning(f"尝试获取数据库连接失败，{retry_interval}秒后重试 ({i+1}/{retry_count})")
                time.sleep(retry_interval)
                retry_interval *= 1.5  # 略微增加重试间隔
        
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

# 新增：异步数据库操作函数
def execute_query_async(sql, params=None, fetchall=False):
    """异步执行查询SQL并返回Future"""
    def query_func():
        return execute_query(sql, params, fetchall)
        
    return db_pool._thread_pool.submit(query_func)

def execute_update_async(sql, params=None):
    """异步执行更新SQL并提交，返回Future"""
    def update_func():
        return execute_update(sql, params)
        
    return db_pool._thread_pool.submit(update_func)

def execute_concurrent_queries(query_list):
    """并发执行多个查询，返回结果列表
    
    参数:
        query_list: 包含(sql, params, fetchall)元组的列表
        
    返回:
        查询结果列表，按输入顺序排列
    """
    futures = []
    
    # 提交所有查询任务
    for query in query_list:
        sql, params = query[0], query[1]
        fetchall = query[2] if len(query) > 2 else False
        
        future = execute_query_async(sql, params, fetchall)
        futures.append(future)
    
    # 等待所有任务完成并收集结果
    results = []
    request_timeout = POOL_CONFIG['request_timeout']
    for future in futures:
        try:
            result = future.result(timeout=request_timeout)  # 设置每个查询的超时时间
            results.append(result)
        except Exception as e:
            logger.error(f"并发查询出错: {str(e)}")
            results.append(None)
    
    return results

class DatabaseService:
    """数据库服务类，为插件提供基础数据库操作接口"""
    
    @staticmethod
    def execute_query(sql, params=None, fetch_all=False):
        """执行查询SQL并返回结果"""
        return execute_query(sql, params, fetch_all)
    
    @staticmethod
    def execute_update(sql, params=None):
        """执行更新SQL并提交"""
        return execute_update(sql, params)
    
    @staticmethod
    def execute_transaction(operations):
        """执行事务，包含多条SQL语句"""
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
        """异步执行查询SQL并返回Future"""
        return execute_query_async(sql, params, fetch_all)
    
    @staticmethod
    def execute_update_async(sql, params=None):
        """异步执行更新SQL并提交，返回Future"""
        return execute_update_async(sql, params)
    
    @staticmethod
    def execute_concurrent_queries(query_list):
        """并发执行多个查询，返回结果列表"""
        return execute_concurrent_queries(query_list)
        
    @staticmethod
    async def execute_query_await(sql, params=None, fetch_all=False):
        """异步执行查询SQL并等待结果(使用await)"""
        return await db_pool.execute_async_db(sql, params, fetch_all)
    
    @staticmethod
    async def execute_update_await(sql, params=None):
        """异步执行更新SQL并等待结果(使用await)"""
        future = execute_update_async(sql, params)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, 
            lambda: future.result(timeout=POOL_CONFIG['request_timeout'])
        )
    
    @staticmethod
    async def execute_concurrent_queries_await(query_list):
        """异步并发执行多个查询并等待所有结果(使用await)"""
        return await db_pool.batch_execute_async_db(query_list) 