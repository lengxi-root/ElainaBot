#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
WebSocket 客户端模块 - 集成 MessageEvent
支持QQ官方Bot API的WebSocket连接和消息处理
"""

import asyncio
import json
import time
import traceback
import logging
import ssl
import certifi
import websockets
import aiohttp
from typing import Dict, Any, Optional, List, Callable
from core.event.MessageEvent import MessageEvent
from function.Access import BOT凭证

# 配置日志
logger = logging.getLogger(__name__)

# WebSocket操作码 (基于QQ官方API)
class WSOpCode:
    DISPATCH = 0        # 服务端分发事件
    HEARTBEAT = 1       # 客户端发送心跳
    IDENTIFY = 2        # 客户端发送鉴权
    RESUME = 6          # 客户端恢复连接
    RECONNECT = 7       # 服务端通知客户端重连
    INVALID_SESSION = 9 # 当identify或resume失败时，服务端返回该消息
    HELLO = 10          # 当客户端与网关建立ws连接之后，网关下发的第一条消息
    HEARTBEAT_ACK = 11  # 当发送心跳成功之后，就会收到该消息

# Intent定义 (基于QQ官方API - 使用数字位运算)
class Intent:
    # 基础权限数字常量 (基于Go SDK定义)
    GUILDS = 1 << 0                    # 1
    GUILD_MEMBERS = 1 << 1             # 2
    GUILD_BANS = 1 << 2                # 4
    GUILD_EMOJIS = 1 << 3              # 8
    GUILD_INTEGRATIONS = 1 << 4        # 16
    GUILD_WEBHOOKS = 1 << 5            # 32
    GUILD_INVITES = 1 << 6             # 64
    GUILD_VOICE_STATES = 1 << 7        # 128
    GUILD_PRESENCES = 1 << 8           # 256
    GUILD_MESSAGES = 1 << 9            # 512
    GUILD_MESSAGE_REACTIONS = 1 << 10  # 1024
    GUILD_MESSAGE_TYPING = 1 << 11     # 2048
    DIRECT_MESSAGE = 1 << 12           # 4096
    DIRECT_MESSAGE_REACTIONS = 1 << 13 # 8192
    DIRECT_MESSAGE_TYPING = 1 << 14    # 16384
    
    # QQ群相关权限 (需要特殊申请)
    GROUP_AT_MESSAGE_CREATE = 1 << 25  # 群@机器人事件
    INTERACTION = 1 << 26              # 互动事件
    MESSAGE_AUDIT = 1 << 27            # 消息审核事件
    FORUM = 1 << 28                    # 论坛事件
    AUDIO = 1 << 29                    # 音频事件
    PUBLIC_GUILD_MESSAGES = 1 << 30    # 公域频道消息事件
    

    
    # 权限组合 (数字)
    BASIC = GUILDS | GUILD_MEMBERS | GUILD_MESSAGE_REACTIONS | DIRECT_MESSAGE | INTERACTION | MESSAGE_AUDIT
    
    # MessageEvent支持的消息类型所需权限
    MESSAGE_EVENT_ONLY = GUILDS | GUILD_MESSAGE_REACTIONS | DIRECT_MESSAGE | INTERACTION | MESSAGE_AUDIT | GROUP_AT_MESSAGE_CREATE
    
    # 频道权限组合
    GUILD_ALL = BASIC | GUILD_MESSAGES
    
    # 公域频道权限组合  
    PUBLIC_GUILD = BASIC | PUBLIC_GUILD_MESSAGES
    
    # 带群功能的权限组合 (需要特殊申请)
    WITH_GROUP = BASIC | GROUP_AT_MESSAGE_CREATE
    



class WebSocketClient:
    """
    WebSocket 客户端，实现了 QQ 官方 Bot API 的 WebSocket 协议
    """
    
    def __init__(self, name: str = "default", config: Dict[str, Any] = None):
        self.name = name
        self.config = config or {}
        self.websocket = None
        self.connected = False
        self.running = False
        self.reconnect_count = 0
        self.last_heartbeat = 0
        self.heartbeat_interval = 45000  # 默认45秒
        self.heartbeat_task = None
        self.session_id = None
        self.last_seq = 0
        
        # 事件处理器
        self.handlers = {
            'message': [],
            'connect': [],
            'disconnect': [], 
            'error': [],
            'ready': []
        }
        
        # 统计信息
        self.stats = {
            'start_time': 0,
            'received_messages': 0,
            'sent_messages': 0,
            'heartbeat_count': 0,
            'reconnect_count': 0
        }
        
        # Intent配置 - 固定使用MessageEvent所需权限
        self.intents = Intent.MESSAGE_EVENT_ONLY
        
        # 日志配置
        if self.config.get('log_level'):
            logger.setLevel(getattr(logging, self.config['log_level'].upper()))
    
    def add_handler(self, event_type: str, handler: Callable):
        """添加事件处理器"""
        if event_type in self.handlers:
            self.handlers[event_type].append(handler)
    
    def remove_handler(self, event_type: str, handler: Callable):
        """移除事件处理器"""
        if event_type in self.handlers and handler in self.handlers[event_type]:
            self.handlers[event_type].remove(handler)
    
    async def _call_handlers(self, event_type: str, data: Any):
        """调用事件处理器"""
        try:
            for handler in self.handlers.get(event_type, []):
                if asyncio.iscoroutinefunction(handler):
                    await handler(data)
                else:
                    handler(data)
        except Exception as e:
            logger.error(f"处理 {event_type} 事件时出错: {e}")
    
    async def connect(self) -> bool:
        """连接到WebSocket服务器"""
        if self.connected:
            return True
            
        try:
            # 获取WebSocket URL
            ws_url = self.config.get('url')
            if not ws_url:
                logger.error("WebSocket URL 未配置")
                return False
            
            # 创建SSL上下文
            ssl_context = None
            if ws_url.startswith('wss://'):
                ssl_context = ssl.create_default_context(cafile=certifi.where())
            
            logger.info(f"正在连接到 WebSocket 服务器: {ws_url}")
            
            # 连接参数
            connect_kwargs = {'ssl': ssl_context} if ssl_context else {}
            
            # 处理不同版本的websockets库
            try:
                self.websocket = await websockets.connect(ws_url, **connect_kwargs)
            except TypeError as e:
                if 'ssl' in str(e):
                    # 如果ssl参数不支持，尝试不使用ssl参数
                    self.websocket = await websockets.connect(ws_url)
                else:
                    raise
            
            self.connected = True
            self.stats['start_time'] = time.time()
            
            logger.info("WebSocket 连接已建立")
            await self._call_handlers('connect', {'timestamp': time.time()})
            
            return True
            
        except Exception as e:
            logger.error(f"WebSocket 连接失败: {e}")
            self.connected = False
            return False
    
    async def disconnect(self):
        """断开WebSocket连接"""
        if self.connected and self.websocket:
            try:
                await self.websocket.close()
                logger.info("WebSocket 连接已断开")
            except Exception as e:
                logger.error(f"断开连接时出错: {e}")
        
        self.connected = False
        self.websocket = None
        
        # 停止心跳任务
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
        
        await self._call_handlers('disconnect', {'timestamp': time.time()})
    
    async def send_message(self, message: Dict[str, Any]) -> bool:
        """发送消息到WebSocket服务器"""
        if not self.connected or not self.websocket:
            logger.error("WebSocket 未连接")
            return False
        
        try:
            message_text = json.dumps(message, ensure_ascii=False)
            await self.websocket.send(message_text)
            self.stats['sent_messages'] += 1
            

            
            return True
            
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            self.connected = False
            return False
    
    async def send_identify(self):
        """发送身份验证消息"""
        try:
            # 获取访问令牌
            access_token = BOT凭证()
            if not access_token:
                logger.error("无法获取访问令牌")
                return False
            
            identify_payload = {
                "op": WSOpCode.IDENTIFY,
                "d": {
                    "token": f"QQBot {access_token}",
                    "intents": self.intents,  # 现在是数字
                    "shard": [0, 1],  # [shard_id, shard_count]
                    "properties": {
                        "$os": "python",
                        "$browser": "elaina-bot",
                        "$device": "elaina-bot"
                    }
                }
            }
            
            if await self.send_message(identify_payload):
                logger.info("身份验证消息已发送")
                return True
            else:
                logger.error("发送身份验证消息失败")
                return False
                
        except Exception as e:
            logger.error(f"发送身份验证消息异常: {e}")
            return False
    
    async def send_heartbeat(self):
        """发送心跳消息"""
        try:
            heartbeat_payload = {
                "op": WSOpCode.HEARTBEAT,
                "d": self.last_seq
            }
            
            if await self.send_message(heartbeat_payload):
                self.last_heartbeat = time.time()
                self.stats['heartbeat_count'] += 1
                return True
            else:
                logger.error("发送心跳消息失败")
                return False
                
        except Exception as e:
            logger.error(f"发送心跳消息异常: {e}")
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
        
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
        
        self.heartbeat_task = asyncio.create_task(heartbeat_loop())
    
    async def _process_message(self, message):
        """处理收到的WebSocket消息"""
        try:
            # 解析消息
            if isinstance(message, str):
                data = json.loads(message)
            else:
                data = json.loads(message.decode('utf-8'))
            
            self.stats['received_messages'] += 1
            
            op_code = data.get('op')
            seq = data.get('s')
            event_type = data.get('t')
            event_data = data.get('d')
            
            # 更新序列号
            if seq:
                self.last_seq = seq
            

            
            # 处理不同类型的消息
            if op_code == WSOpCode.HELLO:
                await self._handle_hello(event_data)
            elif op_code == WSOpCode.HEARTBEAT_ACK:
                pass  # 心跳确认
            elif op_code == WSOpCode.RECONNECT:
                logger.warning("服务器要求重连")
                self.connected = False
            elif op_code == WSOpCode.INVALID_SESSION:
                logger.error("会话无效，需要重新认证")
                self.session_id = None
                self.connected = False
            elif op_code == WSOpCode.DISPATCH:
                await self._handle_dispatch(event_type, event_data)
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON解析失败: {e}")
        except Exception as e:
            logger.error(f"处理消息时出错: {e}")
    
    async def _handle_hello(self, data):
        """处理Hello消息"""
        try:
            self.heartbeat_interval = data.get('heartbeat_interval', 45000)
            logger.info(f"收到Hello消息，心跳间隔: {self.heartbeat_interval}ms")
            
            # 发送身份验证
            if await self.send_identify():
                # 启动心跳
                await self.start_heartbeat()
            else:
                logger.error("身份验证失败")
                self.connected = False
                
        except Exception as e:
            logger.error(f"处理Hello消息时出错: {e}")
    
    async def _handle_dispatch(self, event_type, event_data):
        """处理分发事件"""
        try:
            if event_type == "READY":
                await self._handle_ready(event_data)
            else:
                # 只处理MessageEvent支持的消息类型
                supported_types = {
                    'GROUP_AT_MESSAGE_CREATE',
                    'C2C_MESSAGE_CREATE', 
                    'INTERACTION_CREATE',
                    'AT_MESSAGE_CREATE',
                    'GROUP_ADD_ROBOT'
                }
                
                if event_type in supported_types:
                    try:
                        # 构造MessageEvent需要的数据结构
                        message_data = {
                            't': event_type,
                            'd': event_data,
                            'id': event_data.get('id'),
                            's': self.last_seq
                        }
                        message_event = MessageEvent(message_data)
                        if not message_event.ignore:
                            await self._call_handlers('message', message_event)
                    except Exception:
                        pass  # 忽略MessageEvent处理错误
                    
        except Exception as e:
            logger.error(f"处理分发事件时出错: {e}")
    
    async def _handle_ready(self, data):
        """处理Ready事件"""
        try:
            self.session_id = data.get('session_id')
            bot_info = data.get('user', {})
            
            logger.info(f"Bot已就绪: {bot_info.get('username', 'Unknown')}")
            logger.info(f"会话ID: {self.session_id}")
            
            await self._call_handlers('ready', {
                'session_id': self.session_id,
                'bot_info': bot_info,
                'data': data
            })
            
        except Exception as e:
            logger.error(f"处理Ready事件时出错: {e}")
    
    async def _listen(self):
        """监听WebSocket消息"""
        try:
            async for message in self.websocket:
                if not self.running:
                    break
                await self._process_message(message)
                
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"WebSocket 连接已关闭: {e}")
            self.connected = False
            
        except Exception as e:
            logger.error(f"监听消息时出错: {e}")
            self.connected = False

    async def start(self):
        """启动WebSocket客户端"""
        self.running = True
        logger.info("启动 WebSocket 客户端")
        
        # 主循环：处理连接和监听
        while self.running:
            try:
                # 如果未连接，尝试连接
                if not self.connected:
                    # 检查重连次数限制
                    if (self.config.get('max_reconnects', -1) != -1 and 
                        self.reconnect_count >= self.config.get('max_reconnects', 5)):
                        logger.error("达到最大重连次数，停止尝试")
                        break
                    

                    
                    if await self.connect():
                        logger.info("WebSocket 连接建立成功，开始监听消息")
                        self.reconnect_count = 0  # 重置重连计数
                        self.stats['reconnect_count'] = self.reconnect_count
                    else:
                        self.reconnect_count += 1
                        self.stats['reconnect_count'] = self.reconnect_count
                        
                        # 自动重连
                        reconnect_interval = self.config.get('reconnect_interval', 5)
                        logger.warning(f"连接失败，{reconnect_interval}秒后重试")
                        await asyncio.sleep(reconnect_interval)
                        continue
                
                # 如果已连接，开始监听
                if self.connected:
                    await self._listen()
                    
            except Exception as e:
                logger.error(f"WebSocket 主循环异常: {e}")
                self.connected = False
                await asyncio.sleep(self.config.get('reconnect_interval', 5))

    async def stop(self):
        """停止WebSocket客户端"""
        logger.info("停止 WebSocket 客户端")
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
    """
    WebSocket 连接管理器
    支持多个 WebSocket 连接，每个连接都集成了 MessageEvent 处理器
    """
    
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
    
    async def start_all(self):
        """启动所有客户端"""
        self.running = True
        tasks = []
        for name, client in self.clients.items():
            task = asyncio.create_task(client.start())
            tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    async def stop_all(self):
        """停止所有客户端"""
        self.running = False
        tasks = []
        for name, client in self.clients.items():
            task = asyncio.create_task(client.stop())
            tasks.append(task)
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有客户端的统计信息"""
        return {name: client.get_stats() for name, client in self.clients.items()}


