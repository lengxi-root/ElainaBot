#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio, json, time, traceback, logging, ssl, certifi, websockets, requests, sys, concurrent.futures
from typing import Dict, Any, Optional, List, Callable
from contextlib import asynccontextmanager
from function.Access import BOT凭证

def _get_supported_event_types():
    from core.event.MessageEvent import MessageEvent
    supported_types = set()
    for attr_name in dir(MessageEvent):
        if (not attr_name.startswith('_') and not callable(getattr(MessageEvent, attr_name)) and
            attr_name != 'UNKNOWN_MESSAGE' and isinstance(getattr(MessageEvent, attr_name), str)):
            attr_value = getattr(MessageEvent, attr_name)
            if attr_value and attr_value != 'UNKNOWN':
                supported_types.add(attr_value)
    return supported_types

def setup_asyncio_policy():
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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

def _safe_asyncio_sleep(duration: float):
    async def safe_sleep():
        try:
            await asyncio.sleep(duration)
        except RuntimeError as e:
            if "no running event loop" in str(e):
                raise asyncio.CancelledError("Event loop stopped")
            raise
    return safe_sleep()

class WebSocketClient:
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
        self.is_custom_mode = config and config.get('custom_mode', False)
        self.handlers = {'message': [], 'connect': [], 'disconnect': [], 'error': [], 'ready': []}
        self.stats = {'start_time': 0, 'received_messages': 0, 'sent_messages': 0, 'heartbeat_count': 0, 'reconnect_count': 0}
        self.intents = Intent.MESSAGE_EVENT_ONLY
        self._setup_logging()

    def _setup_logging(self):
        if self.config.get('log_level'):
            logger.setLevel(getattr(logging, self.config['log_level'].upper()))

    def _check_connection(self):
        return self.connected and self.websocket

    def _update_stats(self, **kwargs):
        self.stats.update(kwargs)

    def _get_config_value(self, key, default=None):
        return self.config.get(key, default)

    @asynccontextmanager
    async def _safe_websocket_operation(self):
        if not self._check_connection():
            raise Exception("WebSocket未连接")
        try:
            yield self.websocket
        except Exception as e:
            self.connected = False
            raise

    async def _send_ws_message(self, payload, message_type="消息"):
        if not self._check_connection():
            return False
        try:
            return await self._send_ws_message_internal(payload, message_type)
        except:
            return False

    async def _send_ws_message_internal(self, payload, message_type):
        async with self._safe_websocket_operation() as ws:
            message_text = json.dumps(payload, ensure_ascii=False)
            await ws.send(message_text)
            self._update_stats(sent_messages=self.stats['sent_messages'] + 1)
            return True

    def _create_ssl_context(self):
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except:
            return None

    async def _call_handlers(self, event_type: str, data: Any):
        for handler in self.handlers.get(event_type, []):
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
            except:
                pass

    def add_handler(self, event_type: str, handler: Callable):
        if event_type in self.handlers:
            self.handlers[event_type].append(handler)
    
    def remove_handler(self, event_type: str, handler: Callable):
        if event_type in self.handlers and handler in self.handlers[event_type]:
            self.handlers[event_type].remove(handler)
    
    async def connect(self) -> bool:
        if self.connected:
            return True
        ws_url = self._get_config_value('url')
        if not ws_url:
            return False
        try:
            return await self._connect_internal(ws_url)
        except:
            return False

    async def _connect_internal(self, ws_url):
        connect_kwargs = {}
        if ws_url.startswith('wss://'):
            ssl_context = self._create_ssl_context()
            if ssl_context:
                connect_kwargs['ssl'] = ssl_context
        try:
            self.websocket = await websockets.connect(ws_url, **connect_kwargs)
        except TypeError:
            self.websocket = await websockets.connect(ws_url)
        self.connected = True
        self._update_stats(start_time=time.time())
        await self._call_handlers('connect', {'timestamp': time.time()})
        if self.is_custom_mode:
            await self._call_handlers('ready', {'session_id': None, 'bot_info': {'username': 'Custom WebSocket'}, 'data': {'custom_mode': True}})
        return True
    
    async def disconnect(self):
        if self._check_connection():
            try:
                await self.websocket.close()
            except:
                pass
        self.connected = False
        self.websocket = None
        self._cancel_heartbeat_task()
        await self._call_handlers('disconnect', {'timestamp': time.time()})

    def _cancel_heartbeat_task(self):
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
    
    async def send_message(self, message: Dict[str, Any]) -> bool:
        return await self._send_ws_message(message, "消息")
    
    async def send_identify(self):
        access_token = BOT凭证()
        if not access_token:
            return False
        identify_payload = {
            "op": WSOpCode.IDENTIFY,
            "d": {
                "token": f"QQBot {access_token}",
                "intents": self.intents,
                "shard": [0, 1],
                "properties": {"$os": "python", "$browser": "elaina-bot", "$device": "elaina-bot"}
            }
        }
        return await self._send_ws_message(identify_payload, "身份验证")
    
    async def send_heartbeat(self):
        heartbeat_payload = {"op": WSOpCode.HEARTBEAT, "d": self.last_seq}
        if await self._send_ws_message(heartbeat_payload, "心跳"):
            self.last_heartbeat = time.time()
            self._update_stats(heartbeat_count=self.stats['heartbeat_count'] + 1)
            return True
        return False
    
    async def start_heartbeat(self):
        async def heartbeat_loop():
            while self.running and self.connected:
                try:
                    await asyncio.sleep(self.heartbeat_interval / 1000)
                    if self.running and self.connected:
                        await self.send_heartbeat()
                except asyncio.CancelledError:
                    break
                except:
                    break
        self._cancel_heartbeat_task()
        self.heartbeat_task = asyncio.create_task(heartbeat_loop())
    
    async def _process_message(self, message):
        try:
            if self.is_custom_mode:
                self._update_stats(received_messages=self.stats['received_messages'] + 1)
                try:
                    if isinstance(message, (bytes, bytearray)):
                        message = message.decode('utf-8')
                    parsed_data = json.loads(message) if isinstance(message, str) else message
                except (json.JSONDecodeError, UnicodeDecodeError):
                    parsed_data = message
                await self._call_handlers('message', parsed_data)
                return
            data = json.loads(message if isinstance(message, str) else message.decode('utf-8'))
            self._update_stats(received_messages=self.stats['received_messages'] + 1)
            op_code = data.get('op')
            seq = data.get('s')
            event_type = data.get('t')
            event_data = data.get('d')
            if seq:
                self.last_seq = seq
            await self._handle_op_code(op_code, event_type, event_data, data)
        except:
            pass

    async def _handle_op_code(self, op_code, event_type, event_data, raw_data=None):
        try:
            if op_code == WSOpCode.HELLO:
                await self._handle_hello(event_data)
            elif op_code == WSOpCode.HEARTBEAT_ACK:
                pass
            elif op_code == WSOpCode.RECONNECT:
                self._handle_reconnect()
            elif op_code == WSOpCode.INVALID_SESSION:
                self._handle_invalid_session()
            elif op_code == WSOpCode.DISPATCH:
                await self._handle_dispatch(event_type, event_data, raw_data)
        except:
            pass

    def _handle_reconnect(self):
        self.connected = False

    def _handle_invalid_session(self):
        self.session_id = None
        self.connected = False
    
    async def _handle_hello(self, data):
        self.heartbeat_interval = data.get('heartbeat_interval', 45000)
        if await self.send_identify():
            await self.start_heartbeat()
        else:
            self.connected = False
    
    async def _handle_dispatch(self, event_type, event_data, raw_data=None):
        if event_type == "READY":
            await self._handle_ready(event_data)
        else:
            await self._handle_message_event(event_type, event_data, raw_data)

    async def _handle_message_event(self, event_type, event_data, raw_data=None):
        supported_types = _get_supported_event_types()
        if event_type in supported_types:
            try:
                await self._call_handlers('message', raw_data)
            except:
                pass
    
    async def _handle_ready(self, data):
        self.session_id = data.get('session_id')
        bot_info = data.get('user', {})
        await self._call_handlers('ready', {'session_id': self.session_id, 'bot_info': bot_info, 'data': data})
    
    async def _listen(self):
        try:
            async for message in self.websocket:
                if not self.running:
                    break
                await self._process_message(message)
        except websockets.exceptions.ConnectionClosed:
            self.connected = False
        except:
            self.connected = False

    async def start(self):
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
            except:
                self.connected = False
                try:
                    await _safe_asyncio_sleep(self._get_config_value('reconnect_interval', 5))
                except asyncio.CancelledError:
                    self.running = False
                    break

    async def _should_stop_reconnecting(self):
        max_reconnects = self._get_config_value('max_reconnects', -1)
        if max_reconnects != -1 and self.reconnect_count >= max_reconnects:
            return True
        return False

    async def _handle_connection_failure(self):
        self.reconnect_count += 1
        self._update_stats(reconnect_count=self.reconnect_count)
        reconnect_interval = self._get_config_value('reconnect_interval', 5)
        try:
            await _safe_asyncio_sleep(reconnect_interval)
        except asyncio.CancelledError:
            self.running = False
            return

    async def stop(self):
        self.running = False
        await self.disconnect()
    
    def get_stats(self) -> Dict[str, Any]:
        uptime = time.time() - self.stats['start_time'] if self.stats['start_time'] > 0 else 0
        return {**self.stats, 'uptime': uptime, 'connected': self.connected, 'running': self.running,
                'session_id': self.session_id, 'last_seq': self.last_seq, 'heartbeat_interval': self.heartbeat_interval}

