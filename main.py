#!/usr/bin/env python
# -*- coding: utf-8 -*-

def check_and_replace_config():
    import os
    import shutil
    import time
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_new_path = os.path.join(base_dir, 'web', 'config_new.py')
    config_path = os.path.join(base_dir, 'config.py')
    backup_dir = os.path.join(base_dir, 'data', 'config')
    
    if os.path.exists(config_new_path):
        if os.path.exists(config_path):
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = time.strftime('%Y%m%d_%H%M%S')
            shutil.copy2(config_path, os.path.join(backup_dir, f'config_backup_{timestamp}.py'))
        shutil.move(config_new_path, config_path)

check_and_replace_config()

import eventlet
eventlet.monkey_patch()

import sys, os, time, json, gc, threading, logging, traceback, random, warnings, signal, multiprocessing
from multiprocessing import Process, Event
from flask import Flask, request, jsonify
from flask_socketio import SocketIO
from config import LOG_CONFIG, LOG_DB_CONFIG, WEBSOCKET_CONFIG, SERVER_CONFIG, WEB_SECURITY
from function.Access import BOT凭证, BOTAPI, Json取, Json
from function.httpx_pool import get_pool_manager

warnings.filterwarnings("ignore", category=UserWarning)

try:
    from web.app import start_web, add_framework_log, add_error_log
    _web_available = True
except:
    _web_available = False
    add_framework_log = add_error_log = lambda *a, **k: None

try:
    from function.log_db import add_log_to_db
except:
    add_log_to_db = lambda *a, **k: False

try:
    from function.dau_analytics import start_dau_analytics, stop_dau_analytics
    _dau_available = True
except:
    _dau_available = False
    start_dau_analytics = stop_dau_analytics = lambda: None

_logging_initialized = False
_app_initialized = False
http_pool = get_pool_manager()
_web_process = None
_web_process_event = Event()

def log_error(error_msg, tb_str=None):
    logging.error(f"{error_msg}\n{tb_str or traceback.format_exc()}")
    add_error_log(error_msg, tb_str or traceback.format_exc())

def cleanup_gc():
    if random.random() < 0.05:
        gc.collect(0)

def start_web_process():
    setup_logging()
    log_to_console("Web进程已启动")
    init_systems(is_subprocess=True)
    from web.app import start_web
    from eventlet import wsgi
    web_host = SERVER_CONFIG.get('host', '0.0.0.0')
    web_port = SERVER_CONFIG.get('web_port', 5002)
    log_to_console(f"Web面板独立进程启动在 {web_host}:{web_port}")
    web_app, web_socketio = start_web(main_app=None, is_subprocess=True)
    wsgi.server(eventlet.listen((web_host, web_port)), web_app, log=None, log_output=False)

def start_web_dual_process():
    global _web_process
    _web_process = Process(target=start_web_process, daemon=True)
    _web_process.start()
    web_port = SERVER_CONFIG.get('web_port', 5002)
    web_host = SERVER_CONFIG.get('host', '0.0.0.0')
    display_host = 'localhost' if web_host == '0.0.0.0' else web_host
    log_to_console(f"Web面板独立进程已启动，PID: {_web_process.pid}")
    web_token = WEB_SECURITY.get('access_token', '')
    web_url = f"http://{display_host}:{web_port}/web/{'?token=' + web_token if web_token else ''}"
    log_to_console(f"🌐 Web管理面板: {web_url}")
    return True

def stop_web_process():
    global _web_process
    _web_process_event.set()
    if _web_process and _web_process.is_alive():
        log_to_console("正在停止Web进程...")
        _web_process.terminate()
        _web_process.join(timeout=5)
        log_to_console("Web进程已停止")

def log_to_console(message):
    logging.info(message)
    add_framework_log(message)



def setup_logging():
    global _logging_initialized
    if _logging_initialized:
        return
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    formatter = logging.Formatter('[ElainaBot] %(asctime)s - %(levelname)s - %(message)s', datefmt='%m-%d %H:%M:%S')
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    for logger_name in ['werkzeug', 'socketio', 'engineio', 'urllib3']:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.ERROR)
        logger.propagate = False
    _logging_initialized = True
    log_to_console("日志系统初始化成功")

sys.excepthook = lambda exctype, value, tb: log_error(f"{exctype.__name__}: {value}", "".join(traceback.format_tb(tb)))

import flask.cli
flask.cli.show_server_banner = lambda *args: None

def create_app():
    flask_app = Flask(__name__)
    flask_app.config['SECRET_KEY'] = 'elainabot_secret'
    flask_app.config['TEMPLATES_AUTO_RELOAD'] = True
    flask_app.jinja_env.auto_reload = True
    flask_app.logger.disabled = True
    socketio = SocketIO(flask_app, cors_allowed_origins="*", async_mode='eventlet', logger=False, engineio_logger=False)
    flask_app.socketio = socketio
    
    @flask_app.route('/', methods=['GET', 'POST'])
    def handle_request():
        if request.method == 'GET':
            return jsonify({"message": "The service is temporarily unavailable"}), 200
        data = request.get_data()
        if not data:
            return "No data received", 400
        json_data = json.loads(data)
        op = json_data.get("op")
        if op == 0:
            threading.Thread(target=process_message_event, args=(data.decode(),), daemon=True).start()
            return "OK"
        elif op == 13:
            from function.sign import Signs
            return Signs().sign(data.decode())
        return "Event not handled", 400
    
    log_to_console("Flask应用创建成功")
    return flask_app

