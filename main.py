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
import multiprocessing
from multiprocessing import Process, Queue, Event

import signal

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
try:
    from web.app import start_web, add_plugin_log, add_framework_log, add_error_log
    _web_available = True
except ImportError:
    _web_available = False
    def add_plugin_log(*args, **kwargs): pass
    def add_framework_log(*args, **kwargs): pass 
    def add_error_log(*args, **kwargs): pass

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

# 进程管理变量
_web_process = None
_web_process_event = Event()

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

def start_web_process():
    """Web进程启动函数"""
    setup_logging()
    log_to_console("Web进程已启动")
    
    from web.app import start_web
    import eventlet
    from eventlet import wsgi
    
    web_host = SERVER_CONFIG.get('host', '0.0.0.0')
    web_port = SERVER_CONFIG.get('web_port', 5002)
    
    log_to_console(f"Web面板独立进程启动在 {web_host}:{web_port}")
    
    web_app, web_socketio = start_web(main_app=None)
    
    wsgi.server(
        eventlet.listen((web_host, web_port)),
        web_app,
        log=None,
        log_output=False
    )

def start_web_dual_process():
    """启动Web面板作为独立进程"""
    global _web_process
    
    _web_process = Process(target=start_web_process, daemon=True)
    _web_process.start()
    
    web_port = SERVER_CONFIG.get('web_port', 5002)
    web_host = SERVER_CONFIG.get('host', '0.0.0.0')
    display_host = 'localhost' if web_host == '0.0.0.0' else web_host
    
    log_to_console(f"Web面板独立进程已启动，PID: {_web_process.pid}")
    
    # 构造Web面板访问URL
    web_token = WEB_SECURITY.get('access_token', '')
    web_url = f"http://{display_host}:{web_port}/web/"
    if web_token:
        web_url += f"?token={web_token}"
    log_to_console(f"🌐 Web管理面板: {web_url}")
    
    return True

def stop_web_process():
    """停止Web进程"""
    global _web_process, _web_process_event
    
    _web_process_event.set()
    
    if _web_process and _web_process.is_alive():
        log_to_console("正在停止Web进程...")
        _web_process.terminate()
        _web_process.join(timeout=5)
        log_to_console("Web进程已停止")

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
                    args=(data.decode(),),
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

# record_message函数已移除，消息记录现在在MessageEvent初始化时自动完成

def _process_message_concurrent(event):
    """统一的并发消息处理逻辑"""
    import concurrent.futures
    from core.plugin.PluginManager import PluginManager
    
    result = [False]
    
    def plugin_task():
        """插件处理任务"""
        try:
            plugin_manager = PluginManager()
            result[0] = plugin_manager.dispatch_message(event)
        except Exception as e:
            log_error(f"插件处理失败: {str(e)}")
    
    def storage_and_web_task():
        """数据库存储+web推送任务"""
        try:
            if not event.skip_recording:
                # 先存储再推送
                event._record_message_to_db_only()
                import datetime
                timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                event._notify_web_display(timestamp)
        except Exception as e:
            log_error(f"存储推送失败: {str(e)}")
    
    # 异步执行，避免阻塞主线程
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        # 提交任务但不等待完成，避免阻塞HTTP响应
        plugin_future = executor.submit(plugin_task)
        storage_future = executor.submit(storage_and_web_task)
        
        # 只等待插件处理结果，存储推送异步进行
        try:
            plugin_future.result(timeout=0.1)  # 短暂等待插件结果
        except concurrent.futures.TimeoutError:
            # 插件处理超时，让它在后台继续运行
            pass
    
    # 插件处理完成后，更新ID缓存（定期批量保存）
    try:
        event.record_last_message_id()
    except Exception as e:
        log_error(f"更新消息ID缓存失败: {str(e)}")
    
    return result[0]

def process_message_event(data):
    """处理消息事件"""
    if not data:
        return False
        
    try:
        from core.event.MessageEvent import MessageEvent
        event = MessageEvent(data)
        if event.ignore:
            return False
        
        result = _process_message_concurrent(event)
        cleanup_gc()
        return result
    except Exception as e:
        log_error(f"消息处理失败: {str(e)}")
        return False

async def handle_ws_message(raw_data):
    """处理WebSocket消息 - 与webhook使用相同的处理流程"""
    threading.Thread(
        target=process_message_event,
        args=(raw_data,),
        daemon=True
    ).start()

