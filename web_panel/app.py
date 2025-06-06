# 重新导入Flask组件
# ===== 1. 标准库导入 =====
import os
import sys
import glob
import json
import time
import re
import threading
import traceback
import importlib.util
import functools
import gc  # 导入垃圾回收模块
import warnings
import logging
from datetime import datetime
from collections import deque

# ===== 2. 第三方库导入 =====
from flask import Flask, render_template, request, jsonify, Blueprint
from flask_socketio import SocketIO
from flask_cors import CORS
import psutil
import urllib3

# ===== 3. 自定义模块导入 =====
from config import LOG_DB_CONFIG
try:
    from function.log_db import add_log_to_db
except ImportError:
    def add_log_to_db(log_type, log_data):
        return False

# ===== 4. 全局配置与变量 =====
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # 禁用urllib3不安全请求的警告
START_TIME = datetime.now()  # 记录框架启动时间
PREFIX = '/web'  # 路径前缀
web_panel = Blueprint('web_panel', __name__, 
                     static_url_path=f'{PREFIX}/static',
                     static_folder='static',  
                     template_folder='templates')
socketio = None  # Socket.IO实例 - 会在start_web_panel中设置
MAX_LOGS = 1000  # 最大保存日志数量
received_messages = deque(maxlen=MAX_LOGS)
plugin_logs = deque(maxlen=MAX_LOGS)
framework_logs = deque(maxlen=MAX_LOGS)
error_logs = deque(maxlen=MAX_LOGS)
_last_gc_time = 0  # 上次垃圾回收时间
_last_gc_log_time = 0  # 上次推送垃圾回收日志的时间
_gc_interval = 30  # 垃圾回收间隔(秒)
_gc_log_interval = 120  # 垃圾回收日志推送间隔(秒)
plugins_info = []  # 存储插件信息
from core.event.MessageEvent import MessageEvent  # 导入MessageEvent用于消息解析

