#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ===== 1. 标准库导入 =====
import sys
import os
import time
import json
import gc
import threading
import logging
import traceback
import random
import warnings

# 抑制PIL图片处理相关的警告
warnings.filterwarnings("ignore", "Corrupt EXIF data", UserWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="PIL")
try:
    import eventlet
    eventlet.monkey_patch()  # 必须在导入其他模块前进行monkey patch
except ImportError:
    pass  # 如果没有安装eventlet，则跳过

# ===== 2. 第三方库导入 =====
from flask import Flask, request, jsonify
from flask_socketio import SocketIO
# ===== 3. 自定义模块导入 =====
from config import LOG_CONFIG, LOG_DB_CONFIG, WEBSOCKET_CONFIG, SERVER_CONFIG
from web.app import start_web, add_received_message, add_plugin_log, add_framework_log, add_error_log
try:
    from function.log_db import add_log_to_db
except ImportError:
    def add_log_to_db(log_type, log_data):
        return False
from function.Access import BOT凭证, BOTAPI, Json取, Json
from function.httpx_pool import async_get, async_post, sync_get, sync_post, get_pool_manager

# 导入DAU分析模块
try:
    from function.dau_analytics import start_dau_analytics, stop_dau_analytics
    _dau_analytics_available = True
except ImportError:
    _dau_analytics_available = False
    def start_dau_analytics():
        pass
    def stop_dau_analytics():
        pass

# ===== 4. 全局状态变量 =====
_logging_initialized = False  # 日志系统初始化标志
_app_initialized = False      # 应用初始化标志
http_pool = get_pool_manager() # HTTP连接池

# ===== 5. 日志系统 =====
class NullHandler(logging.Handler):
    """空日志处理器，不输出任何内容"""
    def emit(self, record):
        pass

def setup_logging():
    """初始化日志系统"""
    global _logging_initialized
    
    if _logging_initialized:
        return
    
    # 配置根日志记录器
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # 设置根日志级别为INFO，允许显示更多日志
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    
    # 禁用第三方库日志
    for logger_name in ['werkzeug', 'socketio', 'engineio', 'urllib3', 'httpx_pool', 'db_pool', 'log_db']:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)
        logger.propagate = False
    
    _logging_initialized = True

# 全局异常处理
def global_exception_handler(exctype, value, tb):
    """处理所有未捕获的异常，记录到错误日志"""
    error_msg = f"未捕获的异常: {exctype.__name__}: {value}"
    tb_str = "".join(traceback.format_tb(tb))
    
    # 输出到控制台
    print(error_msg)
    print(tb_str)
    
    # 记录到前台日志
    try:
        add_error_log(error_msg, tb_str)
    except Exception:
        pass
    
    # 记录到数据库日志
    try:
        if LOG_DB_CONFIG.get('enabled', False):
            add_log_to_db('error', {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'content': error_msg,
                'traceback': tb_str
            })
    except Exception:
        pass
        
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = global_exception_handler

# ===== 6. Flask应用与SocketIO =====
# 禁用Flask启动横幅
import flask.cli
flask.cli.show_server_banner = lambda *args: None

def create_app():
    """创建并配置Flask应用"""
    flask_app = Flask(__name__)
    flask_app.config['SECRET_KEY'] = 'mbot_secret'
    flask_app.config['TEMPLATES_AUTO_RELOAD'] = True
    flask_app.jinja_env.auto_reload = True
    flask_app.logger.disabled = True
    
    # 初始化SocketIO
    socketio = SocketIO(
        flask_app,
        cors_allowed_origins="*",
        async_mode='eventlet',
        logger=False,
        engineio_logger=False
    )
    flask_app.socketio = socketio
    
    # 注册路由
    @flask_app.route('/', methods=['GET', 'POST'])
    def handle_request():
        """主入口路由，处理GET/POST请求"""
        try:
            if request.method == 'GET':
                if request.args.get('type'):
                    return "Type handled"
                return jsonify({"message": "The service is temporarily unavailable"}), 200
                
            data = request.get_data()
            if not data:
                return "No data received", 400
                
            try:
                json_data = json.loads(data)
            except json.JSONDecodeError:
                return "Invalid JSON data", 400
                
            op = json_data.get("op")
            
            # 先检查op是否为0（消息处理）
            if op == 0:
                threading.Thread(
                    target=process_message_event, 
                    args=(json_data, data.decode()),
                    daemon=True
                ).start()
                return "OK"
            
            # 然后检查op是否为13（签名）
            elif op == 13:
                from function.sign import Signs
                sign = Signs()
                return sign.sign(data.decode())
                
            return "Event not handled", 400
            
        except Exception as e:
            error_msg = f"处理请求时发生错误: {str(e)}"
            add_error_log(error_msg, traceback.format_exc())
            return "Server error", 500
    
    return flask_app

