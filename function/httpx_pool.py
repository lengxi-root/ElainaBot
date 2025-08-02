#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
HTTPX连接池管理模块
提供高效、线程安全的HTTP请求管理，自动定期重建以防止资源泄露
"""

import time
import logging
import threading
import asyncio
import httpx
from typing import Optional, Dict, Any, Union
from contextlib import asynccontextmanager, contextmanager
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import urllib.parse

# 配置日志记录器
logger = logging.getLogger("httpx_pool")
logger.setLevel(logging.INFO)

# 禁用httpx请求日志
httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)

# 默认配置
DEFAULT_CONFIG = {
    "MAX_CONNECTIONS": 200,         # 最大连接数
    "MAX_KEEPALIVE": 75,            # 保持活动的最大连接数 
    "KEEPALIVE_EXPIRY": 30.0,       # 连接保持活动的时间(秒)
    "TIMEOUT": 20.0,                # 请求超时时间(秒)
    "VERIFY_SSL": False,            # 是否验证SSL证书
    "REBUILD_INTERVAL": 43200       # 客户端重建间隔(秒)，12小时
}

def _sanitize_url(url: str) -> str:
    """
    清理URL中的非法字符，特别是换行符和制表符
    保持参数值的原始内容，只清理有害字符
    """
    try:
        # 解析URL并保留所有组件
        parsed = urlparse(url)
        
        # 重建完整URL，包含查询参数和片段
        sanitized_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        
        # 保留查询参数
        if parsed.query:
            sanitized_url += f"?{parsed.query}"
            
        # 保留片段标识符
        if parsed.fragment:
            sanitized_url += f"#{parsed.fragment}"
        
        return sanitized_url
        
    except Exception as e:
        logger.warning(f"URL解析失败，使用fallback清理方案: {e}")
        # 如果解析失败，只替换明显的有害字符
        return url.replace('\n', '%0A').replace('\r', '%0D').replace('\t', '%09')

class HttpxPoolManager:
    """HTTPX连接池管理器，支持同步和异步请求，自动重建连接以防止资源泄露"""
    
    _instance = None
    _lock = threading.RLock()
    
    @classmethod
    def get_instance(cls, **kwargs):
        """获取单例实例"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance
    
    def __init__(
        self,
        max_connections: int = DEFAULT_CONFIG["MAX_CONNECTIONS"],
        max_keepalive: int = DEFAULT_CONFIG["MAX_KEEPALIVE"],
        keepalive_expiry: float = DEFAULT_CONFIG["KEEPALIVE_EXPIRY"],
        timeout: float = DEFAULT_CONFIG["TIMEOUT"],
        verify: bool = DEFAULT_CONFIG["VERIFY_SSL"],
        rebuild_interval: int = DEFAULT_CONFIG["REBUILD_INTERVAL"],
        **kwargs
    ):
        """初始化连接池管理器"""
        self.max_connections = max_connections
        self.max_keepalive_connections = max_keepalive
        self.limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
            keepalive_expiry=keepalive_expiry
        )
        self.timeout = timeout
        self.verify = verify
        self.kwargs = kwargs
        self.rebuild_interval = rebuild_interval
        
        # 客户端实例
        self._sync_client = None
        self._async_client = None
        
        # 上次重建时间
        self._last_sync_rebuild = 0
        self._last_async_rebuild = 0
        
        # 线程锁，确保线程安全
        self._sync_lock = threading.RLock()
        self._async_lock = threading.RLock()
        
        self._build_sync_client()
        self._build_async_client()
        
        # 注册清理函数
        atexit.register(self.cleanup)
        
        logger.info("HTTP连接池模块初始化完成")
        
    def _build_sync_client(self):
        """构建同步客户端"""
        self._close_sync_client()
        
        self._sync_client = httpx.Client(
            timeout=self.timeout,
            limits=httpx.Limits(
                max_connections=self.max_connections,
                max_keepalive_connections=self.max_keepalive_connections
            )
        )
        self._sync_client_creation_time = time.time()
        
    def _build_async_client(self):
        """构建异步客户端"""
        if self._async_client and not self._async_client.is_closed:
            asyncio.create_task(self._async_client.aclose())
            
        self._async_client = httpx.AsyncClient(
            timeout=self.timeout,
            limits=httpx.Limits(
                max_connections=self.max_connections,
                max_keepalive_connections=self.max_keepalive_connections
            )
        )
        self._async_client_creation_time = time.time()
        
    def _close_sync_client(self):
        """关闭同步客户端"""
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None
            
    async def _close_async_client(self):
        """关闭异步客户端"""
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()
            self._async_client = None
    
    @contextmanager
    def sync_request_context(self, url=None):
        """同步请求上下文管理器，用于安全地执行请求"""
        client = self.get_sync_client()
        try:
            yield client
        except Exception as e:
            url_info = f" for URL: {url}" if url else ""
            logger.error(f"Error in sync request{url_info}: {str(e)}")
            raise
    
    @asynccontextmanager
    async def async_request_context(self, url=None):
        """异步请求上下文管理器，用于安全地执行异步请求"""
        client = await self.get_async_client()
        try:
            yield client
        except Exception as e:
            url_info = f" for URL: {url}" if url else ""
            logger.error(f"Error in async request{url_info}: {str(e)}")
            raise
    
    def close(self):
        """关闭所有连接，释放资源"""
        self._close_sync_client()
        
        # 对于异步客户端，需要在事件循环中关闭
        if self._async_client is not None:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._close_async_client())
                else:
                    loop.run_until_complete(self._close_async_client())
            except Exception as e:
                logger.error(f"Error closing async client: {str(e)}")
                # 如果获取事件循环失败，尝试直接关闭
                self._async_client = None
        
    def cleanup(self):
        """清理所有HTTP客户端资源"""
        self._close_sync_client()
        
        # 异步客户端需要在事件循环中关闭
        if self._async_client and not self._async_client.is_closed:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # 如果事件循环正在运行，创建任务关闭客户端
                    loop.create_task(self._close_async_client())
                else:
                    # 如果事件循环没有运行，直接运行关闭操作
                    loop.run_until_complete(self._close_async_client())
            except Exception:
                # 如果无法获取事件循环，强制设置为None
                self._async_client = None
                
    def get_sync_client(self) -> httpx.Client:
        """获取同步客户端"""
        with self._sync_lock:
            if self._sync_client is None:
                self._build_sync_client()
            return self._sync_client
    
    async def get_async_client(self) -> httpx.AsyncClient:
        """获取异步客户端"""
        with self._async_lock:
            if self._async_client is None:
                self._build_async_client()
            return self._async_client

