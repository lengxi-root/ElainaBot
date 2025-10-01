#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time, logging, threading, asyncio, httpx, atexit
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
    try:
        parsed = urlparse(url)
        sanitized_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            sanitized_url += f"?{parsed.query}"
        if parsed.fragment:
            sanitized_url += f"#{parsed.fragment}"
        return sanitized_url
    except:
        return url.replace('\n', '%0A').replace('\r', '%0D').replace('\t', '%09')

class HttpxPoolManager:
    _instance = None
    _lock = threading.RLock()
    
    @classmethod
    def get_instance(cls, **kwargs):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance
    
    def __init__(self, max_connections: int = DEFAULT_CONFIG["MAX_CONNECTIONS"], max_keepalive: int = DEFAULT_CONFIG["MAX_KEEPALIVE"],
                 keepalive_expiry: float = DEFAULT_CONFIG["KEEPALIVE_EXPIRY"], timeout: float = DEFAULT_CONFIG["TIMEOUT"],
                 verify: bool = DEFAULT_CONFIG["VERIFY_SSL"], rebuild_interval: int = DEFAULT_CONFIG["REBUILD_INTERVAL"], **kwargs):
        self.max_connections = max_connections
        self.max_keepalive_connections = max_keepalive
        self.limits = httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_keepalive, keepalive_expiry=keepalive_expiry)
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
        return {'timeout': self.timeout, 'limits': httpx.Limits(max_connections=self.max_connections, max_keepalive_connections=self.max_keepalive_connections)}

    def _build_sync_client(self):
        self._close_sync_client()
        self._sync_client = httpx.Client(**self._build_client_config())
        self._sync_client_creation_time = time.time()
        
    def _build_async_client(self):
        if self._async_client and not self._async_client.is_closed:
            asyncio.create_task(self._async_client.aclose())
        self._async_client = httpx.AsyncClient(**self._build_client_config())
        self._async_client_creation_time = time.time()
        
    def _close_sync_client(self):
        if self._sync_client:
            self._sync_client.close()
            self._sync_client = None
            
    async def _close_async_client(self):
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()
            self._async_client = None

    def _safe_close_async_client(self):
        if self._async_client and not self._async_client.is_closed:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._close_async_client())
                else:
                    loop.run_until_complete(self._close_async_client())
            except:
                self._async_client = None
    
    @contextmanager
    def sync_request_context(self, url=None):
        client = self.get_sync_client()
        try:
            yield client
        except Exception as e:
            raise
    
    @asynccontextmanager
    async def async_request_context(self, url=None):
        client = await self.get_async_client()
        try:
            yield client
        except Exception as e:
            raise
    
    def close(self):
        self._close_sync_client()
        self._safe_close_async_client()
        
    def cleanup(self):
        self._close_sync_client()
        self._safe_close_async_client()
                
    def get_sync_client(self) -> httpx.Client:
        with self._sync_lock:
            if self._sync_client is None:
                self._build_sync_client()
            return self._sync_client
    
    async def get_async_client(self) -> httpx.AsyncClient:
        with self._async_lock:
            if self._async_client is None:
                self._build_async_client()
            return self._async_client

_pool_manager = None

def get_pool_manager(**kwargs) -> HttpxPoolManager:
    return HttpxPoolManager.get_instance(**kwargs)

def _process_json_kwargs(kwargs):
    if 'json' in kwargs:
        import json
        json_data = kwargs.pop('json')
        kwargs['content'] = json.dumps(json_data).encode('utf-8')
        if 'headers' not in kwargs:
            kwargs['headers'] = {}
        if 'Content-Type' not in kwargs['headers'] and 'content-type' not in kwargs['headers']:
            kwargs['headers']['Content-Type'] = 'application/json'
    return kwargs

def _make_sync_request(method: str, url: str, **kwargs) -> httpx.Response:
    url = _sanitize_url(url)
    kwargs = _process_json_kwargs(kwargs)
    kwargs.pop('verify', None)
    try:
        pool = get_pool_manager()
        with pool.sync_request_context(url=url) as client:
            return getattr(client, method.lower())(url, **kwargs)
    except httpx.HTTPStatusError as e:
        raise e
    except Exception as e:
        raise e

async def _make_async_request(method: str, url: str, **kwargs) -> httpx.Response:
    url = _sanitize_url(url)
    kwargs = _process_json_kwargs(kwargs)
    kwargs.pop('verify', None)
    pool = get_pool_manager()
    async with pool.async_request_context(url=url) as client:
        return await getattr(client, method.lower())(url, **kwargs)

def sync_get(url: str, **kwargs) -> httpx.Response:
    return _make_sync_request('GET', url, **kwargs)

def sync_post(url: str, **kwargs) -> httpx.Response:
    return _make_sync_request('POST', url, **kwargs)

def sync_delete(url: str, **kwargs) -> httpx.Response:
    return _make_sync_request('DELETE', url, **kwargs)

async def async_get(url: str, **kwargs) -> httpx.Response:
    return await _make_async_request('GET', url, **kwargs)

async def async_post(url: str, **kwargs) -> httpx.Response:
    return await _make_async_request('POST', url, **kwargs)

async def async_delete(url: str, **kwargs) -> httpx.Response:
    return await _make_async_request('DELETE', url, **kwargs)

def _make_json_request(request_func, url: str, **kwargs) -> Union[Dict, list]:
    return request_func(url, **kwargs).json()

async def _make_async_json_request(request_func, url: str, **kwargs) -> Union[Dict, list]:
    response = await request_func(url, **kwargs)
    return response.json()

def get_json(url: str, **kwargs) -> Union[Dict, list]:
    return _make_json_request(sync_get, url, **kwargs)

def post_json(url: str, **kwargs) -> Union[Dict, list]:
    return _make_json_request(sync_post, url, **kwargs)

def delete_json(url: str, **kwargs) -> Union[Dict, list]:
    return _make_json_request(sync_delete, url, **kwargs)

async def async_get_json(url: str, **kwargs) -> Union[Dict, list]:
    return await _make_async_json_request(async_get, url, **kwargs)

async def async_post_json(url: str, **kwargs) -> Union[Dict, list]:
    return await _make_async_json_request(async_post, url, **kwargs)

async def async_delete_json(url: str, **kwargs) -> Union[Dict, list]:
    return await _make_async_json_request(async_delete, url, **kwargs)

def get_binary_content(url: str, **kwargs) -> bytes:
    return sync_get(url, **kwargs).content

def run_async(coroutine):
    try:
        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        if current_loop is not None:
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
        raise

atexit.register(get_pool_manager().close) 