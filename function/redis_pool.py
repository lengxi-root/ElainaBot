#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Redis连接池模块 - 支持热重载

使用方式：
    from function.redis_pool import redis_pool
    
    # 直接使用，可以安全缓存引用
    if redis_pool.is_enabled():
        redis_pool.set('key', 'value')
"""

import sys
import threading
import logging
from typing import Optional, Any
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger('ElainaBot.function.redis_pool')

# ============ 热重载保护机制 ============
# 使用 sys.modules 存储单例，确保模块重载时实例不丢失
_INSTANCE_KEY = '__elaina_redis_pool_singleton__'

def _get_existing_instance():
    """获取已存在的实例"""
    return sys.modules.get(_INSTANCE_KEY)

def _save_instance(instance):
    """保存实例到 sys.modules"""
    sys.modules[_INSTANCE_KEY] = instance
# ========================================

try:
    from config import REDIS_CONFIG
except ImportError:
    REDIS_CONFIG = {'enabled': False}

try:
    from redis import ConnectionPool, Redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

_ENABLED = REDIS_CONFIG.get('enabled', False)
_HOST = REDIS_CONFIG.get('host', '127.0.0.1')
_PORT = REDIS_CONFIG.get('port', 6379)
_PASSWORD = REDIS_CONFIG.get('password', None)
_DB = REDIS_CONFIG.get('db', 0)
_MAX_CONNECTIONS = REDIS_CONFIG.get('max_connections', 50)
_SOCKET_TIMEOUT = REDIS_CONFIG.get('socket_timeout', 5)
_SOCKET_CONNECT_TIMEOUT = REDIS_CONFIG.get('socket_connect_timeout', 5)
_RETRY_ON_TIMEOUT = REDIS_CONFIG.get('retry_on_timeout', True)
_HEALTH_CHECK_INTERVAL = REDIS_CONFIG.get('health_check_interval', 30)
_DECODE_RESPONSES = REDIS_CONFIG.get('decode_responses', True)


class RedisPool:
    """Redis连接池单例类"""
    _instance = None
    _init_lock = threading.Lock()
    _pool: Optional[ConnectionPool] = None
    _client: Optional[Redis] = None
    _initialized = False
    _init_status = None
    _init_message = ''
    _thread_pool = None
    
    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super(RedisPool, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        with self._init_lock:
            if not self._initialized:
                self._initialized = True
                self._do_init()
    
    def _do_init(self):
        if not _ENABLED:
            self._init_status = 'disabled'
            self._init_message = 'Redis连接池已禁用'
            return
        if not REDIS_AVAILABLE:
            self._init_status = 'no_module'
            self._init_message = 'redis模块未安装'
            return
        self._init_pool()
    
    def _init_pool(self):
        try:
            self._pool = ConnectionPool(
                host=_HOST, port=_PORT, password=_PASSWORD, db=_DB,
                max_connections=_MAX_CONNECTIONS, socket_timeout=_SOCKET_TIMEOUT,
                socket_connect_timeout=_SOCKET_CONNECT_TIMEOUT,
                retry_on_timeout=_RETRY_ON_TIMEOUT,
                health_check_interval=_HEALTH_CHECK_INTERVAL,
                decode_responses=_DECODE_RESPONSES
            )
            self._client = Redis(connection_pool=self._pool)
            self._client.ping()
            self._thread_pool = ThreadPoolExecutor(max_workers=20, thread_name_prefix="RedisPool")
            self._init_status = 'success'
            self._init_message = f'Redis连接池初始化成功 [{_HOST}:{_PORT}]'
        except Exception as e:
            self._init_status = 'failed'
            self._init_message = f'Redis连接池初始化失败: {e}'
            self._pool = None
            self._client = None
    
    def get_init_status(self) -> tuple:
        """获取初始化状态"""
        return self._init_status, self._init_message
    
    def is_enabled(self) -> bool:
        """检查Redis是否可用"""
        return _ENABLED and REDIS_AVAILABLE and self._client is not None
    
    def get_client(self) -> Optional[Redis]:
        """获取Redis客户端"""
        return self._client if self.is_enabled() else None
    
    def get_connection(self) -> Optional[Redis]:
        """获取Redis连接"""
        return self.get_client()

    # ==================== 基础操作 ====================
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取值"""
        if not self.is_enabled():
            return default
        try:
            value = self._client.get(key)
            return value if value is not None else default
        except Exception as e:
            logger.error(f"Redis GET失败 [{key}]: {e}")
            return default
    
    def set(self, key: str, value: Any, ex: int = None, px: int = None, nx: bool = False, xx: bool = False) -> bool:
        """设置值"""
        if not self.is_enabled():
            return False
        try:
            return self._client.set(key, value, ex=ex, px=px, nx=nx, xx=xx)
        except Exception as e:
            logger.error(f"Redis SET失败 [{key}]: {e}")
            return False
    
    def delete(self, *keys) -> int:
        """删除键"""
        if not self.is_enabled() or not keys:
            return 0
        try:
            return self._client.delete(*keys)
        except Exception as e:
            logger.error(f"Redis DELETE失败: {e}")
            return 0
    
    def exists(self, *keys) -> int:
        """检查键是否存在"""
        if not self.is_enabled() or not keys:
            return 0
        try:
            return self._client.exists(*keys)
        except Exception as e:
            logger.error(f"Redis EXISTS失败: {e}")
            return 0
    
    def expire(self, key: str, seconds: int) -> bool:
        """设置过期时间（秒）"""
        if not self.is_enabled():
            return False
        try:
            return self._client.expire(key, seconds)
        except Exception as e:
            logger.error(f"Redis EXPIRE失败 [{key}]: {e}")
            return False
    
    def expireat(self, key: str, when: int) -> bool:
        """设置过期时间点（Unix时间戳）"""
        if not self.is_enabled():
            return False
        try:
            return self._client.expireat(key, when)
        except Exception as e:
            logger.error(f"Redis EXPIREAT失败 [{key}]: {e}")
            return False
    
    def ttl(self, key: str) -> int:
        """获取剩余过期时间"""
        if not self.is_enabled():
            return -2
        try:
            return self._client.ttl(key)
        except Exception as e:
            logger.error(f"Redis TTL失败 [{key}]: {e}")
            return -2
    
    def incr(self, key: str, amount: int = 1) -> Optional[int]:
        """自增"""
        if not self.is_enabled():
            return None
        try:
            return self._client.incr(key, amount)
        except Exception as e:
            logger.error(f"Redis INCR失败 [{key}]: {e}")
            return None
    
    def decr(self, key: str, amount: int = 1) -> Optional[int]:
        """自减"""
        if not self.is_enabled():
            return None
        try:
            return self._client.decr(key, amount)
        except Exception as e:
            logger.error(f"Redis DECR失败 [{key}]: {e}")
            return None

    # ==================== Hash操作 ====================
    
    def hget(self, name: str, key: str, default: Any = None) -> Any:
        """获取Hash字段值"""
        if not self.is_enabled():
            return default
        try:
            value = self._client.hget(name, key)
            return value if value is not None else default
        except Exception as e:
            logger.error(f"Redis HGET失败 [{name}.{key}]: {e}")
            return default
    
    def hset(self, name: str, key: str = None, value: Any = None, mapping: dict = None) -> int:
        """设置Hash字段"""
        if not self.is_enabled():
            return 0
        try:
            return self._client.hset(name, key, value, mapping)
        except Exception as e:
            logger.error(f"Redis HSET失败 [{name}]: {e}")
            return 0
    
    def hdel(self, name: str, *keys) -> int:
        """删除Hash字段"""
        if not self.is_enabled() or not keys:
            return 0
        try:
            return self._client.hdel(name, *keys)
        except Exception as e:
            logger.error(f"Redis HDEL失败 [{name}]: {e}")
            return 0
    
    def hgetall(self, name: str) -> dict:
        """获取Hash所有字段"""
        if not self.is_enabled():
            return {}
        try:
            return self._client.hgetall(name)
        except Exception as e:
            logger.error(f"Redis HGETALL失败 [{name}]: {e}")
            return {}
    
    def hexists(self, name: str, key: str) -> bool:
        """检查Hash字段是否存在"""
        if not self.is_enabled():
            return False
        try:
            return self._client.hexists(name, key)
        except Exception as e:
            logger.error(f"Redis HEXISTS失败 [{name}.{key}]: {e}")
            return False
    
    def hincrby(self, name: str, key: str, amount: int = 1) -> Optional[int]:
        """Hash字段自增"""
        if not self.is_enabled():
            return None
        try:
            return self._client.hincrby(name, key, amount)
        except Exception as e:
            logger.error(f"Redis HINCRBY失败 [{name}.{key}]: {e}")
            return None

    # ==================== List操作 ====================
    
    def lpush(self, name: str, *values) -> int:
        """左侧插入"""
        if not self.is_enabled() or not values:
            return 0
        try:
            return self._client.lpush(name, *values)
        except Exception as e:
            logger.error(f"Redis LPUSH失败 [{name}]: {e}")
            return 0
    
    def rpush(self, name: str, *values) -> int:
        """右侧插入"""
        if not self.is_enabled() or not values:
            return 0
        try:
            return self._client.rpush(name, *values)
        except Exception as e:
            logger.error(f"Redis RPUSH失败 [{name}]: {e}")
            return 0
    
    def lpop(self, name: str, count: int = None) -> Any:
        """左侧弹出"""
        if not self.is_enabled():
            return None
        try:
            return self._client.lpop(name, count)
        except Exception as e:
            logger.error(f"Redis LPOP失败 [{name}]: {e}")
            return None
    
    def rpop(self, name: str, count: int = None) -> Any:
        """右侧弹出"""
        if not self.is_enabled():
            return None
        try:
            return self._client.rpop(name, count)
        except Exception as e:
            logger.error(f"Redis RPOP失败 [{name}]: {e}")
            return None
    
    def lrange(self, name: str, start: int, end: int) -> list:
        """获取列表范围"""
        if not self.is_enabled():
            return []
        try:
            return self._client.lrange(name, start, end)
        except Exception as e:
            logger.error(f"Redis LRANGE失败 [{name}]: {e}")
            return []
    
    def llen(self, name: str) -> int:
        """获取列表长度"""
        if not self.is_enabled():
            return 0
        try:
            return self._client.llen(name)
        except Exception as e:
            logger.error(f"Redis LLEN失败 [{name}]: {e}")
            return 0

    # ==================== Set操作 ====================
    
    def sadd(self, name: str, *values) -> int:
        """添加集合成员"""
        if not self.is_enabled() or not values:
            return 0
        try:
            return self._client.sadd(name, *values)
        except Exception as e:
            logger.error(f"Redis SADD失败 [{name}]: {e}")
            return 0
    
    def srem(self, name: str, *values) -> int:
        """移除集合成员"""
        if not self.is_enabled() or not values:
            return 0
        try:
            return self._client.srem(name, *values)
        except Exception as e:
            logger.error(f"Redis SREM失败 [{name}]: {e}")
            return 0
    
    def smembers(self, name: str) -> set:
        """获取集合所有成员"""
        if not self.is_enabled():
            return set()
        try:
            return self._client.smembers(name)
        except Exception as e:
            logger.error(f"Redis SMEMBERS失败 [{name}]: {e}")
            return set()
    
    def sismember(self, name: str, value: Any) -> bool:
        """检查是否为集合成员"""
        if not self.is_enabled():
            return False
        try:
            return self._client.sismember(name, value)
        except Exception as e:
            logger.error(f"Redis SISMEMBER失败 [{name}]: {e}")
            return False
    
    def scard(self, name: str) -> int:
        """获取集合成员数量"""
        if not self.is_enabled():
            return 0
        try:
            return self._client.scard(name)
        except Exception as e:
            logger.error(f"Redis SCARD失败 [{name}]: {e}")
            return 0

    # ==================== Sorted Set操作 ====================
    
    def zadd(self, name: str, mapping: dict, nx: bool = False, xx: bool = False) -> int:
        """添加有序集合成员"""
        if not self.is_enabled() or not mapping:
            return 0
        try:
            return self._client.zadd(name, mapping, nx=nx, xx=xx)
        except Exception as e:
            logger.error(f"Redis ZADD失败 [{name}]: {e}")
            return 0
    
    def zrem(self, name: str, *values) -> int:
        """移除有序集合成员"""
        if not self.is_enabled() or not values:
            return 0
        try:
            return self._client.zrem(name, *values)
        except Exception as e:
            logger.error(f"Redis ZREM失败 [{name}]: {e}")
            return 0
    
    def zrange(self, name: str, start: int, end: int, withscores: bool = False) -> list:
        """获取有序集合范围"""
        if not self.is_enabled():
            return []
        try:
            return self._client.zrange(name, start, end, withscores=withscores)
        except Exception as e:
            logger.error(f"Redis ZRANGE失败 [{name}]: {e}")
            return []
    
    def zrevrange(self, name: str, start: int, end: int, withscores: bool = False) -> list:
        """获取有序集合范围（倒序）"""
        if not self.is_enabled():
            return []
        try:
            return self._client.zrevrange(name, start, end, withscores=withscores)
        except Exception as e:
            logger.error(f"Redis ZREVRANGE失败 [{name}]: {e}")
            return []
    
    def zscore(self, name: str, value: Any) -> Optional[float]:
        """获取成员分数"""
        if not self.is_enabled():
            return None
        try:
            return self._client.zscore(name, value)
        except Exception as e:
            logger.error(f"Redis ZSCORE失败 [{name}]: {e}")
            return None
    
    def zincrby(self, name: str, amount: float, value: Any) -> Optional[float]:
        """成员分数自增"""
        if not self.is_enabled():
            return None
        try:
            return self._client.zincrby(name, amount, value)
        except Exception as e:
            logger.error(f"Redis ZINCRBY失败 [{name}]: {e}")
            return None
    
    def zcard(self, name: str) -> int:
        """获取有序集合成员数量"""
        if not self.is_enabled():
            return 0
        try:
            return self._client.zcard(name)
        except Exception as e:
            logger.error(f"Redis ZCARD失败 [{name}]: {e}")
            return 0

    # ==================== 异步操作 ====================
    
    def get_async(self, key: str, default: Any = None):
        """异步获取值"""
        if not self.is_enabled() or not self._thread_pool:
            return None
        return self._thread_pool.submit(self.get, key, default)
    
    def set_async(self, key: str, value: Any, ex: int = None):
        """异步设置值"""
        if not self.is_enabled() or not self._thread_pool:
            return None
        return self._thread_pool.submit(self.set, key, value, ex)
    
    def delete_async(self, *keys):
        """异步删除键"""
        if not self.is_enabled() or not self._thread_pool:
            return None
        return self._thread_pool.submit(self.delete, *keys)

    # ==================== 管道与工具 ====================
    
    def pipeline(self, transaction: bool = True):
        """获取管道对象"""
        if not self.is_enabled():
            return None
        try:
            return self._client.pipeline(transaction=transaction)
        except Exception as e:
            logger.error(f"Redis PIPELINE失败: {e}")
            return None
    
    def keys(self, pattern: str = "*") -> list:
        """获取匹配的键"""
        if not self.is_enabled():
            return []
        try:
            return self._client.keys(pattern)
        except Exception as e:
            logger.error(f"Redis KEYS失败 [{pattern}]: {e}")
            return []
    
    def scan_iter(self, match: str = None, count: int = None):
        """迭代扫描键"""
        if not self.is_enabled():
            return iter([])
        try:
            return self._client.scan_iter(match=match, count=count)
        except Exception as e:
            logger.error(f"Redis SCAN_ITER失败: {e}")
            return iter([])
    
    def flushdb(self, asynchronous: bool = False) -> bool:
        """清空当前数据库"""
        if not self.is_enabled():
            return False
        try:
            return self._client.flushdb(asynchronous=asynchronous)
        except Exception as e:
            logger.error(f"Redis FLUSHDB失败: {e}")
            return False
    
    def info(self, section: str = None) -> dict:
        """获取服务器信息"""
        if not self.is_enabled():
            return {}
        try:
            return self._client.info(section)
        except Exception as e:
            logger.error(f"Redis INFO失败: {e}")
            return {}
    
    def dbsize(self) -> int:
        """获取键数量"""
        if not self.is_enabled():
            return 0
        try:
            return self._client.dbsize()
        except Exception as e:
            logger.error(f"Redis DBSIZE失败: {e}")
            return 0
    
    def ping(self) -> bool:
        """测试连接"""
        if not self.is_enabled():
            return False
        try:
            return self._client.ping()
        except Exception as e:
            logger.error(f"Redis PING失败: {e}")
            return False
    
    def close(self):
        """关闭连接池"""
        if self._thread_pool:
            self._thread_pool.shutdown(wait=False)
            self._thread_pool = None
        if self._pool:
            self._pool.disconnect()
            self._pool = None
        self._client = None


class RedisConnectionManager:
    """Redis连接上下文管理器"""
    __slots__ = ('pool', 'client')
    
    def __init__(self):
        self.pool = redis_pool
        self.client = None
    
    def __enter__(self) -> Optional[Redis]:
        if self.pool.is_enabled():
            self.client = self.pool.get_client()
        return self.client
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.client = None


# ============ 创建/复用单例 ============
_existing = _get_existing_instance()
if _existing is not None and hasattr(_existing, '_client') and hasattr(_existing, 'is_enabled'):
    # 复用已存在的实例（热重载场景）
    redis_pool = _existing
    logger.debug("Redis连接池: 复用已存在的实例")
else:
    # 首次创建实例
    redis_pool = RedisPool()
    _save_instance(redis_pool)
    logger.debug("Redis连接池: 创建新实例")


def init_redis() -> tuple:
    """初始化Redis并返回状态"""
    return redis_pool.get_init_status()


def get_redis_pool():
    """获取Redis连接池实例"""
    return redis_pool