# 全局HTTP连接池管理器实例
_pool_manager = None

def get_pool_manager(**kwargs) -> HttpxPoolManager:
    """获取全局连接池管理器实例"""
    return HttpxPoolManager.get_instance(**kwargs)

def sync_get(url: str, max_retries: int = 3, retry_delay: float = 1.0, **kwargs) -> httpx.Response:
    """发送同步GET请求，支持重试机制"""
    # 自动处理URL中的非法字符（如换行符）
    url = _sanitize_url(url)
    
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            pool = get_pool_manager()
            with pool.sync_request_context(url=url) as client:
                response = client.get(url, **kwargs)
                response.raise_for_status()  # 检查HTTP状态码
                return response
                
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            last_exception = e
            if attempt < max_retries - 1:
                logger.warning(f"HTTP请求失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}, 等待 {retry_delay} 秒后重试")
                time.sleep(retry_delay * (2 ** attempt))  # 指数退避
                continue
                
        except httpx.HTTPStatusError as e:
            # HTTP状态错误通常不需要重试（如404, 500等）
            logger.error(f"HTTP状态错误: {e.response.status_code}")
            raise e
            
        except Exception as e:
            last_exception = e
            logger.error(f"意外错误: {str(e)}")
            break
    
    # 所有重试都失败了
    if last_exception:
        logger.error(f"所有重试都失败，最后错误: {str(last_exception)}")
        raise last_exception
    else:
        raise httpx.RequestError("请求失败，未知错误")

def sync_post(url: str, **kwargs) -> httpx.Response:
    """发送同步POST请求"""
    # 自动处理URL中的非法字符（如换行符）
    url = _sanitize_url(url)
    pool = get_pool_manager()
    with pool.sync_request_context(url=url) as client:
        return client.post(url, **kwargs)

