#!/usr/bin/env python
# -*- coding: utf-8 -*-

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

warnings.filterwarnings("ignore", "Corrupt EXIF data", UserWarning)
warnings.filterwarnings("ignore", category=UserWarning, module="PIL")

try:
    import eventlet
    eventlet.monkey_patch()
except ImportError:
    logging.warning("WARNING: eventlet not found, some features may not work properly")

from flask import Flask, request, jsonify
from flask_socketio import SocketIO
from config import LOG_CONFIG, LOG_DB_CONFIG, WEBSOCKET_CONFIG, SERVER_CONFIG, WEB_SECURITY
from web.app import start_web, add_received_message, add_plugin_log, add_framework_log, add_error_log

try:
    from function.log_db import add_log_to_db
except ImportError:
    def add_log_to_db(log_type, log_data):
        return False

from function.Access import BOT凭证, BOTAPI, Json取, Json
from function.httpx_pool import get_pool_manager

try:
    from function.dau_analytics import start_dau_analytics, stop_dau_analytics
    _dau_available = True
except ImportError:
    _dau_available = False
    def start_dau_analytics():
        pass
    def stop_dau_analytics():
        pass

# 全局状态变量
_logging_initialized = False
_app_initialized = False
http_pool = get_pool_manager()

# 通用错误处理函数
def log_error(error_msg, tb_str=None):
    """统一的错误日志记录"""
    if tb_str is None:
        tb_str = traceback.format_exc()
    
    # 只使用logging模块输出错误，避免重复
    logging.error(f"ERROR: {error_msg}")
    if tb_str:
        logging.error(f"{tb_str}")
    
    try:
        add_error_log(error_msg, tb_str)
    except:
        pass
    
    try:
        if LOG_DB_CONFIG.get('enabled', False):
            add_log_to_db('error', {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'content': error_msg,
                'traceback': tb_str
            })
    except:
        pass

def cleanup_gc():
    """执行垃圾回收"""
    if random.random() < 0.05:
        gc.collect(0)

def log_to_console(message):
    """输出消息到宝塔项目日志"""
    # 只使用logging模块输出，避免重复
    logging.info(f"{message}")
    
    # 也推送到Web面板（如果可用）
    try:
        add_framework_log(message)
    except:
        pass



def setup_logging():
    """初始化日志系统"""
    global _logging_initialized
    
    if _logging_initialized:
        return
    
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 配置适合宝塔环境的日志格式，时间不显示年份
    formatter = logging.Formatter('[ElainaBot] %(asctime)s - %(levelname)s - %(message)s', 
                                 datefmt='%m-%d %H:%M:%S')
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    
    # 禁用第三方库日志
    for logger_name in ['werkzeug', 'socketio', 'engineio', 'urllib3']:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)
        logger.propagate = False
    
    _logging_initialized = True
    log_to_console("日志系统初始化成功")

def global_exception_handler(exctype, value, tb):
    """全局异常处理"""
    error_msg = f"未捕获的异常: {exctype.__name__}: {value}"
    tb_str = "".join(traceback.format_tb(tb))
    log_error(error_msg, tb_str)
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = global_exception_handler

# 禁用Flask启动横幅
import flask.cli
flask.cli.show_server_banner = lambda *args: None

def create_app():
    """创建Flask应用"""
    flask_app = Flask(__name__)
    flask_app.config['SECRET_KEY'] = 'elainabot_secret'
    flask_app.config['TEMPLATES_AUTO_RELOAD'] = True
    flask_app.jinja_env.auto_reload = True
    flask_app.logger.disabled = True
    
    socketio = SocketIO(
        flask_app,
        cors_allowed_origins="*",
        async_mode='eventlet',
        logger=False,
        engineio_logger=False
    )
    flask_app.socketio = socketio
    
    @flask_app.route('/', methods=['GET', 'POST'])
    def handle_request():
        """主入口路由"""
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
            
            if op == 0:
                threading.Thread(
                    target=process_message_event, 
                    args=(json_data, data.decode()),
                    daemon=True
                ).start()
                return "OK"
            elif op == 13:
                from function.sign import Signs
                sign = Signs()
                return sign.sign(data.decode())
                
            return "Event not handled", 400
            
        except Exception as e:
            log_error(f"处理请求时发生错误: {str(e)}")
            return "Server error", 500
    
    log_to_console("Flask应用创建成功")
    return flask_app

def record_message(message_data):
    """记录消息到Web面板"""
    try:
        add_received_message(message_data)
    except Exception as e:
        log_error(f"记录消息失败: {str(e)}")

def process_message_event(data, raw_data=None):
    """处理消息事件"""
    if not data:
        return False
        
    record_message(data)
    
    try:
        from core.plugin.PluginManager import PluginManager
        from core.event.MessageEvent import MessageEvent
        
        plugin_manager = PluginManager()
        event = MessageEvent(raw_data if raw_data else data)
        result = plugin_manager.dispatch_message(event)
        
        cleanup_gc()
        return result
        
    except Exception as e:
        log_error(f"插件系统处理消息失败: {str(e)}")
        return False