async def create_websocket_client():
    """创建WebSocket客户端"""
    from function.ws_client import create_qq_bot_client
    
    try:
        log_to_console("正在获取网关地址...")
        client = await create_qq_bot_client(WEBSOCKET_CONFIG)
        if not client:
            raise Exception("无法获取网关地址或创建客户端")
        
        log_to_console("正在配置事件处理器...")
        client.add_handler('message', handle_ws_message)
        client.add_handler('connect', lambda data: log_to_console("WebSocket连接已建立"))
        client.add_handler('disconnect', lambda data: log_to_console("WebSocket连接已断开"))
        client.add_handler('error', lambda data: log_error(f"WebSocket错误: {data.get('error', '')}"))
        client.add_handler('ready', lambda data: log_to_console(
            f"WebSocket已就绪 - Bot: {data.get('bot_info', {}).get('username', 'Unknown')}"
        ))
        
        return client
        
    except Exception as e:
        log_error(f"创建WebSocket客户端时发生错误: {str(e)}")
        raise

def run_websocket_client():
    """运行WebSocket客户端"""
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            import asyncio
            import sys
            
            # Windows系统下设置正确的事件循环策略
            if sys.platform == 'win32':
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
            
            # 确保清理之前的事件循环
            try:
                current_loop = asyncio.get_event_loop()
                if current_loop.is_running():
                    current_loop.close()
            except:
                pass
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def websocket_main():
                log_to_console(f"正在创建WebSocket客户端...")
                client = await create_websocket_client()
                log_to_console("WebSocket客户端已创建，开始连接...")
                await client.start()
            
            loop.run_until_complete(websocket_main())
            log_to_console("WebSocket客户端连接成功")
            break  # 成功后跳出重试循环
            
        except KeyboardInterrupt:
            log_to_console("WebSocket客户端被用户中断")
            break
        except Exception as e:
            log_error(f"WebSocket客户端运行失败 (第 {attempt + 1}/{max_retries} 次): {str(e)}")
            if attempt < max_retries - 1:
                log_to_console(f"等待 10 秒后重试...")
                time.sleep(10)  # 增加等待时间



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
    
    # 集成Web面板服务
    if _web_available and SERVER_CONFIG.get('enable_web', True):
        if SERVER_CONFIG.get('web_dual_process', False):
            # 双进程模式：启动独立的Web进程
            start_web_dual_process()
            log_to_console("Web面板独立进程启动成功")
        else:
            # 单进程模式：集成到主进程
            start_web(app)
            log_to_console("Web面板服务已集成到主进程")
    
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

def signal_handler(signum, frame):
    """信号处理器"""
    print("\n收到退出信号，正在关闭服务...")
    
    if SERVER_CONFIG.get('web_dual_process', False):
        stop_web_process()
    
    if _dau_available:
        stop_dau_analytics()
    
    sys.exit(0)

def start_main_process():
    """主进程启动函数"""
    try:
        # 设置信号处理
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        app = initialize_app()
        
        import eventlet
        from eventlet import wsgi
        
        host = SERVER_CONFIG.get('host', '0.0.0.0')
        port = SERVER_CONFIG.get('port', 5001)
        socket_timeout = SERVER_CONFIG.get('socket_timeout', 30)
        keepalive = SERVER_CONFIG.get('keepalive', True)
        
        logging.info(f"🚀 主框架启动成功！")
        logging.info(f"📡 主服务器地址: {host}:{port}")
        
        if _web_available and SERVER_CONFIG.get('enable_web', True):
            if not SERVER_CONFIG.get('web_dual_process', False):
                # 单进程模式：Web面板集成在主端口
                web_token = WEB_SECURITY.get('access_token', '')
                display_host = 'localhost' if host == '0.0.0.0' else host
                web_url = f"http://{display_host}:{port}/web/"
                if web_token:
                    web_url += f"?token={web_token}"
                logging.info(f"🌐 Web管理面板: {web_url}")
            # 双进程模式的URL在start_web_dual_process函数中已经输出
        
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
        log_error(f"主进程启动失败: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    # 设置多进程启动方法，确保跨平台兼容性
    if hasattr(multiprocessing, 'set_start_method'):
        try:
            multiprocessing.set_start_method('spawn', force=True)
        except RuntimeError:
            # 如果已经设置过启动方法，则忽略错误
            pass
    
    try:
        start_main_process()
    except KeyboardInterrupt:
        print("\n收到中断信号，正在关闭...")
    except Exception as e:
        print(f"ElainaBot服务启动失败: {str(e)}")
    finally:
        if SERVER_CONFIG.get('web_dual_process', False):
            stop_web_process()
        
        if _dau_available:
            stop_dau_analytics()
        
        sys.exit(0)  