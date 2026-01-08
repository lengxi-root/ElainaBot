#!/usr/bin/env python
# -*- coding: utf-8 -*-

import time, logging, threading, asyncio, httpx, atexit, json
from urllib.parse import urlparse

logger = logging.getLogger("ElainaBot.function.httpx_pool")
logger.setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

_MAX_CONNECTIONS = 200
_MAX_KEEPALIVE = 75
_KEEPALIVE_EXPIRY = 30.0
_TIMEOUT = 30.0
_JSON_CONTENT_TYPE = 'application/json'
_CONTENT_TYPE_KEYS = frozenset({'Content-Type', 'content-type'})

def _sanitize_url(url):
    try:
        p = urlparse(url)
        result = f"{p.scheme}://{p.netloc}{p.path}"
        if p.query:
            result += f"?{p.query}"
        if p.fragment:
            result += f"#{p.fragment}"
        return result
    except:
        return url.replace('\n', '%0A').replace('\r', '%0D').replace('\t', '%09')

class HttpxPoolManager:
    __slots__ = ('_limits', '_timeout', '_sync_client', '_async_client', '_sync_lock', '_async_lock')
    _instance = None
    _init_lock = threading.RLock()
    
    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        self._limits = httpx.Limits(max_connections=_MAX_CONNECTIONS, max_keepalive_connections=_MAX_KEEPALIVE, keepalive_expiry=_KEEPALIVE_EXPIRY)
        self._timeout = _TIMEOUT
        self._sync_client = None
        self._async_client = None
        self._sync_lock = threading.RLock()
        self._async_lock = threading.RLock()
        self._build_sync_client()
        self._build_async_client()
        atexit.register(self.cleanup)

    def _build_sync_client(self):
        if self._sync_client:
            try:
                self._sync_client.close()
            except:
                pass
        self._sync_client = httpx.Client(timeout=self._timeout, limits=self._limits)
        
    def _build_async_client(self):
        if self._async_client and not self._async_client.is_closed:
            try:
                asyncio.create_task(self._async_client.aclose())
            except:
                pass
        self._async_client = httpx.AsyncClient(timeout=self._timeout, limits=self._limits)
    
    def get_sync_client(self):
        with self._sync_lock:
            if self._sync_client is None:
                self._build_sync_client()
            return self._sync_client
    
    async def get_async_client(self):
        with self._async_lock:
            if self._async_client is None:
                self._build_async_client()
            return self._async_client
        
    def cleanup(self):
        if self._sync_client:
            try:
                self._sync_client.close()
            except:
                pass
            self._sync_client = None
        if self._async_client and not self._async_client.is_closed:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self._async_client.aclose())
                else:
                    loop.run_until_complete(self._async_client.aclose())
            except:
                pass
            self._async_client = None

_pool = None

def get_pool_manager():
    global _pool
    if _pool is None:
        _pool = HttpxPoolManager.get_instance()
    return _pool

def _process_json_kwargs(kwargs):
    if 'json' in kwargs:
        json_data = kwargs.pop('json')
        kwargs['content'] = json.dumps(json_data).encode('utf-8')
        headers = kwargs.get('headers')
        if headers is None:
            kwargs['headers'] = {'Content-Type': _JSON_CONTENT_TYPE}
        elif not (_CONTENT_TYPE_KEYS & set(headers.keys())):
            headers['Content-Type'] = _JSON_CONTENT_TYPE
    kwargs.pop('verify', None)
    return kwargs

def _make_sync_request(method, url, **kwargs):
    url = _sanitize_url(url)
    kwargs = _process_json_kwargs(kwargs)
    client = get_pool_manager().get_sync_client()
    return getattr(client, method)(url, **kwargs)

async def _make_async_request(method, url, **kwargs):
    url = _sanitize_url(url)
    kwargs = _process_json_kwargs(kwargs)
    client = await get_pool_manager().get_async_client()
    return await getattr(client, method)(url, **kwargs)

def sync_get(url, **kwargs):
    return _make_sync_request('get', url, **kwargs)

def sync_post(url, **kwargs):
    return _make_sync_request('post', url, **kwargs)

def sync_delete(url, **kwargs):
    return _make_sync_request('delete', url, **kwargs)

async def async_get(url, **kwargs):
    return await _make_async_request('get', url, **kwargs)

async def async_post(url, **kwargs):
    return await _make_async_request('post', url, **kwargs)

async def async_delete(url, **kwargs):
    return await _make_async_request('delete', url, **kwargs)

def get_json(url, **kwargs):
    return sync_get(url, **kwargs).json()

def post_json(url, **kwargs):
    return sync_post(url, **kwargs).json()

def delete_json(url, **kwargs):
    return sync_delete(url, **kwargs).json()

async def async_get_json(url, **kwargs):
    return (await async_get(url, **kwargs)).json()

async def async_post_json(url, **kwargs):
    return (await async_post(url, **kwargs)).json()

async def async_delete_json(url, **kwargs):
    return (await async_delete(url, **kwargs)).json()

def get_binary_content(url, **kwargs):
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

atexit.register(get_pool_manager().cleanup)
