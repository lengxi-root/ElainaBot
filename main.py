#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ==================== 性能优化预定义 ====================
# Python 优化标志
import sys
sys.dont_write_bytecode = False  # 保持 .pyc 缓存加速导入

# 预编译正则表达式缓存
import re
re.DOTALL  # 触发 re 模块初始化

# 设置更高效的内存分配器 (Python 3.8+)
try:
    import ctypes
    libc = ctypes.CDLL("msvcrt" if sys.platform == "win32" else "libc.so.6")
except:
    pass

# ==================== Eventlet Monkey Patch ====================
import eventlet
eventlet.monkey_patch(all=True, thread=True, socket=True, select=True, time=True)
import sys, os, time, shutil

def check_config_and_redirect():
    """检查配置文件，如果未配置则启动配置向导"""
    try:
        import importlib.util
        import subprocess
        
        # 加载配置文件
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.py')
        if not os.path.exists(config_path):
            print("❌ 配置文件 config.py 不存在！")
            sys.exit(1)
        
        spec = importlib.util.spec_from_file_location("config", config_path)
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        
        # 获取必填配置项
        appid = str(getattr(config, 'appid', '')).strip()
        secret = str(getattr(config, 'secret', '')).strip()
        
        # 只检查是否为空
        if not appid or not secret:
            print("\n" + "="*60)
            print("  ⚠️  检测到配置未完成，正在启动配置向导...")
            print("="*60 + "\n")
            print("  请填写以下必填项：")
            print("  - appid: 机器人APPID")
            print("  - secret: 机器人密钥")
            print("  - ROBOT_QQ: 机器人QQ号（可选）")
            print("\n" + "="*60 + "\n")
            
            # 启动配置向导
            base_dir = os.path.dirname(os.path.abspath(__file__))
            config_wizard = os.path.join(base_dir, 'main.sc.py')
            
            if not os.path.exists(config_wizard):
                print("❌ main.sc.py 配置向导文件不存在！")
                sys.exit(1)
            
            # 使用当前 Python 解释器启动 main.sc.py
            subprocess.run([sys.executable, config_wizard])
            sys.exit(0)
        
        # 配置检查通过
        print("✅ 配置文件检查通过")
        print(f"   - APPID: {appid}")
        print(f"   - ROBOT_QQ: {getattr(config, 'ROBOT_QQ', 'N/A')}")
        return True
        
    except Exception as e:
        print(f"⚠️  配置检查失败: {e}")
        print("继续启动主程序...")
        return True

def check_python_version():
    required_version = (3, 9)
    current_version = sys.version_info[:2]
    if current_version < required_version:
        print(f"❌ Python版本不符合要求！当前: {current_version[0]}.{current_version[1]}, 要求: {required_version[0]}.{required_version[1]}+")
        sys.exit(1)
    print(f"✅ Python版本检查通过: Python {current_version[0]}.{current_version[1]}")
    return True

def check_dependencies():
    try:
        from importlib.metadata import version, PackageNotFoundError
    except ImportError:
        try:
            from importlib_metadata import version, PackageNotFoundError
        except ImportError:
            print("⚠️  警告: 无法导入依赖检查模块，跳过依赖检查")
            return True
    
    base_dir = os.path.dirname(os.path.abspath(__file__))
    requirements_file = os.path.join(base_dir, 'requirements.txt')
    if not os.path.exists(requirements_file):
        print("⚠️  警告: 未找到 requirements.txt 文件，跳过依赖检查")
        return True
    
    print("🔍 正在检查依赖包...")
    missing_packages = []
    try:
        with open(requirements_file, 'r', encoding='utf-8') as f:
            requirements = f.readlines()
        
        for line in requirements:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '==' in line:
                package_name = line.split('==')[0].strip()
            elif '>=' in line:
                package_name = line.split('>=')[0].strip()
            else:
                package_name = line.strip()
            
            possible_names = [
                package_name, package_name.lower(),
                package_name.lower().replace('_', '-'),
                package_name.lower().replace('-', '_'),
            ]
            
            installed = False
            for check_name in possible_names:
                try:
                    version(check_name)
                    installed = True
                    break
                except PackageNotFoundError:
                    continue
            
            if not installed:
                missing_packages.append(package_name)
        
        if not missing_packages:
            print("✅ 所有依赖包检查通过！")
            return True
        
        print("\n❌ 缺少依赖包:", ', '.join(missing_packages))
        print("💡 pip install -r requirements.txt")
        print("\n按 Enter 继续或 Ctrl+C 退出...")
        try:
            input()
        except KeyboardInterrupt:
            sys.exit(0)
        return True
    except:
        return True