class WebSocketManager:
    def __init__(self):
        self.clients: Dict[str, WebSocketClient] = {}
        self.running = False
    
    def add_client(self, name: str, client: WebSocketClient):
        self.clients[name] = client
        
    def remove_client(self, name: str):
        if name in self.clients:
            del self.clients[name]
    
    def get_client(self, name: str) -> Optional[WebSocketClient]:
        return self.clients.get(name)

    async def _execute_client_operation(self, operation_name, client_method):
        self.running = operation_name == "start"
        tasks = [asyncio.create_task(getattr(client, client_method)()) for name, client in self.clients.items()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def start_all(self):
        await self._execute_client_operation("start", "start")
    
    async def stop_all(self):
        await self._execute_client_operation("stop", "stop")
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        return {name: client.get_stats() for name, client in self.clients.items()}

class QQBotWSManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._ssl_context = self._create_ssl_context()

    def _create_ssl_context(self):
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except:
            return None
    
    def get_gateway_url(self) -> Optional[str]:
        access_token = BOT凭证()
        if not access_token:
            return None
        return self._fetch_gateway_url(access_token)

    def _fetch_gateway_url(self, access_token, max_retries=3):
        url = "https://api.sgroup.qq.com/gateway/bot"
        headers = {"Authorization": f"QQBot {access_token}", "Content-Type": "application/json"}
        for attempt in range(max_retries):
            try:
                response = requests.get(url, headers=headers, timeout=30)
                if response.status_code == 200:
                    data = response.json()
                    gateway_url = data.get('url')
                    if gateway_url:
                        return gateway_url
            except:
                pass
            if attempt < max_retries - 1:
                time.sleep(3 + attempt)
        return None
    
    async def create_client(self, name: str = "qq_bot") -> Optional[WebSocketClient]:
        custom_url = self.config.get('custom_url')
        if custom_url:
            gateway_url = custom_url
        else:
            gateway_url = self.get_gateway_url()
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

_manager = WebSocketManager()

def create_client(name: str, url: str, config: Dict[str, Any] = None) -> WebSocketClient:
    client_config = config or {}
    client_config['url'] = url
    client = WebSocketClient(name, client_config)
    _manager.add_client(name, client)
    return client

async def create_qq_bot_client(config: Dict[str, Any], name: str = "qq_bot") -> Optional[WebSocketClient]:
    manager = QQBotWSManager(config)
    client = await manager.create_client(name)
    if client:
        _manager.add_client(name, client)
    return client

def create_custom_ws_client(ws_url: str, name: str = "custom_ws", config: Dict[str, Any] = None) -> WebSocketClient:
    client_config = config or {}
    client_config.update({
        'url': ws_url,
        'reconnect_interval': client_config.get('reconnect_interval', 5),
        'max_reconnects': client_config.get('max_reconnects', -1),
        'log_level': client_config.get('log_level', 'INFO'),
        'log_message_content': client_config.get('log_message_content', False),
    })
    client = WebSocketClient(name, client_config)
    _manager.add_client(name, client)
    return client

def get_client(name: str) -> Optional[WebSocketClient]:
    return _manager.get_client(name)

def remove_client(name: str):
    _manager.remove_client(name)

async def start_all_clients():
    await _manager.start_all()

async def stop_all_clients():
    await _manager.stop_all()

def get_all_stats() -> Dict[str, Dict[str, Any]]:
    return _manager.get_all_stats()

def run_in_thread_safe_loop(coro):
    def run_with_loop():
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