# ===== 5. 装饰器与通用函数 =====
def catch_error(func):
    """捕获错误的装饰器，记录到错误日志"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_msg = f"{func.__name__} 执行出错: {str(e)}"
            tb_info = traceback.format_exc()
            print(error_msg)
            print(tb_info)
            if 'add_error_log' in globals() and 'socketio' in globals() and socketio is not None:
                add_error_log(error_msg, tb_info)
            else:
                log_data = {
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'content': error_msg,
                    'traceback': tb_info
                }
                add_log_to_db('error', log_data)
            return None
    return wrapper

# ===== 6. 日志处理类 =====
class LogHandler:
    """
    统一日志处理基类，减少重复代码。
    支持内存队列、数据库写入、SocketIO推送。
    """
    def __init__(self, log_type, max_logs=MAX_LOGS):
        self.log_type = log_type
        self.logs = deque(maxlen=max_logs)
        if log_type == 'received':
            self.global_logs = received_messages
        elif log_type == 'plugin':
            self.global_logs = plugin_logs
        elif log_type == 'framework':
            self.global_logs = framework_logs
        elif log_type == 'error':
            self.global_logs = error_logs
    def add(self, content, traceback_info=None, skip_db=False):
        """
        添加日志条目，支持SocketIO推送和数据库写入。
        :param content: 日志内容
        :param traceback_info: 错误调用栈信息（可选）
        :param skip_db: 是否跳过数据库写入
        """
        global socketio
        entry = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'content': content
        }
        if traceback_info:
            entry['traceback'] = traceback_info
        self.logs.append(entry)
        self.global_logs.append(entry)
        if not skip_db:
            add_log_to_db(self.log_type, entry)
            self.logs.clear()
            self.global_logs.clear()
        if socketio is not None:
            try:
                socketio.emit('new_message', {
                    'type': self.log_type,
                    'data': entry
                }, namespace=PREFIX)
            except Exception as e:
                print(f"推送日志到前端失败: {str(e)}")
        return entry

# ===== 7. 日志处理器实例 =====
received_handler = LogHandler('received')
plugin_handler = LogHandler('plugin')
framework_handler = LogHandler('framework')
error_handler = LogHandler('error')

# ===== 8. 日志与消息相关API =====
@catch_error
def add_received_message(message):
    """
    添加接收到的消息日志，使用MessageEvent解析消息。
    支持原始字符串和结构化消息。
    """
    if isinstance(message, str) and not message.startswith('{'):
        formatted_message = message
        user_id = "未知用户"
        group_id = "c2c"
        pure_content = message
    else:
        try:
            event = MessageEvent(message)
            user_id = event.user_id or "未知用户"
            group_id = event.group_id or "c2c"
            pure_content = event.content or ""
            is_interaction = getattr(event, 'event_type', None) == 'INTERACTION_CREATE'
            if is_interaction:
                chat_type = event.get('d/chat_type')
                scene = event.get('d/scene')
                if (chat_type == 2 or scene == 'c2c'):
                    group_id = "c2c"
                    user_id = event.get('d/user_openid') or user_id
                button_data = event.get('d/data/resolved/button_data')
                if button_data and not pure_content:
                    pure_content = button_data
            is_group_add = getattr(event, 'message_type', None) == getattr(event, 'GROUP_ADD_ROBOT', 'GROUP_ADD_ROBOT')
            if (
                (not user_id or user_id == "未知用户") and (not group_id or group_id == "未知群" or group_id == "c2c")
                or ((not user_id or user_id == "未知用户") and (not group_id or group_id == "未知群" or group_id == "c2c") and (not pure_content))
            ) and not is_group_add:
                try:
                    pure_content = json.dumps(message, ensure_ascii=False)
                except Exception:
                    pure_content = str(message)
            formatted_message = f"{user_id}（{group_id}）：{pure_content}" if group_id != "c2c" else f"{user_id}：{pure_content}"
        except Exception as e:
            formatted_message = str(message)
            user_id = "未知用户"
            group_id = "c2c"
            pure_content = formatted_message
            add_error_log(f"消息解析失败: {str(e)}", traceback.format_exc())
    display_entry = received_handler.add(formatted_message, skip_db=True)
    db_entry = {
        'timestamp': display_entry['timestamp'],
        'content': pure_content,
        'user_id': user_id,
        'group_id': group_id
    }
    add_log_to_db('received', db_entry)
    return display_entry

@catch_error
def add_plugin_log(log):
    """添加插件日志"""
    return plugin_handler.add(log)

@catch_error
def add_framework_log(log):
    """添加框架日志"""
    return framework_handler.add(log)

@catch_error
def add_error_log(log, traceback_info=None):
    """添加错误日志"""
    return error_handler.add(log, traceback_info)

# ===== 9. API路由 =====
@web_panel.route('/')
@catch_error
def index():
    """Web面板首页"""
    return render_template('index.html', prefix=PREFIX)

@web_panel.route('/api/logs/<log_type>')
@catch_error
def get_logs(log_type):
    """API端点，用于分页获取日志"""
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('size', 50, type=int)
    if log_type == 'received':
        logs = list(received_messages)
    elif log_type == 'plugin':
        logs = list(plugin_logs)
    elif log_type == 'framework':
        logs = list(framework_logs)
    else:
        return jsonify({'error': '无效的日志类型'}), 400
    logs.reverse()
    start = (page - 1) * page_size
    end = start + page_size
    page_logs = logs[start:end]
    return jsonify({
        'logs': page_logs,
        'total': len(logs),
        'page': page,
        'page_size': page_size,
        'total_pages': (len(logs) + page_size - 1) // page_size
    })

@web_panel.route('/status')
@catch_error
def status():
    """状态检查接口"""
    return jsonify({
        'status': 'ok',
        'version': '1.0',
        'logs_count': {
            'received': len(received_messages),
            'plugin': len(plugin_logs),
            'framework': len(framework_logs)
        }
    })

# ===== 10. 系统信息与插件管理 =====
@catch_error
def get_system_info():
    """
    获取系统信息，包含基本的内存和CPU使用情况以及详细的内存分配。
    支持自动垃圾回收和内存分配估算。
    """
    global _last_gc_time, _last_gc_log_time
    process = psutil.Process(os.getpid())
    current_time = time.time()
    collected = 0
    if current_time - _last_gc_time >= _gc_interval:
        collected = gc.collect()
        _last_gc_time = current_time
        if current_time - _last_gc_log_time >= _gc_log_interval:
            add_framework_log(f"系统执行垃圾回收，回收了 {collected} 个对象")
            _last_gc_log_time = current_time
    memory_info = process.memory_info()
    rss = memory_info.rss / 1024 / 1024  # MB
    cpu_percent = process.cpu_percent(interval=0.05)
    objects = gc.get_objects()
    framework_mb = 0
    plugins_mb = 0
    web_panel_mb = 0
    other_mb = 0
    modules = sys.modules.copy()
    for module_name, module in modules.items():
        module_size = 0
        try:
            if module and hasattr(module, '__dict__'):
                for name, obj in module.__dict__.items():
                    try:
                        obj_size = sys.getsizeof(obj) / (1024 * 1024)
                        module_size += obj_size
                    except:
                        pass
            if module_name.startswith('plugins'):
                plugins_mb += module_size
            elif module_name == 'web_panel' or module_name.startswith('web_panel.'):
                web_panel_mb += module_size
            elif module_name in ('core', 'function') or module_name.startswith(('core.', 'function.')):
                framework_mb += module_size
            else:
                other_mb += module_size
        except:
            pass
    total_estimated = plugins_mb + framework_mb + web_panel_mb + other_mb
    if total_estimated > 0:
        ratio = rss / total_estimated
        plugins_mb *= ratio
        framework_mb *= ratio
        web_panel_mb *= ratio
        other_mb *= ratio
    else:
        avg = rss / 4
        plugins_mb = avg
        framework_mb = avg
        web_panel_mb = avg
        other_mb = avg
    uptime_seconds = (datetime.now() - START_TIME).total_seconds()
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    formatted_uptime = {
        'days': int(days),
        'hours': int(hours),
        'minutes': int(minutes),
        'seconds': int(seconds),
        'total_seconds': uptime_seconds
    }
    return {
        'cpu_percent': cpu_percent,
        'memory_percent': process.memory_percent(),
        'memory_info': {
            'total_mb': rss,
            'plugins_mb': plugins_mb,
            'framework_mb': framework_mb,
            'web_panel_mb': web_panel_mb,
            'other_mb': other_mb,
            'gc_counts': gc.get_count(),
            'total_objects': len(objects)
        },
        'memory_collection': {
            'collected_count': gc.get_count(),
            'objects_count': len(objects)
        },
        'uptime': formatted_uptime
    }

@catch_error
def process_plugin_module(module, plugin_path, module_name, is_hot_reload=False, is_system=False, is_cold_load=False):
    """处理单个插件模块，提取插件信息"""
    plugin_info_list = []
    plugin_classes_found = False
    
    # 获取文件修改时间（仅热更新插件需要）
    last_modified_str = ""
    if is_hot_reload:
        last_modified = os.path.getmtime(plugin_path)
        last_modified_str = datetime.fromtimestamp(last_modified).strftime('%Y-%m-%d %H:%M:%S')
        
    # 扫描模块中的所有类
    for attr_name in dir(module):
        # 跳过特殊属性、内置函数和模块
        if attr_name.startswith('__') or not hasattr(getattr(module, attr_name), '__class__'):
            continue
            
        attr = getattr(module, attr_name)
        # 检查是否是类，并且不是从其他模块导入的类
        if isinstance(attr, type) and attr.__module__ == module.__name__:
            # 检查是否有get_regex_handlers方法
            if hasattr(attr, 'get_regex_handlers'):
                plugin_classes_found = True
                
                # 确定插件名称
                if is_system:
                    name = f"system/{module_name}/{attr_name}"
                elif is_hot_reload:
                    name = f"example/{module_name}/{attr_name}"
                else:
                    name = f"{module_name}/{attr_name}"
                
                plugin_info = {
                    'name': name,
                    'class_name': attr_name,
                    'status': 'loaded',
                    'error': '',
                    'path': plugin_path,
                    'is_hot_reload': is_hot_reload,
                    'is_cold_load': is_cold_load,
                    'is_system': is_system
                }
                
                # 获取处理器
                try:
                    handlers = attr.get_regex_handlers()
                    plugin_info['handlers'] = len(handlers) if handlers else 0
                    plugin_info['handlers_list'] = list(handlers.keys()) if handlers else []
                    
                    # 获取插件优先级
                    plugin_info['priority'] = getattr(attr, 'priority', 10)
                    
                    # 获取处理器的owner_only属性
                    handlers_owner_only = {}
                    handlers_group_only = {}
                    for pattern, handler_info in handlers.items():
                        if isinstance(handler_info, dict):
                            handlers_owner_only[pattern] = handler_info.get('owner_only', False)
                            handlers_group_only[pattern] = handler_info.get('group_only', False)
                        else:
                            handlers_owner_only[pattern] = False
                            handlers_group_only[pattern] = False
                            
                    plugin_info['handlers_owner_only'] = handlers_owner_only
                    plugin_info['handlers_group_only'] = handlers_group_only
                    
                    # 添加修改时间（如果有）
                    if last_modified_str:
                        plugin_info['last_modified'] = last_modified_str
                        
                except Exception as e:
                    plugin_info['status'] = 'error'
                    plugin_info['error'] = f"获取处理器失败: {str(e)}"
                    plugin_info['traceback'] = traceback.format_exc()
                
                # 添加到结果列表
                plugin_info_list.append(plugin_info)
    
    # 如果没有找到插件类，添加一个错误记录
    if not plugin_classes_found:
        name_prefix = "system/" if is_system else "example/" if is_hot_reload else ""
        plugin_info = {
            'name': f"{name_prefix}{module_name}",
            'class_name': 'unknown',
            'status': 'error',
            'error': '未在模块中找到有效的插件类',
            'path': plugin_path,
            'is_hot_reload': is_hot_reload,
            'is_cold_load': is_cold_load
        }
        # 添加修改时间（如果有）
        if last_modified_str:
            plugin_info['last_modified'] = last_modified_str
            
        plugin_info_list.append(plugin_info)
    
    return plugin_info_list

@catch_error
def load_plugin_module(plugin_path, module_name, is_hot_reload=False, is_system=False, is_cold_load=False):
    """加载插件模块并进行错误处理"""
    try:
        # 确定完整模块名称
        if is_system:
            full_module_name = f"plugins.system.{module_name}"
        elif is_hot_reload:
            full_module_name = f"plugins.example.{module_name}"
        else:
            full_module_name = f"plugins.{module_name}.main"
            
        # 动态导入模块
        spec = importlib.util.spec_from_file_location(full_module_name, plugin_path)
        if not spec or not spec.loader:
            return [{
                'name': module_name,
                'class_name': 'unknown',
                'status': 'error',
                'error': '无法加载插件文件',
                'path': plugin_path,
                'is_hot_reload': is_hot_reload,
                'is_cold_load': is_cold_load
            }]
            
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # 处理模块中的插件类
        return process_plugin_module(module, plugin_path, module_name, is_hot_reload, is_system, is_cold_load)
        
    except Exception as e:
        name_prefix = "system/" if is_system else "example/" if is_hot_reload else ""
        return [{
            'name': f"{name_prefix}{module_name}",
            'class_name': 'unknown',
            'status': 'error',
            'error': str(e),
            'path': plugin_path,
            'is_hot_reload': is_hot_reload,
            'is_cold_load': is_cold_load,
            'traceback': traceback.format_exc()
        }]

@catch_error
def scan_plugins():
    """扫描所有插件并获取其状态"""
    global plugins_info
    plugins_info = []
    
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # 1. 处理system目录下的所有插件
    system_dir = os.path.join(script_dir, 'plugins', 'system')
    if os.path.exists(system_dir) and os.path.isdir(system_dir):
        py_files = [f for f in os.listdir(system_dir) if f.endswith('.py') and f != '__init__.py']
        
        for py_file in py_files:
            plugin_file = os.path.join(system_dir, py_file)
            plugin_name = os.path.splitext(py_file)[0]
            
            # 加载系统插件
            plugin_info_list = load_plugin_module(plugin_file, plugin_name, is_system=True, is_cold_load=True)
            plugins_info.extend(plugin_info_list)
    
    # 2. 处理非example和system目录下的插件 (仅main.py)
    for item in os.listdir(os.path.join(script_dir, 'plugins')):
        if item not in ['example', 'system'] and os.path.isdir(os.path.join(script_dir, 'plugins', item)):
            main_py = os.path.join(script_dir, 'plugins', item, 'main.py')
            if os.path.exists(main_py):
                # 加载标准插件
                plugin_info_list = load_plugin_module(main_py, item, is_cold_load=True)
                plugins_info.extend(plugin_info_list)
    
    # 3. 处理example目录下的所有py文件
    example_dir = os.path.join(script_dir, 'plugins', 'example')
    if os.path.exists(example_dir) and os.path.isdir(example_dir):
        py_files = [f for f in os.listdir(example_dir) if f.endswith('.py') and f != '__init__.py']
        
        for py_file in py_files:
            # 获取模块名（不含.py后缀）
            module_name = os.path.splitext(py_file)[0]
            plugin_file = os.path.join(example_dir, py_file)
            
            # 加载热更新插件
            plugin_info_list = load_plugin_module(plugin_file, module_name, is_hot_reload=True)
            plugins_info.extend(plugin_info_list)
    
    # 按状态排序：已加载的排在前面，然后按热更新排序
    plugins_info.sort(key=lambda x: (0 if x['status'] == 'loaded' else 1, 0 if x.get('is_hot_reload', False) else 1))
    return plugins_info

@catch_error
def register_socketio_handlers(sio):
    """注册Socket.IO事件处理函数"""
    @sio.on('connect', namespace=PREFIX)
    def handle_connect():
        # 降低日志输出详细程度
        print(f"Web面板：新客户端连接")
        
        # 扫描插件状态
        scan_plugins()
        
        # 构建初始数据包
        initial_data = {
            'logs': {
                'received_messages': list(received_handler.logs)[-50:],
                'plugin_logs': list(plugin_handler.logs)[-50:],
                'framework_logs': list(framework_handler.logs)[-50:],
                'error_logs': list(error_handler.logs)[-50:]
            },
            'system_info': get_system_info(),
            'plugins_info': plugins_info,
            'prefix': PREFIX
        }
        
        # 所有类型的日志都逆序排列，最新的在最上面
        for log_type in initial_data['logs']:
            initial_data['logs'][log_type].reverse()
        
        # 发送初始数据
        sio.emit('initial_data', initial_data, room=request.sid, namespace=PREFIX)

    @sio.on('disconnect', namespace=PREFIX)
    def handle_disconnect():
        # 减少输出
        print("Web面板：客户端断开连接")

    @sio.on('get_system_info', namespace=PREFIX)
    def handle_get_system_info():
        """处理客户端请求系统信息的事件"""
        global _last_gc_time
        current_time = time.time()
        
        # 获取系统信息
        system_info = get_system_info()
        
        # 发送系统信息更新
        sio.emit('system_info_update', system_info, 
                room=request.sid, namespace=PREFIX)

    @sio.on('refresh_plugins', namespace=PREFIX)
    def handle_refresh_plugins():
        """处理刷新插件信息的请求"""
        plugins = scan_plugins()
        sio.emit('plugins_update', plugins, 
                room=request.sid, namespace=PREFIX)

    @sio.on('request_logs', namespace=PREFIX)
    def handle_request_logs(data):
        """处理客户端请求日志的事件"""
        log_type = data.get('type', 'received')
        page = data.get('page', 1)
        page_size = data.get('page_size', 50)
        
        # 获取对应类型的日志
        logs_map = {
            'received': received_handler.logs,
            'plugin': plugin_handler.logs,
            'framework': framework_handler.logs,
            'error': error_handler.logs
        }
        
        logs = list(logs_map.get(log_type, []))
        
        # 所有类型的日志都进行逆序排列，确保最新的在最上面
        logs.reverse()
        
        # 计算分页
        start = (page - 1) * page_size
        end = start + page_size
        page_logs = logs[start:end] if start < len(logs) else []
        
        # 发送日志更新
        sio.emit('logs_update', {
            'type': log_type,
            'logs': page_logs,
            'total': len(logs),
            'page': page,
            'page_size': page_size
        }, room=request.sid, namespace=PREFIX)

# ===== 11. Web面板启动函数 =====
def start_web_panel(main_app=None):
    """
    集成web面板到主应用中，支持独立运行或集成到已有Flask应用。
    自动初始化SocketIO、CORS、日志等。
    :param main_app: 主Flask应用实例
    :return: (app, socketio) 或 None
    """
    global socketio
    print(f"初始化Web面板，URL前缀: {PREFIX}")
    print("Web面板已配置为使用数据库记录日志")
    if main_app is None:
        app = Flask(__name__)
        app.register_blueprint(web_panel, url_prefix=PREFIX)
        CORS(app, resources={r"/*": {"origins": "*"}})
        try:
            print(f"正在初始化独立Socket.IO，路径为: /socket.io")
            socketio = SocketIO(app, 
                            cors_allowed_origins="*",
                            path="/socket.io",
                            logger=True,
                            engineio_logger=True)
            # 关键：注册Socket.IO处理函数
            register_socketio_handlers(socketio)
            print("Socket.IO处理函数注册成功")
        except Exception as e:
            print(f"Socket.IO初始化错误: {str(e)}")
            error_tb = traceback.format_exc()
            print(error_tb)
            add_log_to_db('error', {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'content': f"Socket.IO初始化错误: {str(e)}",
                'traceback': error_tb
            })
        print("创建独立的Web面板应用")
        return app, socketio
    else:
        main_app.register_blueprint(web_panel, url_prefix=PREFIX)
        try:
            CORS(main_app, resources={r"/*": {"origins": "*"}})
        except Exception as e:
            print(f"CORS设置错误: {str(e)}")
        try:
            if hasattr(main_app, 'socketio'):
                socketio = main_app.socketio
                print("使用主应用已有的Socket.IO实例")
            else:
                print(f"正在初始化Socket.IO，路径为: /socket.io")
                socketio = SocketIO(main_app, 
                                cors_allowed_origins="*",
                                path="/socket.io",
                                logger=True,
                                engineio_logger=True)
                main_app.socketio = socketio
                print("Socket.IO实例创建成功")
            # 关键：注册Socket.IO处理函数
            register_socketio_handlers(socketio)
            print("Socket.IO处理函数注册成功")
        except Exception as e:
            print(f"Socket.IO初始化错误: {str(e)}")
            error_tb = traceback.format_exc()
            print(error_tb)
            add_log_to_db('error', {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'content': f"Socket.IO初始化错误: {str(e)}",
                'traceback': error_tb
            })
        print(f"Web面板已集成到主应用，URL前缀: {PREFIX}")
        return None