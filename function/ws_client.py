#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import json
import time
import traceback
import logging
import ssl
import certifi
import websockets
import requests
import sys
import concurrent.futures
from typing import Dict, Any, Optional, List, Callable
from contextlib import asynccontextmanager

from function.Access import BOT凭证

# 动态获取支持的事件类型
def _get_supported_event_types():
    """动态获取MessageEvent中定义的所有事件类型"""
    from core.event.MessageEvent import MessageEvent
    supported_types = set()
    
    # 获取MessageEvent类中所有的事件类型常量
    for attr_name in dir(MessageEvent):
        # 跳过私有属性、方法和UNKNOWN_MESSAGE
        if (not attr_name.startswith('_') and 
            not callable(getattr(MessageEvent, attr_name)) and
            attr_name != 'UNKNOWN_MESSAGE' and
            isinstance(getattr(MessageEvent, attr_name), str)):
            
            attr_value = getattr(MessageEvent, attr_name)
            # 确保是有效的事件类型字符串
            if attr_value and attr_value != 'UNKNOWN':
                supported_types.add(attr_value)
    
    return supported_types

def setup_asyncio_policy():
    """设置asyncio策略以兼容Windows和Linux"""
    if sys.platform == 'win32':
        # Windows上强制使用SelectorEventLoop避免线程问题
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 初始化时设置策略
setup_asyncio_policy()

logger = logging.getLogger(__name__)

class WSOpCode:
    DISPATCH = 0
    HEARTBEAT = 1
    IDENTIFY = 2
    RESUME = 6
    RECONNECT = 7
    INVALID_SESSION = 9
    HELLO = 10
    HEARTBEAT_ACK = 11

class Intent:
    GUILDS = 1 << 0
    GUILD_MEMBERS = 1 << 1
    GUILD_BANS = 1 << 2
    GUILD_EMOJIS = 1 << 3
    GUILD_INTEGRATIONS = 1 << 4
    GUILD_WEBHOOKS = 1 << 5
    GUILD_INVITES = 1 << 6
    GUILD_VOICE_STATES = 1 << 7
    GUILD_PRESENCES = 1 << 8
    GUILD_MESSAGES = 1 << 9
    GUILD_MESSAGE_REACTIONS = 1 << 10
    GUILD_MESSAGE_TYPING = 1 << 11
    DIRECT_MESSAGE = 1 << 12
    DIRECT_MESSAGE_REACTIONS = 1 << 13
    DIRECT_MESSAGE_TYPING = 1 << 14
    GROUP_AT_MESSAGE_CREATE = 1 << 25
    INTERACTION = 1 << 26
    MESSAGE_AUDIT = 1 << 27
    FORUM = 1 << 28
    AUDIO = 1 << 29
    PUBLIC_GUILD_MESSAGES = 1 << 30
    
    BASIC = GUILDS | GUILD_MEMBERS | GUILD_MESSAGE_REACTIONS | DIRECT_MESSAGE | INTERACTION | MESSAGE_AUDIT
    MESSAGE_EVENT_ONLY = GUILDS | GUILD_MESSAGE_REACTIONS | DIRECT_MESSAGE | INTERACTION | MESSAGE_AUDIT | GROUP_AT_MESSAGE_CREATE
    GUILD_ALL = BASIC | GUILD_MESSAGES
    PUBLIC_GUILD = BASIC | PUBLIC_GUILD_MESSAGES
    WITH_GROUP = BASIC | GROUP_AT_MESSAGE_CREATE

