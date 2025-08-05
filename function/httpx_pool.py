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
import atexit
from typing import Optional, Dict, Any, Union
from contextlib import asynccontextmanager, contextmanager
from urllib.parse import urlparse

logger = logging.getLogger("httpx_pool")
logger.setLevel(logging.INFO)

httpx_logger = logging.getLogger("httpx")
httpx_logger.setLevel(logging.WARNING)

DEFAULT_CONFIG = {
    "MAX_CONNECTIONS": 200,
    "MAX_KEEPALIVE": 75,
    "KEEPALIVE_EXPIRY": 30.0,
    "TIMEOUT": 30.0,
    "VERIFY_SSL": False,
    "REBUILD_INTERVAL": 43200
}

def _sanitize_url(url: str) -> str:
    """清理URL中的非法字符"""
    try:
        parsed = urlparse(url)
        sanitized_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        
        if parsed.query:
            sanitized_url += f"?{parsed.query}"
        if parsed.fragment:
            sanitized_url += f"#{parsed.fragment}"
        
        return sanitized_url
        
    except Exception as e:
        logger.warning(f"URL解析失败: {e}")
        return url.replace('\n', '%0A').replace('\r', '%0D').replace('\t', '%09')

class HttpxPoolManager:
    """HTTP连接池管理器"""
    
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
        
        self._sync_client = None
        self._async_client = None
        self._last_sync_rebuild = 0
        self._last_async_rebuild = 0
        self._sync_lock = threading.RLock()
        self._async_lock = threading.RLock()
        
        self._build_sync_client()
        self._build_async_client()
        
        atexit.register(self.cleanup)

    def _build_client_config(self):
        """构建客户端配置"""
        return {
            'timeout': self.timeout,
            'limits': httpx.Limits(
                max_connections=self.max_connections,
                max_keepalive_connections=self.max_keepalive_connections
            )
        }

    def _build_sync_client(self):
        """构建同步客户端"""
        self._close_sync_client()
        self._sync_client = httpx.Client(**self._build_client_config())
        self._sync_client_creation_time = time.time()
        
    def _build_async_client(self):
        """构建异步客户端"""
        if self._async_client and not self._async_client.is_closed:
            asyncio.create_task(self._async_client.aclose())
            
        self._async_client = httpx.AsyncClient(**self._build_client_config())
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

    def _safe_close_async_client(self):
        """安全关闭异步客户端"""
        if self._async_client and not self._async_client.is_closed:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._close_async_client())
                else:
                    loop.run_until_complete(self._close_async_client())
            except Exception:
                self._async_client = None
    
    @contextmanager
    def sync_request_context(self, url=None):
        """同步请求上下文管理器"""
        client = self.get_sync_client()
        try:
            yield client
        except Exception as e:
            url_info = f" for URL: {url}" if url else ""
            logger.error(f"Error in sync request{url_info}: {str(e)}")
            raise
    
    @asynccontextmanager
    async def async_request_context(self, url=None):
        """异步请求上下文管理器"""
        client = await self.get_async_client()
        try:
            yield client
        except Exception as e:
            url_info = f" for URL: {url}" if url else ""
            logger.error(f"Error in async request{url_info}: {str(e)}")
            raise
    
    def close(self):
        """关闭所有连接"""
        self._close_sync_client()
        self._safe_close_async_client()
        
    def cleanup(self):
        """清理所有HTTP客户端资源"""
        self._close_sync_client()
        self._safe_close_async_client()
                
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

# 全局连接池管理器
_pool_manager = None

def get_pool_manager(**kwargs) -> HttpxPoolManager:
    """获取全局连接池管理器实例"""
    return HttpxPoolManager.get_instance(**kwargs)

def _process_json_kwargs(kwargs):
    """处理JSON参数"""
    if 'json' in kwargs:
        import json
        json_data = kwargs.pop('json')
        kwargs['content'] = json.dumps(json_data).encode('utf-8')
        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        kwargs['headers']['Content-Type'] = 'application/json'
    return kwargs