def _process_message_concurrent(event):
    import concurrent.futures
    from core.plugin.PluginManager import PluginManager
    result = [False]
    
    def plugin_task():
        try:
            result[0] = PluginManager.dispatch_message(event)
        except Exception as e:
            log_error(f"插件处理失败: {str(e)}")
    
    def storage_task():
        if not event.skip_recording:
            event._record_message_to_db_only()
            import datetime
            event._notify_web_display(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        plugin_future = executor.submit(plugin_task)
        executor.submit(storage_task)
        try:
            plugin_future.result(timeout=0.1)
        except concurrent.futures.TimeoutError:
            pass
    
    event.record_last_message_id()
    return result[0]

def process_message_event(data):
    if not data:
        return False
    from core.event.MessageEvent import MessageEvent
    event = MessageEvent(data)
    if event.ignore:
        return False
    result = _process_message_concurrent(event)
    cleanup_gc()
    return result

async def handle_ws_message(raw_data):
    threading.Thread(target=process_message_event, args=(raw_data,), daemon=True).start()

async def create_websocket_client():
    from function.ws_client import create_qq_bot_client
    log_to_console("正在获取网关地址...")
    client = await create_qq_bot_client(WEBSOCKET_CONFIG)
    if not client:
        raise Exception("无法获取网关地址或创建客户端")
    log_to_console("正在配置事件处理器...")
    client.add_handler('message', handle_ws_message)
    client.add_handler('connect', lambda d: log_to_console("WebSocket连接已建立"))
    client.add_handler('disconnect', lambda d: log_to_console("WebSocket连接已断开"))
    client.add_handler('error', lambda d: log_error(f"WebSocket错误: {d.get('error', '')}"))
    client.add_handler('ready', lambda d: log_to_console(f"WebSocket已就绪 - Bot: {d.get('bot_info', {}).get('username', '二次转发接收模式')}"))
    return client

def run_websocket_client():
    import asyncio
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    for attempt in range(3):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            log_to_console(f"正在创建WebSocket客户端...")
            client = loop.run_until_complete(create_websocket_client())
            log_to_console("WebSocket客户端已创建，开始连接...")
            loop.run_until_complete(client.start())
            log_to_console("WebSocket客户端连接成功")
            break
        except KeyboardInterrupt:
            log_to_console("WebSocket客户端被用户中断")
            break
        except Exception as e:
            log_error(f"WebSocket客户端运行失败 (第 {attempt + 1}/3 次): {str(e)}")
            if attempt < 2:
                log_to_console(f"等待 10 秒后重试...")
                time.sleep(10)
        finally:
            try:
                loop.close()
            except:
                pass

def setup_websocket():
    if WEBSOCKET_CONFIG.get('enabled', False) and WEBSOCKET_CONFIG.get('auto_connect', True):
        from config import appid, secret
        if appid and secret:
            threading.Thread(target=run_websocket_client, daemon=True).start()
            log_to_console("WebSocket自动连接启动成功")

def init_systems(is_subprocess=False):
    setup_logging()
    gc.enable()
    gc.set_threshold(700, 10, 5)
    gc.collect(0)
    log_to_console("垃圾回收系统初始化成功")
    
    def load_plugins():
        from core.plugin.PluginManager import PluginManager
        PluginManager.load_plugins()
        log_to_console("插件系统初始化成功")
    
    threading.Thread(target=load_plugins, daemon=True).start()
    
    if not is_subprocess:
        setup_websocket()
    else:
        log_to_console("子进程模式：跳过WebSocket初始化")
    
    return True

def initialize_app():
    global _app_initialized, app
    if _app_initialized:
        return app
    app = create_app()
    init_systems()
    if _web_available:
        if SERVER_CONFIG.get('web_dual_process', False):
            start_web_dual_process()
            log_to_console("Web面板独立进程启动成功")
        else:
            start_web(app)
            log_to_console("Web面板服务已集成到主进程")
    if _dau_available:
        start_dau_analytics()
        log_to_console("DAU分析服务启动成功")
    _app_initialized = True
    return app

wsgi_app = initialize_app()

def signal_handler(signum, frame):
    if SERVER_CONFIG.get('web_dual_process', False):
        stop_web_process()
    if _dau_available:
        stop_dau_analytics()
    sys.exit(0)

def start_main_process():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    app = initialize_app()
    from eventlet import wsgi
    host = SERVER_CONFIG.get('host', '0.0.0.0')
    port = SERVER_CONFIG.get('port', 5001)
    logging.info(f"🚀 主框架启动成功！")
    logging.info(f"📡 主服务器地址: {host}:{port}")
    if _web_available and not SERVER_CONFIG.get('web_dual_process', False):
        web_token = WEB_SECURITY.get('access_token', '')
        display_host = 'localhost' if host == '0.0.0.0' else host
        web_url = f"http://{display_host}:{port}/web/"
        if web_token:
            web_url += f"?token={web_token}"
        logging.info(f"🌐 Web管理面板: {web_url}")
    logging.info(f"⚡ 系统就绪，等待消息处理...")
    wsgi.server(eventlet.listen((host, port)), app, log=None, log_output=False, keepalive=True, socket_timeout=30)

if __name__ == "__main__":
    if hasattr(multiprocessing, 'set_start_method'):
        try:
            multiprocessing.set_start_method('spawn', force=True)
        except:
            pass
    try:
        start_main_process()
    except KeyboardInterrupt:
        pass
    finally:
        if SERVER_CONFIG.get('web_dual_process', False):
            stop_web_process()
        if _dau_available:
            stop_dau_analytics()
        sys.exit(0)  