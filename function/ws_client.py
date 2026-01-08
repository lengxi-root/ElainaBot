#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio, json, time, logging, ssl, certifi, websockets, requests, sys, concurrent.futures
from contextlib import asynccontextmanager
from function.Access import BOT凭证
from functools import lru_cache

@lru_cache(maxsize=1)
def _get_supported_event_types():
    from core.event.MessageEvent import MessageEvent
    types = set()
    for name in dir(MessageEvent):
        if name.startswith('_') or name == 'UNKNOWN_MESSAGE':
            continue
        val = getattr(MessageEvent, name)
        if isinstance(val, str) and val and val != 'UNKNOWN' and not callable(val):
            types.add(val)
    return frozenset(types)

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = logging.getLogger('ElainaBot.function.ws_client')

_OP_DISPATCH = 0
_OP_HEARTBEAT = 1
_OP_IDENTIFY = 2
_OP_RESUME = 6
_OP_RECONNECT = 7
_OP_INVALID_SESSION = 9
_OP_HELLO = 10
_OP_HEARTBEAT_ACK = 11

class Intent:
    GUILDS = 1 << 0
    GUILD_MEMBERS = 1 << 1
    # GUILD_BANS = 1 << 2
    # GUILD_EMOJIS = 1 << 3
    # GUILD_INTEGRATIONS = 1 << 4
    # GUILD_WEBHOOKS = 1 << 5
    # GUILD_INVITES = 1 << 6
    # GUILD_VOICE_STATES = 1 << 7
    # GUILD_PRESENCES = 1 << 8
    # GUILD_MESSAGES = 1 << 9
    GUILD_MESSAGE_REACTIONS = 1 << 10
    # GUILD_MESSAGE_TYPING = 1 << 11
    DIRECT_MESSAGE = 1 << 12
    # DIRECT_MESSAGE_REACTIONS = 1 << 13
    # DIRECT_MESSAGE_TYPING = 1 << 14
    GROUP_AT_MESSAGE_CREATE = 1 << 25
    INTERACTION = 1 << 26
    MESSAGE_AUDIT = 1 << 27
    # FORUM = 1 << 28
    # AUDIO = 1 << 29
    # PUBLIC_GUILD_MESSAGES = 1 << 30

_DEFAULT_INTENTS = Intent.GUILDS | Intent.GUILD_MESSAGE_REACTIONS | Intent.DIRECT_MESSAGE | Intent.INTERACTION | Intent.MESSAGE_AUDIT | Intent.GROUP_AT_MESSAGE_CREATE
_DEFAULT_HEARTBEAT = 45000
_GATEWAY_URL = "https://api.sgroup.qq.com/gateway/bot"
_HANDLER_TYPES = frozenset({'message', 'connect', 'disconnect', 'error', 'ready'})

@lru_cache(maxsize=1)
def _get_ssl_context():
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except:
        return None

