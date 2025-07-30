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

# 优化的默认配置，提升性能
DEFAULT_CONFIG = {
    "MAX_CONNECTIONS": 100,         # 适度降低最大连接数，减少资源占用
    "MAX_KEEPALIVE": 50,            # 保持活动的连接数，提升复用率
    "KEEPALIVE_EXPIRY": 60.0,       # 增加连接保持时间，减少重建频率
    "TIMEOUT": 15.0,                # 略微减少超时时间，提升响应速度
    "VERIFY_SSL": False,            # 是否验证SSL证书
    "REBUILD_INTERVAL": 21600       # 6小时重建一次，平衡性能和稳定性
}

def _sanitize_url(url: str) -> str:
    """
    清理URL中的非法字符，特别是换行符和制表符
    自动对URL参数进行正确编码，保持参数值的原始内容
    """
    try:
        # 分离URL的基础部分和查询参数部分
        if '?' in url:
            base_url, query_string = url.split('?', 1)
            # 清理基础URL部分的换行符
            clean_base_url = base_url.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
            
            # 手动解析查询参数，保持参数值的原始内容（包括换行符）
            params = []
            if query_string:
                # 分割参数对
                for param_pair in query_string.split('&'):
                    if '=' in param_pair:
                        key, value = param_pair.split('=', 1)
                        # 对key和value分别进行URL编码，保持换行符等特殊字符
                        encoded_key = urllib.parse.quote(key, safe='')
                        encoded_value = urllib.parse.quote(value, safe='')
                        params.append(f"{encoded_key}={encoded_value}")
                    else:
                        # 没有等号的参数
                        encoded_param = urllib.parse.quote(param_pair, safe='')
                        params.append(encoded_param)
            
            # 重新构建URL
            if params:
                sanitized_url = f"{clean_base_url}?{'&'.join(params)}"
            else:
                sanitized_url = clean_base_url
        else:
            # 没有查询参数，只清理基础URL
            sanitized_url = url.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
            
        logger.debug(f"URL清理: 原始长度{len(url)} -> 清理后长度{len(sanitized_url)}")
        return sanitized_url
        
    except Exception as e:
        logger.warning(f"URL清理失败，使用fallback方案: {e}")
        # 如果解析失败，使用简单的替换方案
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
        
        logger.info(f"HttpxPoolManager initialized with max_connections={max_connections}, " 
                    f"max_keepalive={max_keepalive}, rebuild_interval={rebuild_interval}s")
    
    def get_sync_client(self) -> httpx.Client:
        """获取同步客户端，如果需要则重建"""
        with self._sync_lock:
            current_time = time.time()
            
            # 检查是否需要重建客户端
            if self._sync_client is None or (current_time - self._last_sync_rebuild) > self.rebuild_interval:
                self._close_sync_client()
                self._sync_client = httpx.Client(
                    limits=self.limits,
                    timeout=self.timeout,
                    verify=self.verify,
                    **self.kwargs
                )
                self._last_sync_rebuild = current_time
                logger.info(f"Sync client rebuilt at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))}")
                
            return self._sync_client
    
    async def get_async_client(self) -> httpx.AsyncClient:
        """获取异步客户端，如果需要则重建"""
        with self._async_lock:
            current_time = time.time()
            
            # 检查是否需要重建客户端
            if self._async_client is None or (current_time - self._last_async_rebuild) > self.rebuild_interval:
                await self._close_async_client()
                self._async_client = httpx.AsyncClient(
                    limits=self.limits,
                    timeout=self.timeout,
                    verify=self.verify,
                    **self.kwargs
                )
                self._last_async_rebuild = current_time
                logger.info(f"Async client rebuilt at {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(current_time))}")
                
            return self._async_client
    
    def _close_sync_client(self):
        """关闭同步客户端"""
        if self._sync_client is not None:
            try:
                self._sync_client.close()
                logger.debug("Sync client closed")
            except Exception as e:
                logger.error(f"Error closing sync client: {str(e)}")
            finally:
                self._sync_client = None
    
    async def _close_async_client(self):
        """关闭异步客户端"""
        if self._async_client is not None:
            try:
                await self._async_client.aclose()
                logger.debug("Async client closed")
            except Exception as e:
                logger.error(f"Error closing async client: {str(e)}")
            finally:
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
        
        logger.info("All HTTP clients closed and resources released")

# 全局函数

def get_pool_manager(**kwargs) -> HttpxPoolManager:
    """获取全局连接池管理器实例"""
    return HttpxPoolManager.get_instance(**kwargs)

def sync_get(url: str, **kwargs) -> httpx.Response:
    """发送同步GET请求"""
    # 自动处理URL中的非法字符（如换行符）
    url = _sanitize_url(url)
    pool = get_pool_manager()
    with pool.sync_request_context(url=url) as client:
        return client.get(url, **kwargs)

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
    """在同步环境中运行异步函数"""
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # 如果当前线程没有事件循环，创建一个新的
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    try:
        return loop.run_until_complete(coroutine)
    finally:
        # 不关闭事件循环，可能会被其他地方使用
        pass

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

# 使用示例
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    # 同步请求示例
    response = sync_get("https://httpbin.org/get")
    print(f"Sync response status: {response.status_code}")
    print(response.json())
    
    # 异步请求示例
    async def test_async():
        response = await async_get("https://httpbin.org/get")
        print(f"Async response status: {response.status_code}")
        print(response.json())
    
    run_async(test_async()) 