class QQBotWSManager:
    """
    QQ Bot WebSocket 管理器
    专门处理QQ官方Bot API的WebSocket连接
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._ssl_context = self._create_ssl_context()
    
    def _create_ssl_context(self):
        """创建SSL上下文"""
        try:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            return ssl_context
        except Exception as e:
            logger.error(f"创建SSL上下文失败: {e}")
            return None
    
    async def get_gateway_url(self) -> Optional[str]:
        """获取WebSocket网关地址"""
        try:
            # 获取访问令牌
            access_token = BOT凭证()
            if not access_token:
                logger.error("无法获取访问令牌")
                return None
            
            url = "https://api.sgroup.qq.com/gateway/bot"
            headers = {
                "Authorization": f"QQBot {access_token}",
                "Content-Type": "application/json"
            }
            
            # 创建aiohttp连接器
            connector = aiohttp.TCPConnector(ssl=self._ssl_context)
            
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        gateway_url = data.get('url')
                        if gateway_url:
                            logger.info("获取WebSocket网关成功")
                            return gateway_url
                        else:
                            logger.error("网关响应中没有URL")
                            return None
                    else:
                        error_text = await response.text()
                        logger.error(f"获取网关HTTP错误: {response.status}, {error_text}")
                        return None
                        
        except Exception as e:
            logger.error(f"获取网关异常: {e}")
            return None
    
    async def create_client(self, name: str = "qq_bot") -> Optional[WebSocketClient]:
        """创建QQ Bot WebSocket客户端"""
        try:
            # 获取网关URL
            gateway_url = await self.get_gateway_url()
            if not gateway_url:
                logger.error("无法获取WebSocket连接地址")
                return None
            
            # 创建客户端配置
            client_config = {
                'url': gateway_url,
                'reconnect_interval': self.config.get('reconnect_interval', 5),
                'max_reconnects': self.config.get('max_reconnects', -1),
                'log_level': self.config.get('log_level', 'INFO'),
                'log_message_content': self.config.get('log_message_content', False),
            }
            
            # 创建客户端
            client = WebSocketClient(name, client_config)
            return client
            
        except Exception as e:
            logger.error(f"创建QQ Bot WebSocket客户端失败: {e}")
            return None


# 全局管理器实例
_manager = WebSocketManager()


def create_client(name: str, url: str, config: Dict[str, Any] = None) -> WebSocketClient:
    """
    创建WebSocket客户端
    
    Args:
        name: 客户端名称
        url: WebSocket服务器URL
        config: 配置参数
    
    Returns:
        WebSocketClient实例
    """
    client_config = config or {}
    client_config['url'] = url
    
    client = WebSocketClient(name, client_config)
    _manager.add_client(name, client)
    
    return client


async def create_qq_bot_client(config: Dict[str, Any], name: str = "qq_bot") -> Optional[WebSocketClient]:
    """
    创建QQ Bot WebSocket客户端
    
    Args:
        config: QQ Bot配置参数
        name: 客户端名称
    
    Returns:
        WebSocketClient实例或None
    """
    manager = QQBotWSManager(config)
    client = await manager.create_client(name)
    
    if client:
        _manager.add_client(name, client)
    
    return client


def get_client(name: str) -> Optional[WebSocketClient]:
    """获取WebSocket客户端"""
    return _manager.get_client(name)


def remove_client(name: str):
    """移除WebSocket客户端"""
    _manager.remove_client(name)


async def start_all_clients():
    """启动所有WebSocket客户端"""
    await _manager.start_all()


async def stop_all_clients():
    """停止所有WebSocket客户端"""
    await _manager.stop_all()


def get_all_stats() -> Dict[str, Dict[str, Any]]:
    """获取所有客户端统计信息"""
    return _manager.get_all_stats() 