class WebSocketClient:
    __slots__ = ('name', 'config', 'websocket', 'connected', 'running', 'reconnect_count',
                 'last_heartbeat', 'heartbeat_interval', 'heartbeat_task', 'session_id',
                 'last_seq', 'is_custom_mode', 'handlers', 'stats', 'intents')
    
    def __init__(self, name="default", config=None):
        self.name = name
        self.config = config or {}
        self.websocket = None
        self.connected = False
        self.running = False
        self.reconnect_count = 0
        self.last_heartbeat = 0
        self.heartbeat_interval = _DEFAULT_HEARTBEAT
        self.heartbeat_task = None
        self.session_id = None
        self.last_seq = 0
        self.is_custom_mode = self.config.get('custom_mode', False)
        self.handlers = {t: [] for t in _HANDLER_TYPES}
        self.stats = {'start_time': 0, 'received_messages': 0, 'sent_messages': 0, 'heartbeat_count': 0, 'reconnect_count': 0}
        self.intents = _DEFAULT_INTENTS
        log_level = self.config.get('log_level')
        if log_level:
            logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    @asynccontextmanager
    async def _safe_ws_op(self):
        if not self.connected or not self.websocket:
            raise Exception("WebSocket未连接")
        try:
            yield self.websocket
        except:
            self.connected = False
            raise

    async def _send(self, payload):
        if not self.connected or not self.websocket:
            return False
        try:
            async with self._safe_ws_op() as ws:
                await ws.send(json.dumps(payload, ensure_ascii=False))
                self.stats['sent_messages'] += 1
                return True
        except:
            return False

    async def _call_handlers(self, event_type, data):
        for h in self.handlers.get(event_type, []):
            try:
                if asyncio.iscoroutinefunction(h):
                    await h(data)
                else:
                    h(data)
            except:
                pass

    def add_handler(self, event_type, handler):
        if event_type in self.handlers:
            self.handlers[event_type].append(handler)
    
    def remove_handler(self, event_type, handler):
        handlers = self.handlers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)
    
    async def connect(self):
        if self.connected:
            return True
        url = self.config.get('url')
        if not url:
            return False
        try:
            kwargs = {}
            if url.startswith('wss://'):
                ctx = _get_ssl_context()
                if ctx:
                    kwargs['ssl'] = ctx
            try:
                self.websocket = await websockets.connect(url, **kwargs)
            except TypeError:
                self.websocket = await websockets.connect(url)
            self.connected = True
            self.stats['start_time'] = time.time()
            await self._call_handlers('connect', {'timestamp': time.time()})
            if self.is_custom_mode:
                await self._call_handlers('ready', {'session_id': None, 'bot_info': {'username': 'Custom WebSocket'}, 'data': {'custom_mode': True}})
            return True
        except:
            return False
    
    async def disconnect(self):
        if self.connected and self.websocket:
            try:
                await self.websocket.close()
            except:
                pass
        self.connected = False
        self.websocket = None
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
        await self._call_handlers('disconnect', {'timestamp': time.time()})
    
    async def send_message(self, message):
        return await self._send(message)
    
    async def send_identify(self):
        token = BOT凭证()
        if not token:
            return False
        return await self._send({
            "op": _OP_IDENTIFY,
            "d": {
                "token": f"QQBot {token}",
                "intents": self.intents,
                "shard": [0, 1],
                "properties": {"$os": "python", "$browser": "elaina-bot", "$device": "elaina-bot"}
            }
        })
    
    async def send_heartbeat(self):
        if await self._send({"op": _OP_HEARTBEAT, "d": self.last_seq}):
            self.last_heartbeat = time.time()
            self.stats['heartbeat_count'] += 1
            return True
        return False
    
    async def start_heartbeat(self):
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
        async def loop():
            while self.running and self.connected:
                try:
                    await asyncio.sleep(self.heartbeat_interval / 1000)
                    if self.running and self.connected:
                        await self.send_heartbeat()
                except asyncio.CancelledError:
                    break
                except:
                    break
        self.heartbeat_task = asyncio.create_task(loop())
    
    async def _process_message(self, message):
        try:
            if self.is_custom_mode:
                self.stats['received_messages'] += 1
                try:
                    if isinstance(message, (bytes, bytearray)):
                        message = message.decode('utf-8')
                    data = json.loads(message) if isinstance(message, str) else message
                except:
                    data = message
                await self._call_handlers('message', data)
                return
            data = json.loads(message if isinstance(message, str) else message.decode('utf-8'))
            self.stats['received_messages'] += 1
            op = data.get('op')
            seq = data.get('s')
            if seq:
                self.last_seq = seq
            if op == _OP_HELLO:
                self.heartbeat_interval = data.get('d', {}).get('heartbeat_interval', _DEFAULT_HEARTBEAT)
                if await self.send_identify():
                    await self.start_heartbeat()
                else:
                    self.connected = False
            elif op == _OP_RECONNECT:
                self.connected = False
            elif op == _OP_INVALID_SESSION:
                self.session_id = None
                self.connected = False
            elif op == _OP_DISPATCH:
                event_type = data.get('t')
                event_data = data.get('d')
                if event_type == "READY":
                    self.session_id = event_data.get('session_id')
                    await self._call_handlers('ready', {'session_id': self.session_id, 'bot_info': event_data.get('user', {}), 'data': event_data})
                elif event_type in _get_supported_event_types():
                    await self._call_handlers('message', data)
        except:
            pass

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
        max_reconnects = self.config.get('max_reconnects', -1)
        reconnect_interval = self.config.get('reconnect_interval', 5)
        while self.running:
            try:
                if not self.connected:
                    if max_reconnects != -1 and self.reconnect_count >= max_reconnects:
                        break
                    if await self.connect():
                        self.reconnect_count = 0
                        self.stats['reconnect_count'] = 0
                    else:
                        self.reconnect_count += 1
                        self.stats['reconnect_count'] = self.reconnect_count
                        try:
                            await asyncio.sleep(reconnect_interval)
                        except asyncio.CancelledError:
                            self.running = False
                            break
                        continue
                if self.connected:
                    await self._listen()
            except:
                self.connected = False
                try:
                    await asyncio.sleep(reconnect_interval)
                except asyncio.CancelledError:
                    self.running = False
                    break

    async def stop(self):
        self.running = False
        await self.disconnect()
    
    def get_stats(self):
        uptime = time.time() - self.stats['start_time'] if self.stats['start_time'] > 0 else 0
        return {**self.stats, 'uptime': uptime, 'connected': self.connected, 'running': self.running,
                'session_id': self.session_id, 'last_seq': self.last_seq, 'heartbeat_interval': self.heartbeat_interval}