def _make_sync_request(method: str, url: str, max_retries: int = 3, retry_delay: float = 1.0, **kwargs) -> httpx.Response:
    """统一的同步请求处理"""
    url = _sanitize_url(url)
    kwargs = _process_json_kwargs(kwargs)
    
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            pool = get_pool_manager()
            with pool.sync_request_context(url=url) as client:
                response = getattr(client, method.lower())(url, **kwargs)
                if method.upper() == 'GET':
                    response.raise_for_status()
                return response
                
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            last_exception = e
            if attempt < max_retries - 1:
                time.sleep(retry_delay * (2 ** attempt))
                continue
                
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP状态错误: {e.response.status_code}")
            raise e
            
        except Exception as e:
            last_exception = e
            logger.error(f"意外错误: {str(e)}")
            break
    
    if last_exception:
        logger.error(f"所有重试都失败: {str(last_exception)}")
        raise last_exception
    else:
        raise httpx.RequestError("请求失败，未知错误")

async def _make_async_request(method: str, url: str, **kwargs) -> httpx.Response:
    """统一的异步请求处理"""
    url = _sanitize_url(url)
    kwargs = _process_json_kwargs(kwargs)
    
    pool = get_pool_manager()
    async with pool.async_request_context(url=url) as client:
        return await getattr(client, method.lower())(url, **kwargs)

def sync_get(url: str, max_retries: int = 3, retry_delay: float = 1.0, **kwargs) -> httpx.Response:
    """发送同步GET请求"""
    return _make_sync_request('GET', url, max_retries, retry_delay, **kwargs)

def sync_post(url: str, **kwargs) -> httpx.Response:
    """发送同步POST请求"""
    return _make_sync_request('POST', url, **kwargs)

def sync_delete(url: str, **kwargs) -> httpx.Response:
    """发送同步DELETE请求"""
    return _make_sync_request('DELETE', url, **kwargs)

async def async_get(url: str, **kwargs) -> httpx.Response:
    """发送异步GET请求"""
    return await _make_async_request('GET', url, **kwargs)

async def async_post(url: str, **kwargs) -> httpx.Response:
    """发送异步POST请求"""
    return await _make_async_request('POST', url, **kwargs)

async def async_delete(url: str, **kwargs) -> httpx.Response:
    """发送异步DELETE请求"""
    return await _make_async_request('DELETE', url, **kwargs)

def _make_json_request(request_func, url: str, **kwargs) -> Union[Dict, list]:
    """统一的JSON响应处理"""
    response = request_func(url, **kwargs)
    return response.json()

async def _make_async_json_request(request_func, url: str, **kwargs) -> Union[Dict, list]:
    """统一的异步JSON响应处理"""
    response = await request_func(url, **kwargs)
    return response.json()

def get_json(url: str, **kwargs) -> Union[Dict, list]:
    """发送GET请求并返回JSON响应"""
    return _make_json_request(sync_get, url, **kwargs)

def post_json(url: str, **kwargs) -> Union[Dict, list]:
    """发送POST请求并返回JSON响应"""
    return _make_json_request(sync_post, url, **kwargs)

def delete_json(url: str, **kwargs) -> Union[Dict, list]:
    """发送DELETE请求并返回JSON响应"""
    return _make_json_request(sync_delete, url, **kwargs)

async def async_get_json(url: str, **kwargs) -> Union[Dict, list]:
    """异步发送GET请求并返回JSON响应"""
    return await _make_async_json_request(async_get, url, **kwargs)

async def async_post_json(url: str, **kwargs) -> Union[Dict, list]:
    """异步发送POST请求并返回JSON响应"""
    return await _make_async_json_request(async_post, url, **kwargs)

async def async_delete_json(url: str, **kwargs) -> Union[Dict, list]:
    """异步发送DELETE请求并返回JSON响应"""
    return await _make_async_json_request(async_delete, url, **kwargs)

def get_binary_content(url: str, **kwargs) -> bytes:
    """发送GET请求并返回二进制内容"""
    response = sync_get(url, **kwargs)
    return response.content

def run_async(coroutine):
    """在同步环境中运行异步函数"""
    try:
        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        
        if current_loop is not None:
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
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            return loop.run_until_complete(coroutine)
            
    except Exception as e:
        logger.error(f"Error in run_async: {str(e)}")
        raise

# 应用退出时关闭连接池
atexit.register(get_pool_manager().close) 