class WebSocketClient:
    """WebSocket客户端"""
    
    def __init__(self, name: str = "default", config: Dict[str, Any] = None):
        self.name = name
        self.config = config or {}
        self.websocket = None
        self.connected = False
        self.running = False
        self.reconnect_count = 0
        self.last_heartbeat = 0
        self.heartbeat_interval = 45000
        self.heartbeat_task = None
        self.session_id = None
        self.last_seq = 0
        
        self.handlers = {
            'message': [],
            'connect': [],
            'disconnect': [], 
            'error': [],
            'ready': []
        }
        
        self.stats = {
            'start_time': 0,
            'received_messages': 0,
            'sent_messages': 0,
            'heartbeat_count': 0,
            'reconnect_count': 0
        }
        
        self.intents = Intent.MESSAGE_EVENT_ONLY
        self._setup_logging()

    def _setup_logging(self):
        """设置日志级别"""
        if self.config.get('log_level'):
            logger.setLevel(getattr(logging, self.config['log_level'].upper()))

    def _check_connection(self):
        """检查连接状态"""
        return self.connected and self.websocket

    def _update_stats(self, **kwargs):
        """更新统计信息"""
        self.stats.update(kwargs)

    def _get_config_value(self, key, default=None):
        """获取配置值"""
        return self.config.get(key, default)

    @asynccontextmanager
    async def _safe_websocket_operation(self):
        """WebSocket操作上下文管理器"""
        if not self._check_connection():
            raise Exception("WebSocket未连接")
        try:
            yield self.websocket
        except Exception as e:
            logger.error(f"WebSocket操作失败: {e}")
            self.connected = False
            raise

    async def _send_ws_message(self, payload, message_type="消息"):
        """统一的WebSocket消息发送"""
        if not self._check_connection():
            return False

        try:
            return await self._send_ws_message_internal(payload, message_type)
        except Exception:
            return False

    async def _send_ws_message_internal(self, payload, message_type):
        """内部消息发送实现"""
        async with self._safe_websocket_operation() as ws:
            message_text = json.dumps(payload, ensure_ascii=False)
            await ws.send(message_text)
            self._update_stats(sent_messages=self.stats['sent_messages'] + 1)
            return True

    def _create_ssl_context(self):
        """创建SSL上下文"""
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return None

    async def _call_handlers(self, event_type: str, data: Any):
        """调用事件处理器"""
        for handler in self.handlers.get(event_type, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except Exception:
                pass

    def add_handler(self, event_type: str, handler: Callable):
        """添加事件处理器"""
        if event_type in self.handlers:
            self.handlers[event_type].append(handler)
    
    def remove_handler(self, event_type: str, handler: Callable):
        """移除事件处理器"""
        if event_type in self.handlers and handler in self.handlers[event_type]:
            self.handlers[event_type].remove(handler)
    
    async def connect(self) -> bool:
        """连接WebSocket服务器"""
        if self.connected:
            return True
            
        ws_url = self._get_config_value('url')
        if not ws_url:
            logger.error("WebSocket URL未配置")
            return False
        
        try:
            return await self._connect_internal(ws_url)
        except Exception:
            return False

    async def _connect_internal(self, ws_url):
        """内部连接实现"""
        connect_kwargs = {}
        if ws_url.startswith('wss://'):
            ssl_context = self._create_ssl_context()
            if ssl_context:
                connect_kwargs['ssl'] = ssl_context
        
        try:
            self.websocket = await websockets.connect(ws_url, **connect_kwargs)
        except TypeError:
            # 兼容不支持ssl参数的版本
            self.websocket = await websockets.connect(ws_url)
        
        self.connected = True
        self._update_stats(start_time=time.time())
        
        await self._call_handlers('connect', {'timestamp': time.time()})
        return True
    
    async def disconnect(self):
        """断开连接"""
        if self._check_connection():
            try:
                await self.websocket.close()
            except Exception:
                pass
        
        self.connected = False
        self.websocket = None
        
        self._cancel_heartbeat_task()
        await self._call_handlers('disconnect', {'timestamp': time.time()})

    def _cancel_heartbeat_task(self):
        """取消心跳任务"""
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
    
    async def send_message(self, message: Dict[str, Any]) -> bool:
        """发送消息"""
        return await self._send_ws_message(message, "消息")
    
    async def send_identify(self):
        """发送身份验证"""
        access_token = BOT凭证()
        if not access_token:
            logger.error("无法获取访问令牌")
            return False
        
        identify_payload = {
            "op": WSOpCode.IDENTIFY,
            "d": {
                "token": f"QQBot {access_token}",
                "intents": self.intents,
                "shard": [0, 1],
                "properties": {
                    "$os": "python",
                    "$browser": "elaina-bot",
                    "$device": "elaina-bot"
                }
            }
        }
        
        return await self._send_ws_message(identify_payload, "身份验证")
    
    async def send_heartbeat(self):
        """发送心跳"""
        heartbeat_payload = {
            "op": WSOpCode.HEARTBEAT,
            "d": self.last_seq
        }
        
        if await self._send_ws_message(heartbeat_payload, "心跳"):
            self.last_heartbeat = time.time()
            self._update_stats(heartbeat_count=self.stats['heartbeat_count'] + 1)
            return True
        return False
    
    async def start_heartbeat(self):
        """启动心跳任务"""
        async def heartbeat_loop():
            while self.running and self.connected:
                try:
                    await asyncio.sleep(self.heartbeat_interval / 1000)
                    if self.running and self.connected:
                        await self.send_heartbeat()
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"心跳任务异常: {e}")
                    break
        
        self._cancel_heartbeat_task()
        self.heartbeat_task = asyncio.create_task(heartbeat_loop())
    
    async def _process_message(self, message):
        """处理收到的消息"""
        try:
            data = json.loads(message if isinstance(message, str) else message.decode('utf-8'))
            
            self._update_stats(received_messages=self.stats['received_messages'] + 1)
            
            op_code = data.get('op')
            seq = data.get('s')
            event_type = data.get('t')
            event_data = data.get('d')
            
            if seq:
                self.last_seq = seq
            
            await self._handle_op_code(op_code, event_type, event_data, data)
            
        except Exception:
            pass

    async def _handle_op_code(self, op_code, event_type, event_data, raw_data=None):
        """处理操作码"""
        try:
            if op_code == WSOpCode.HELLO:
                await self._handle_hello(event_data)
            elif op_code == WSOpCode.HEARTBEAT_ACK:
                pass  # 心跳ACK不需要处理
            elif op_code == WSOpCode.RECONNECT:
                self._handle_reconnect()
            elif op_code == WSOpCode.INVALID_SESSION:
                self._handle_invalid_session()
            elif op_code == WSOpCode.DISPATCH:
                await self._handle_dispatch(event_type, event_data, raw_data)
        except Exception:
            pass

    def _handle_reconnect(self):
        """处理重连请求"""
        logger.warning("服务器要求重连")
        self.connected = False

    def _handle_invalid_session(self):
        """处理无效会话"""
        logger.error("会话无效，需要重新认证")
        self.session_id = None
        self.connected = False
    
    async def _handle_hello(self, data):
        """处理Hello消息"""
        self.heartbeat_interval = data.get('heartbeat_interval', 45000)
        
        if await self.send_identify():
            await self.start_heartbeat()
        else:
            logger.error("身份验证失败")
            self.connected = False
    
    async def _handle_dispatch(self, event_type, event_data, raw_data=None):
        """处理分发事件"""
        if event_type == "READY":
            await self._handle_ready(event_data)
        else:
            await self._handle_message_event(event_type, event_data, raw_data)

    async def _handle_message_event(self, event_type, event_data, raw_data=None):
        """处理消息事件 - 仅作为连接器，传递原始数据"""
        # 动态获取支持的事件类型，与MessageEvent保持同步
        supported_types = _get_supported_event_types()
        
        if event_type in supported_types:
            try:
                # 传递原始数据给处理器，让上层统一处理
                await self._call_handlers('message', raw_data)
                    
            except Exception as e:
                logger.error(f"处理消息事件失败: {e}")
        else:
            # 记录未支持的事件类型，便于调试
            logger.debug(f"未支持的事件类型: {event_type}, 当前支持的类型: {supported_types}")
    
    async def _handle_ready(self, data):
        """处理Ready事件"""
        self.session_id = data.get('session_id')
        bot_info = data.get('user', {})
        
        await self._call_handlers('ready', {
            'session_id': self.session_id,
            'bot_info': bot_info,
            'data': data
        })
    
    async def _listen(self):
        """监听消息"""
        try:
            async for message in self.websocket:
                if not self.running:
                    break
                await self._process_message(message)
                
        except websockets.exceptions.ConnectionClosed:
            self.connected = False
        except Exception as e:
            logger.error(f"监听消息失败: {e}")
            self.connected = False

    async def start(self):
        """启动客户端"""
        self.running = True
        
        while self.running:
            try:
                if not self.connected:
                    if await self._should_stop_reconnecting():
                        break
                    
                    if await self.connect():
                        self.reconnect_count = 0
                        self._update_stats(reconnect_count=0)
                    else:
                        await self._handle_connection_failure()
                        continue
                
                if self.connected:
                    await self._listen()
                    
            except Exception as e:
                logger.error(f"主循环异常: {e}")
                self.connected = False
                await asyncio.sleep(self._get_config_value('reconnect_interval', 5))

    async def _should_stop_reconnecting(self):
        """检查是否应该停止重连"""
        max_reconnects = self._get_config_value('max_reconnects', -1)
        if max_reconnects != -1 and self.reconnect_count >= max_reconnects:
            logger.error("达到最大重连次数")
            return True
        return False

    async def _handle_connection_failure(self):
        """处理连接失败"""
        self.reconnect_count += 1
        self._update_stats(reconnect_count=self.reconnect_count)
        
        reconnect_interval = self._get_config_value('reconnect_interval', 5)
        await asyncio.sleep(reconnect_interval)

    async def stop(self):
        """停止客户端"""
        self.running = False
        await self.disconnect()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        uptime = time.time() - self.stats['start_time'] if self.stats['start_time'] > 0 else 0
        return {
            **self.stats,
            'uptime': uptime,
            'connected': self.connected,
            'running': self.running,
            'session_id': self.session_id,
            'last_seq': self.last_seq,
            'heartbeat_interval': self.heartbeat_interval
        }