def check_and_replace_config():
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

check_config_and_redirect()
check_python_version()
check_and_replace_config()
check_dependencies()

import json, gc, threading, logging, traceback, random, warnings, signal, multiprocessing
from multiprocessing import Process, Event
from flask import Flask, request, jsonify
from flask_socketio import SocketIO
from config import LOG_DB_CONFIG, WEBSOCKET_CONFIG, SERVER_CONFIG, WEB_CONFIG
from function.Access import BOT凭证, BOTAPI, Json取, Json
from function.httpx_pool import get_pool_manager

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# 预导入常用模块到缓存
import datetime
import hashlib
import urllib.parse

# 创建主框架 logger
logger = logging.getLogger('ElainaBot')

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
_gc_counter = 0
_message_handler_ready = threading.Event()
_plugins_preloaded = False
_message_executor = None

def log_error(error_msg, tb_str=None):
    logger.error(f"{error_msg}\n{tb_str or traceback.format_exc()}")
    add_error_log(error_msg, tb_str or traceback.format_exc())

def cleanup_gc():
    global _gc_counter
    _gc_counter += 1
    if _gc_counter >= 50:  # 降低阈值，更频繁清理
        gc.collect(0)  # 只清理第0代，速度快
        _gc_counter = 0


def log_to_console(message):
    logger.info(message)
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
    
    # 测试logger输出
    test_logger = logging.getLogger('test_logger')
    test_logger.info("✅ Logger测试：控制台输出正常")

sys.excepthook = lambda exctype, value, tb: log_error(f"{exctype.__name__}: {value}", "".join(traceback.format_tb(tb)))

import flask.cli
flask.cli.show_server_banner = lambda *args: None

def create_app():
    flask_app = Flask(__name__)
    flask_app.config['SECRET_KEY'] = 'elainabot_secret'
    flask_app.config['TEMPLATES_AUTO_RELOAD'] = False  # 生产环境关闭自动重载
    flask_app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 31536000  # 静态文件缓存1年
    flask_app.config['JSON_SORT_KEYS'] = False  # 禁用 JSON 键排序
    flask_app.jinja_env.auto_reload = False
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
            global _message_executor
            if _message_executor is None:
                from concurrent.futures import ThreadPoolExecutor
                _message_executor = ThreadPoolExecutor(max_workers=300, thread_name_prefix="MsgHandler")
            http_ctx = {
                'path': request.path,
                'method': request.method,
                'url': request.url,
                'remote_addr': request.remote_addr,
                'headers': dict(request.headers)
            }
            
            _message_executor.submit(process_message_event, data.decode(), http_ctx)
            return "OK"
        elif op == 13:
            from function.sign import Signs
            return Signs().sign(data.decode())
        return "Event not handled", 400
    
    log_to_console("Flask应用创建成功")
    return flask_app

def process_message_event(data, http_context=None):
    if not data:
        return False
    
    global _plugins_preloaded
    if not _plugins_preloaded:
        _message_handler_ready.wait(timeout=5)
    
    try:
        from core.event.MessageEvent import MessageEvent
        from core.plugin.PluginManager import PluginManager
        
        event = MessageEvent(data, http_context=http_context)
        if event.ignore:
            del event
            return False
        
        # 立即执行web实时推送（主线程中执行）
        try:
            if not event.skip_recording:
                import datetime
                timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                event._notify_web_display(timestamp)
        except:
            pass
        
        # 异步执行数据库操作
        def async_db_tasks():
            try:
                if not event.skip_recording:
                    event._record_user_and_group()
                    event._record_message_to_db_only()  # 只执行数据库记录，不包含web推送
                event.record_last_message_id()
            except:
                pass
        
        import threading
        threading.Thread(target=async_db_tasks, daemon=True).start()
        
        try:
            PluginManager.dispatch_message(event)
        except Exception as e:
            log_error(f"插件处理失败: {str(e)}")
        
        del event, data
        cleanup_gc()
        return False
    except Exception as e:
        log_error(f"消息处理异常: {str(e)}")
        return False