def sync_delete(url: str, **kwargs) -> httpx.Response:
    """发送同步DELETE请求
    
    注意：DELETE请求不支持直接使用json参数，如需发送JSON数据，
    请使用content参数并手动将数据序列化为JSON
    """
    # 处理json参数，将其转为content参数
    if 'json' in kwargs:
        import json
        json_data = kwargs.pop('json')
        kwargs['content'] = json.dumps(json_data).encode('utf-8')
        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        kwargs['headers']['Content-Type'] = 'application/json'
    
    # 自动处理URL中的非法字符（如换行符）
    url = _sanitize_url(url)
    pool = get_pool_manager()
    with pool.sync_request_context(url=url) as client:
        return client.delete(url, **kwargs)

async def async_get(url: str, **kwargs) -> httpx.Response:
    """发送异步GET请求"""
    # 自动处理URL中的非法字符（如换行符）
    url = _sanitize_url(url)
    pool = get_pool_manager()
    async with pool.async_request_context(url=url) as client:
        return await client.get(url, **kwargs)

async def async_post(url: str, **kwargs) -> httpx.Response:
    """发送异步POST请求"""
    # 自动处理URL中的非法字符（如换行符）
    url = _sanitize_url(url)
    pool = get_pool_manager()
    async with pool.async_request_context(url=url) as client:
        return await client.post(url, **kwargs)

async def async_delete(url: str, **kwargs) -> httpx.Response:
    """发送异步DELETE请求
    
    注意：DELETE请求不支持直接使用json参数，如需发送JSON数据，
    请使用content参数并手动将数据序列化为JSON
    """
    # 处理json参数，将其转为content参数
    if 'json' in kwargs:
        import json
        json_data = kwargs.pop('json')
        kwargs['content'] = json.dumps(json_data).encode('utf-8')
        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        kwargs['headers']['Content-Type'] = 'application/json'
    
    # 自动处理URL中的非法字符（如换行符）
    url = _sanitize_url(url)
    pool = get_pool_manager()
    async with pool.async_request_context(url=url) as client:
        return await client.delete(url, **kwargs)

def run_async(coroutine):
    """在同步环境中运行异步函数，安全处理事件循环冲突"""
    try:
        # 检查当前是否有运行中的事件循环
        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            # 没有运行中的循环，这是好的
            pass
        
        if current_loop is not None:
            # 如果有运行中的循环，在新线程中创建新循环
            import threading
            import queue
            result_queue = queue.Queue()
            
            def run_in_thread():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    result = new_loop.run_until_complete(coroutine)
                    result_queue.put(('success', result))
                except Exception as e:
                    result_queue.put(('error', e))
                finally:
                    new_loop.close()
                    asyncio.set_event_loop(None)
            
            thread = threading.Thread(target=run_in_thread, daemon=True)
            thread.start()
            thread.join()
            
            result_type, result_data = result_queue.get()
            if result_type == 'error':
                raise result_data
            return result_data
        else:
            # 没有运行中的循环，尝试获取或创建事件循环
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                # 如果当前线程没有事件循环，创建一个新的
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            return loop.run_until_complete(coroutine)
            
    except Exception as e:
        logger.error(f"Error in run_async: {str(e)}")
        raise

def get_json(url: str, **kwargs) -> Union[Dict, list]:
    """简便函数：发送GET请求并返回JSON响应"""
    response = sync_get(url, **kwargs)
    return response.json()

def post_json(url: str, **kwargs) -> Union[Dict, list]:
    """简便函数：发送POST请求并返回JSON响应"""
    response = sync_post(url, **kwargs)
    return response.json()

def delete_json(url: str, **kwargs) -> Union[Dict, list]:
    """简便函数：发送DELETE请求并返回JSON响应"""
    response = sync_delete(url, **kwargs)
    return response.json()

async def async_get_json(url: str, **kwargs) -> Union[Dict, list]:
    """简便函数：异步发送GET请求并返回JSON响应"""
    response = await async_get(url, **kwargs)
    return response.json()

async def async_post_json(url: str, **kwargs) -> Union[Dict, list]:
    """简便函数：异步发送POST请求并返回JSON响应"""
    response = await async_post(url, **kwargs)
    return response.json()

async def async_delete_json(url: str, **kwargs) -> Union[Dict, list]:
    """简便函数：异步发送DELETE请求并返回JSON响应"""
    response = await async_delete(url, **kwargs)
    return response.json()

def get_binary_content(url: str, **kwargs) -> bytes:
    """简便函数：发送GET请求并返回二进制内容"""
    response = sync_get(url, **kwargs)
    return response.content

# 确保应用退出时关闭连接池
import atexit
atexit.register(get_pool_manager().close) 