async def handle_ws_message(event):
    """处理WebSocket消息"""
    try:
        if hasattr(event, 'raw_data'):
            record_message(event.raw_data)
        
        def process_plugin():
            try:
                from core.plugin.PluginManager import PluginManager
                plugin_manager = PluginManager()
                plugin_manager.dispatch_message(event)
                cleanup_gc()
            except Exception as e:
                log_error(f"WebSocket插件处理失败: {str(e)}")
        
        threading.Thread(target=process_plugin, daemon=True).start()
        
    except Exception as e:
        log_error(f"WebSocket消息处理失败: {str(e)}")

async def create_websocket_client():
    """创建WebSocket客户端"""
    from function.ws_client import create_qq_bot_client
    
    client = await create_qq_bot_client(WEBSOCKET_CONFIG)
    if not client:
        raise Exception("创建WebSocket客户端失败")
    
    client.add_handler('message', handle_ws_message)
    client.add_handler('connect', lambda data: log_to_console("WebSocket连接已建立"))
    client.add_handler('disconnect', lambda data: log_to_console("WebSocket连接已断开"))
    client.add_handler('error', lambda data: log_error(f"WebSocket错误: {data.get('error', '')}"))
    client.add_handler('ready', lambda data: log_to_console(
        f"WebSocket已就绪 - Bot: {data.get('bot_info', {}).get('username', 'Unknown')}"
    ))
    
    return client

def run_websocket_client():
    """运行WebSocket客户端"""
    try:
        import asyncio
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def websocket_main():
            client = await create_websocket_client()
            await client.start()
        
        loop.run_until_complete(websocket_main())
        
    except Exception as e:
        log_error(f"WebSocket客户端运行失败: {str(e)}")

def setup_websocket():
    """设置WebSocket连接"""
    if not WEBSOCKET_CONFIG.get('enabled', False) or not WEBSOCKET_CONFIG.get('auto_connect', True):
        return
    
    try:
        from config import appid, secret
        if not appid or not secret:
            log_error("机器人认证配置不完整，请检查 config.py 中的 appid 和 secret")
            return
        
        ws_thread = threading.Thread(target=run_websocket_client, daemon=True, name="WebSocketClient")
        ws_thread.start()
        
        log_to_console("WebSocket自动连接启动成功")
        
    except Exception as e:
        log_error(f"WebSocket设置失败: {str(e)}")

def init_systems():
    """初始化系统组件"""
    try:
        setup_logging()
        
        gc.enable()
        gc.set_threshold(700, 10, 5)
        gc.collect(0)
        log_to_console("垃圾回收系统初始化成功")
        
        def load_plugins_async():
            try:
                from core.plugin.PluginManager import PluginManager
                plugin_manager = PluginManager()
                plugin_manager.load_plugins()
                log_to_console("插件系统初始化成功")
            except Exception as e:
                log_error(f"插件系统初始化失败: {str(e)}")
        
        plugin_thread = threading.Thread(target=load_plugins_async, daemon=True)
        plugin_thread.start()
        
        setup_websocket()
        
        return True
    except Exception as e:
        log_error(f"系统初始化失败: {str(e)}")
        return False

def initialize_app():
    """初始化应用"""
    global _app_initialized, app
    
    if _app_initialized:
        return app
    
    app = create_app()
    init_systems()
    
    if not any(bp.name == 'web' for bp in app.blueprints.values()):
        start_web(app)
        log_to_console("Web面板初始化成功")
    
    if _dau_available:
        try:
            start_dau_analytics()
            log_to_console("DAU分析服务启动成功")
        except Exception as e:
            log_error(f"启动DAU分析服务失败: {str(e)}")
    
    _app_initialized = True
    return app

# WSGI应用入口点
wsgi_app = initialize_app()

if __name__ == "__main__":
    try:
        app = initialize_app()
        
        import eventlet
        from eventlet import wsgi
        
        host = SERVER_CONFIG.get('host', '0.0.0.0')
        port = SERVER_CONFIG.get('port', 5005)
        socket_timeout = SERVER_CONFIG.get('socket_timeout', 30)
        keepalive = SERVER_CONFIG.get('keepalive', True)
        web_token = WEB_SECURITY.get('access_token', '')
        
        # 构建Web面板URL
        display_host = 'localhost' if host == '0.0.0.0' else host
        web_url = f"http://{display_host}:{port}/web/"
        if web_token:
            web_url += f"?token={web_token}"
        
        logging.info(f"🚀 框架启动成功！")
        logging.info(f"📡 服务器地址: {host}:{port}")
        logging.info(f"🌐 Web管理面板: {web_url}")
        logging.info(f"⚡ 系统就绪，等待消息处理...")
        
        wsgi.server(
            eventlet.listen((host, port)),
            app,
            log=None,
            log_output=False,
            keepalive=keepalive,
            socket_timeout=socket_timeout
        )
    except Exception as e:
        log_error(f"ElainaBot服务启动失败: {str(e)}")
        sys.exit(1)  