async def handle_ws_message(raw_data):
    global _message_executor
    if _message_executor is None:
        from concurrent.futures import ThreadPoolExecutor
        _message_executor = ThreadPoolExecutor(max_workers=300, thread_name_prefix="MsgHandler")
    _message_executor.submit(process_message_event, raw_data)

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
        loop = None
        client = None
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
                if client:
                    del client
                if loop:
                    try:
                        pending = asyncio.all_tasks(loop)
                        for task in pending:
                            task.cancel()
                        if pending:
                            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    except:
                        pass
                    try:
                        loop.close()
                    except:
                        pass
                    del loop
                gc.collect()
            except:
                pass

def setup_websocket():
    if WEBSOCKET_CONFIG.get('enabled', False) and WEBSOCKET_CONFIG.get('auto_connect', True):
        from config import appid, secret
        if appid and secret:
            threading.Thread(target=run_websocket_client, daemon=True).start()
            log_to_console("WebSocket自动连接启动成功")

def init_systems(is_subprocess=False):
    global _message_handler_ready, _plugins_preloaded
    setup_logging()
    
    # 优化 GC 设置
    gc.enable()
    gc.set_threshold(500, 10, 5)  # 更激进的第0代回收
    gc.collect(0)
    
    # 禁用 GC 调试
    gc.set_debug(0)
    
    log_to_console("垃圾回收系统初始化成功")
    
    def init_critical_systems():
        try:
            from function.database import Database
            Database()
            log_to_console("数据库系统初始化成功")
            
            # 初始化 Redis
            try:
                from function.redis_pool import init_redis
                status, message = init_redis()
                if status == 'success':
                    log_to_console(f"✅ {message}")
                elif status == 'disabled':
                    log_to_console(f"⏸️ {message}")
                elif status == 'no_module':
                    log_to_console(f"⚠️ {message}")
                else:  # failed
                    log_to_console(f"❌ {message}")
            except Exception as e:
                log_to_console(f"⚠️ Redis初始化异常: {e}")
            
            from core.plugin.PluginManager import PluginManager
            PluginManager.load_plugins()
            log_to_console("插件系统初始化成功")
            _plugins_preloaded = True
            _message_handler_ready.set()
        except Exception as e:
            log_error(f"系统初始化失败: {str(e)}")
            _message_handler_ready.set()
    
    threading.Thread(target=init_critical_systems, daemon=True).start()
    if not is_subprocess:
        setup_websocket()
    return True

def initialize_app():
    global _app_initialized, app
    if _app_initialized:
        return app
    app = create_app()
    init_systems()
    if _web_available:
        start_web(app)
        log_to_console("Web面板服务已集成到主进程")
    if _dau_available:
        start_dau_analytics()
        log_to_console("DAU分析服务启动成功")
    _app_initialized = True
    return app

wsgi_app = initialize_app()

def signal_handler(signum, frame):
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
    logger.info(f"🚀 主框架启动成功！")
    logger.info(f"📡 主服务器地址: {host}:{port}")
    if _web_available:
        web_token = WEB_CONFIG.get('access_token', '')
        display_host = 'localhost' if host == '0.0.0.0' else host
        web_url = f"http://{display_host}:{port}/web/"
        if web_token:
            web_url += f"?token={web_token}"
        logger.info(f"🌐 Web管理面板: {web_url}")
    logger.info(f"⚡ 系统就绪，等待消息处理...")
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
        if _dau_available:
            stop_dau_analytics()
        sys.exit(0)  