# ===== 7. 消息处理 =====
def record_message(message_data):
    """记录接收到的消息到Web面板和数据库"""
    try:
        add_received_message(message_data)
    except Exception as e:
        error_msg = f"记录消息失败: {str(e)}"
        tb_str = traceback.format_exc()
        
        # 记录到前台日志
        try:
            add_error_log(error_msg, tb_str)
        except Exception:
            pass
        
        # 记录到数据库日志
        try:
            if LOG_DB_CONFIG.get('enabled', False):
                add_log_to_db('error', {
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'content': error_msg,
                    'traceback': tb_str
                })
        except Exception:
            pass

def process_message_event(data, raw_data=None):
    """处理消息事件，分发到插件系统"""
    if not data:
        return False
        
    record_message(data)
    
    try:
        # 局部导入减少全局命名空间污染
        from core.plugin.PluginManager import PluginManager
        from core.event.MessageEvent import MessageEvent
        
        plugin_manager = PluginManager()
        event = MessageEvent(raw_data if raw_data else data)
        result = plugin_manager.dispatch_message(event)
        
        # 随机进行小型垃圾回收
        if random.random() < 0.05:
            gc.collect(0)
            
        return result
        
    except Exception as e:
        error_msg = f"插件系统处理消息失败: {str(e)}"
        tb_str = traceback.format_exc()
        
        # 记录到前台日志
        try:
            add_error_log(error_msg, tb_str)
        except Exception:
            pass
        
        # 记录到数据库日志
        try:
            if LOG_DB_CONFIG.get('enabled', False):
                add_log_to_db('error', {
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'content': error_msg,
                    'traceback': tb_str
                })
        except Exception:
            pass
            
        return False

# ===== 8. WebSocket 自动连接 =====
async def handle_ws_message(event):
    """处理WebSocket消息"""
    try:
        # 记录消息内容（如果配置启用）
        if WEBSOCKET_CONFIG.get('log_message_content', False):
            content = getattr(event, 'content', str(event))
            add_framework_log(f"WebSocket收到消息: {content}")
        
        # 记录消息到web面板
        if hasattr(event, 'raw_data'):
            record_message(event.raw_data)
        
        # 分发到插件系统处理
        def process_plugin():
            try:
                from core.plugin.PluginManager import PluginManager
                plugin_manager = PluginManager()
                plugin_manager.dispatch_message(event)
                
                # 随机垃圾回收
                if random.random() < 0.05:
                    gc.collect(0)
                    
            except Exception as e:
                add_error_log(f"WebSocket插件处理失败: {str(e)}", traceback.format_exc())
        
        # 后台线程处理，避免阻塞WebSocket
        threading.Thread(target=process_plugin, daemon=True).start()
        
    except Exception as e:
        add_error_log(f"WebSocket消息处理失败: {str(e)}", traceback.format_exc())

async def create_websocket_client():
    """创建WebSocket客户端"""
    from function.ws_client import create_qq_bot_client
    
    client = await create_qq_bot_client(WEBSOCKET_CONFIG)
    if not client:
        raise Exception("创建WebSocket客户端失败")
    
    # 注册事件处理器
    client.add_handler('message', handle_ws_message)
    client.add_handler('connect', lambda data: add_framework_log("WebSocket连接已建立"))
    client.add_handler('disconnect', lambda data: add_framework_log("WebSocket连接已断开"))
    client.add_handler('error', lambda data: add_error_log(f"WebSocket错误: {data.get('error', '')}"))
    client.add_handler('ready', lambda data: add_framework_log(
        f"WebSocket已就绪 - Bot: {data.get('bot_info', {}).get('username', 'Unknown')}"
    ))
    
    return client

def run_websocket_client():
    """在独立线程中运行WebSocket客户端"""
    try:
        import asyncio
        from function.ws_client import create_qq_bot_client
        
        # 创建新的事件循环
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def websocket_main():
            client = await create_websocket_client()
            await client.start()
        
        loop.run_until_complete(websocket_main())
        
    except Exception as e:
        add_error_log(f"WebSocket客户端运行失败: {str(e)}", traceback.format_exc())