class WebSocketManager:
    __slots__ = ('clients', 'running')
    
    def __init__(self):
        self.clients = {}
        self.running = False
    
    def add_client(self, name, client):
        self.clients[name] = client
        
    def remove_client(self, name):
        self.clients.pop(name, None)
    
    def get_client(self, name):
        return self.clients.get(name)
    
    async def start_all(self):
        self.running = True
        tasks = [asyncio.create_task(c.start()) for c in self.clients.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def stop_all(self):
        self.running = False
        tasks = [asyncio.create_task(c.stop()) for c in self.clients.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    def get_all_stats(self):
        return {name: c.get_stats() for name, c in self.clients.items()}

class QQBotWSManager:
    __slots__ = ('config',)
    
    def __init__(self, config):
        self.config = config
    
    def get_gateway_url(self):
        token = BOT凭证()
        if not token:
            return None
        headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}
        for attempt in range(3):
            try:
                resp = requests.get(_GATEWAY_URL, headers=headers, timeout=30)
                if resp.status_code == 200:
                    url = resp.json().get('url')
                    if url:
                        return url
            except:
                pass
            if attempt < 2:
                time.sleep(3 + attempt)
        return None
    
    async def create_client(self, name="qq_bot"):
        url = self.config.get('custom_url') or self.get_gateway_url()
        if not url:
            return None
        return WebSocketClient(name, {
            'url': url,
            'reconnect_interval': self.config.get('reconnect_interval', 5),
            'max_reconnects': self.config.get('max_reconnects', -1),
            'log_level': self.config.get('log_level', 'INFO'),
            'log_message_content': self.config.get('log_message_content', False),
        })

_manager = WebSocketManager()

def create_client(name, url, config=None):
    cfg = config or {}
    cfg['url'] = url
    client = WebSocketClient(name, cfg)
    _manager.add_client(name, client)
    return client

async def create_qq_bot_client(config, name="qq_bot"):
    client = await QQBotWSManager(config).create_client(name)
    if client:
        _manager.add_client(name, client)
    return client

def create_custom_ws_client(ws_url, name="custom_ws", config=None):
    cfg = config or {}
    cfg.update({
        'url': ws_url,
        'reconnect_interval': cfg.get('reconnect_interval', 5),
        'max_reconnects': cfg.get('max_reconnects', -1),
        'log_level': cfg.get('log_level', 'INFO'),
        'log_message_content': cfg.get('log_message_content', False),
    })
    client = WebSocketClient(name, cfg)
    _manager.add_client(name, client)
    return client

def get_client(name):
    return _manager.get_client(name)

def remove_client(name):
    _manager.remove_client(name)

async def start_all_clients():
    await _manager.start_all()

async def stop_all_clients():
    await _manager.stop_all()

def get_all_stats():
    return _manager.get_all_stats()

def run_in_thread_safe_loop(coro):
    def run():
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    with concurrent.futures.ThreadPoolExecutor() as executor:
        return executor.submit(run).result()