class WebSocketManager:
    """WebSocket连接管理器"""
    
    def __init__(self):
        self.clients: Dict[str, WebSocketClient] = {}
        self.running = False
    
    def add_client(self, name: str, client: WebSocketClient):
        """添加客户端"""
        self.clients[name] = client
        
    def remove_client(self, name: str):
        """移除客户端"""
        if name in self.clients:
            del self.clients[name]
    
    def get_client(self, name: str) -> Optional[WebSocketClient]:
        """获取客户端"""
        return self.clients.get(name)

    async def _execute_client_operation(self, operation_name, client_method):
        """统一的客户端操作执行"""
        self.running = operation_name == "start"
        tasks = []
        
        for name, client in self.clients.items():
            task = asyncio.create_task(getattr(client, client_method)())
            tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def start_all(self):
        """启动所有客户端"""
        await self._execute_client_operation("start", "start")
    
    async def stop_all(self):
        """停止所有客户端"""
        await self._execute_client_operation("stop", "stop")
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有统计信息"""
        return {name: client.get_stats() for name, client in self.clients.items()}

class QQBotWSManager:
    """QQ Bot WebSocket管理器"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._ssl_context = self._create_ssl_context()

    def _create_ssl_context(self):
        """创建SSL上下文"""
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return None
    
    def get_gateway_url(self) -> Optional[str]:
        """获取网关地址"""
        access_token = BOT凭证()
        if not access_token:
            logger.error("无法获取访问令牌")
            return None
        
        return self._fetch_gateway_url(access_token)

    def _fetch_gateway_url(self, access_token, max_retries=3):
        """获取网关URL"""
        url = "https://api.sgroup.qq.com/gateway/bot"
        headers = {
            "Authorization": f"QQBot {access_token}",
            "Content-Type": "application/json"
        }
        
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    gateway_url = data.get('url')
                    if gateway_url:
                        return gateway_url
                        
            except Exception as e:
                logger.error(f"获取网关URL失败 (第 {attempt + 1} 次): {e}")
                
            if attempt < max_retries - 1:
                time.sleep(3 + attempt)
        
        return None
    
    async def create_client(self, name: str = "qq_bot") -> Optional[WebSocketClient]:
        """创建客户端"""
        gateway_url = self.get_gateway_url()  # 移除await，因为现在是同步方法
        if not gateway_url:
            return None
        
        client_config = {
            'url': gateway_url,
            'reconnect_interval': self.config.get('reconnect_interval', 5),
            'max_reconnects': self.config.get('max_reconnects', -1),
            'log_level': self.config.get('log_level', 'INFO'),
            'log_message_content': self.config.get('log_message_content', False),
        }
        
        return WebSocketClient(name, client_config)