def setup_websocket():
    """设置WebSocket自动连接"""
    # 检查配置
    if not WEBSOCKET_CONFIG.get('enabled', False) or not WEBSOCKET_CONFIG.get('auto_connect', True):
        return
    
    
    try:
        # 检查认证配置
        from config import appid, secret
        if not appid or not secret:
            add_error_log("机器人认证配置不完整，请检查 config.py 中的 appid 和 secret")
            return
        
        # 配置WebSocket日志级别
        ws_log_level = WEBSOCKET_CONFIG.get('log_level', 'INFO')
        ws_logger = logging.getLogger('ws_client')
        ws_logger.setLevel(getattr(logging, ws_log_level.upper(), logging.INFO))
        
        # 启动WebSocket客户端线程
        ws_thread = threading.Thread(target=run_websocket_client, daemon=True, name="WebSocketClient")
        ws_thread.start()
        
        add_framework_log("QQ机器人WebSocket自动连接已启动")
        
    except ImportError:
        add_error_log("WebSocket模块导入失败，请检查ws_client.py文件")
    except Exception as e:
        add_error_log(f"WebSocket设置失败: {str(e)}", traceback.format_exc())

# ===== 9. 系统初始化 =====
def init_systems():
    """初始化所有系统组件"""
    try:
        setup_logging()
        gc.enable()
        gc.set_threshold(700, 10, 5)
        gc.collect(0)
        
        # 预加载插件
        def load_plugins_async():
            try:
                from core.plugin.PluginManager import PluginManager
                plugin_manager = PluginManager()
                plugin_manager.load_plugins()
            except Exception as e:
                error_msg = f"插件系统初始化失败: {str(e)}"
                tb_str = traceback.format_exc()
                
                # 记录到前台日志
                try:
                    add_error_log(error_msg, tb_str)
                except Exception:
                    pass
                
                # 记录到数据库日志
                try:
                    if LOG_DB_CONFIG.get('enabled', False):
                        add_log_to_db('error', {
                            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                            'content': error_msg,
                            'traceback': tb_str
                        })
                except Exception:
                    pass
        
        plugin_thread = threading.Thread(target=load_plugins_async, daemon=True)
        plugin_thread.start()
        
        # 启动WebSocket自动连接
        setup_websocket()
        
        return True
    except Exception as e:
        error_msg = f"系统初始化失败: {str(e)}"
        tb_str = traceback.format_exc()
        
        # 记录到前台日志
        try:
            add_error_log(error_msg, tb_str)
        except Exception:
            pass
        
        # 记录到数据库日志
        try:
            if LOG_DB_CONFIG.get('enabled', False):
                add_log_to_db('error', {
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'content': error_msg,
                    'traceback': tb_str
                })
        except Exception:
            pass
            
        return False

def initialize_app():
    """初始化应用，确保只执行一次"""
    global _app_initialized, app
    
    if _app_initialized:
        return app
    
    app = create_app()
    init_systems()
    
    # 初始化Web面板
    if not any(bp.name == 'web' for bp in app.blueprints.values()):
        start_web(app)
    add_framework_log("MBot框架初始化完成")
    
    # 启动DAU分析服务
    if _dau_analytics_available:
        try:
            start_dau_analytics()
        except Exception as e:
            error_msg = f"启动DAU分析服务失败: {str(e)}"
            tb_str = traceback.format_exc()
            
            # 记录到前台日志
            try:
                add_error_log(error_msg, tb_str)
            except Exception:
                pass
            
            # 记录到数据库日志
            try:
                if LOG_DB_CONFIG.get('enabled', False):
                    add_log_to_db('error', {
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'content': error_msg,
                        'traceback': tb_str
                    })
            except Exception:
                pass
    
    _app_initialized = True
    return app

# ===== 9. 主入口 =====
# WSGI应用入口点 - 用于Gunicorn
wsgi_app = initialize_app()

if __name__ == "__main__":
    try:
        app = initialize_app()
        
        import eventlet
        from eventlet import wsgi
        
        # 获取服务器配置
        host = SERVER_CONFIG.get('host', '0.0.0.0')
        port = SERVER_CONFIG.get('port', 5005)
        socket_timeout = SERVER_CONFIG.get('socket_timeout', 30)
        keepalive = SERVER_CONFIG.get('keepalive', True)
        
        print(f"启动MBot服务器 - 监听地址: {host}:{port}")
        
        wsgi.server(
            eventlet.listen((host, port)),
            app,
            log=None,
            log_output=False,
            keepalive=keepalive,
            socket_timeout=socket_timeout
        )
    except Exception as e:
        error_msg = f"MBot服务启动失败: {str(e)}"
        tb_str = traceback.format_exc()
        
        print(error_msg)
        print(tb_str)
        
        # 记录到前台日志
        try:
            add_error_log(error_msg, tb_str)
        except Exception:
            pass
        
        # 记录到数据库日志
        try:
            if LOG_DB_CONFIG.get('enabled', False):
                add_log_to_db('error', {
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'content': error_msg,
                    'traceback': tb_str
                })
        except Exception:
            pass
            
        sys.exit(1)  