# 全局管理器
_manager = WebSocketManager()

def create_client(name: str, url: str, config: Dict[str, Any] = None) -> WebSocketClient:
    """创建WebSocket客户端"""
    client_config = config or {}
    client_config['url'] = url
    
    client = WebSocketClient(name, client_config)
    _manager.add_client(name, client)
    
    return client

async def create_qq_bot_client(config: Dict[str, Any], name: str = "qq_bot") -> Optional[WebSocketClient]:
    """创建QQ Bot客户端"""
    manager = QQBotWSManager(config)
    client = await manager.create_client(name)
    
    if client:
        _manager.add_client(name, client)
    
    return client

def get_client(name: str) -> Optional[WebSocketClient]:
    """获取客户端"""
    return _manager.get_client(name)

def remove_client(name: str):
    """移除客户端"""
    _manager.remove_client(name)

async def start_all_clients():
    """启动所有客户端"""
    await _manager.start_all()

async def stop_all_clients():
    """停止所有客户端"""
    await _manager.stop_all()

def get_all_stats() -> Dict[str, Dict[str, Any]]:
    """获取所有统计信息"""
    return _manager.get_all_stats()

def run_in_thread_safe_loop(coro):
    """在线程安全的事件循环中运行异步代码"""
    import threading
    import concurrent.futures
    
    def run_with_loop():
        """In the new thread, create and run event loop"""
        # 在新线程中设置正确的事件循环策略
        setup_asyncio_policy()
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(run_with_loop)
        return future.result() 