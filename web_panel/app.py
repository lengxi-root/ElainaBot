# 重新导入Flask组件
from flask import Flask
from flask import render_template
from flask import request
from flask import jsonify
from flask import Blueprint
from flask_socketio import SocketIO
import psutil
import json
import os
import glob
import importlib.util
import sys
import traceback
from datetime import datetime
from collections import deque
import re
import threading
import time
from flask_cors import CORS
import functools
import gc  # 导入垃圾回收模块
import warnings
import urllib3

# 禁用urllib3不安全请求的警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 路径前缀
PREFIX = '/web'

# 创建Blueprint而不是直接创建Flask应用
web_panel = Blueprint('web_panel', __name__, 
                     static_url_path=f'{PREFIX}/static',
                     static_folder='static',  
                     template_folder='templates')

# Socket.IO实例 - 会在start_web_panel中设置
socketio = None

# 使用deque限制日志条数，提高性能
MAX_LOGS = 1000  # 最大保存日志数量
received_messages = deque(maxlen=MAX_LOGS)
plugin_logs = deque(maxlen=MAX_LOGS)
framework_logs = deque(maxlen=MAX_LOGS)
error_logs = deque(maxlen=MAX_LOGS)  # 新增：错误日志

# 存储插件信息
plugins_info = []

# 日志文件保存目录
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs')
# 确保日志目录存在
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

# 捕获错误的装饰器，放在使用它的函数之前定义
def catch_error(func):
    """捕获错误的装饰器，记录到错误日志"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # 获取错误信息和调用栈
            error_msg = f"{func.__name__} 执行出错: {str(e)}"
            tb_info = traceback.format_exc()
            
            # 记录错误
            print(error_msg)
            print(tb_info)
            
            # 如果socketio初始化了，使用add_error_log
            if 'add_error_log' in globals() and 'socketio' in globals() and socketio is not None:
                add_error_log(error_msg, tb_info)
            else:
                # 否则直接写入日志文件
                try:
                    today = datetime.now().strftime('%Y-%m-%d')
                    date_dir = os.path.join(LOG_DIR, today)
                    if not os.path.exists(date_dir):
                        os.makedirs(date_dir)
                    error_file = os.path.join(date_dir, f"error.log")
                    with open(error_file, 'a', encoding='utf-8') as f:
                        f.write(f"\n--- 函数执行错误 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                        f.write(error_msg + "\n")
                        f.write(tb_info + "\n")
                except:
                    pass
            
            # 可以返回一个默认值或重新抛出异常
            return None
    return wrapper

# 消息内容解析
def parse_message_content(message_data):
    """解析消息内容，格式化为更友好的显示"""
    try:
        # 尝试解析JSON字符串
        if isinstance(message_data, str):
            try:
                data = json.loads(message_data)
            except json.JSONDecodeError:
                return message_data
        else:
            data = message_data
            
        # 检查是否是JSON格式的消息
        if isinstance(data, dict):
            # 群聊消息格式 (GROUP_AT_MESSAGE_CREATE)
            if "op" in data and "d" in data and data.get("t") == "GROUP_AT_MESSAGE_CREATE":
                d = data.get("d", {})
                member_openid = d.get("author", {}).get("member_openid", "未知用户")
                group_openid = d.get("group_openid", "")
                content = d.get("content", "")
                
                if group_openid:
                    return f"{member_openid}（{group_openid}）：{content}"
                else:
                    return f"{member_openid}：{content}"
            
            # 按钮回调消息格式 (INTERACTION_CREATE)
            elif "op" in data and "d" in data and data.get("t") == "INTERACTION_CREATE":
                d = data.get("d", {})
                
                # 根据用户提供的示例精确提取字段
                # 示例: {"op": 0, "id": "INTERACTION_CREATE:fb0d70b6-d6d3-4208-9e18-92278b4ca7d9", "d": {"id": "8035e62e-31e3-4a10-a74e-2489842acb0f", "application_id": "102134274", "type": 11, "data": {"type": 11, "resolved": {"button_data": "/胡桃签到", "button_id": "1"}}, "version": 1, "group_openid": "8F1223458B2A13A13678F0D05BB125F8", "chat_type": 1, "scene": "group", "timestamp": "2025-05-17T02:16:55+08:00", "group_member_openid": "8C7A05AC58E3BCAAA3E83B22486FAF8F"}, "t": "INTERACTION_CREATE"}
                group_member_openid = d.get("group_member_openid", "未知用户")
                group_openid = d.get("group_openid", "")
                
                # 从data.resolved中获取按钮数据
                button_data = ""
                data_obj = d.get("data", {})
                if isinstance(data_obj, dict) and "resolved" in data_obj:
                    resolved = data_obj.get("resolved", {})
                    button_data = resolved.get("button_data", "")
                    button_id = resolved.get("button_id", "")
                    
                    # 增加button_id信息便于调试
                    if button_id:
                        button_data = f"{button_data} [ID:{button_id}]"
                
                if group_openid:
                    return f"{group_member_openid}（{group_openid}）按钮交互：{button_data} (回调消息)"
                else:
                    return f"{group_member_openid}按钮交互：{button_data} (回调消息)"
            
            # 私聊消息格式 (C2C_MESSAGE_CREATE)
            elif "op" in data and "d" in data and data.get("t") == "C2C_MESSAGE_CREATE":
                d = data.get("d", {})
                user_openid = d.get("author", {}).get("user_openid", "未知用户")
                content = d.get("content", "")
                return f"{user_openid}：{content}"
                    
            # 尝试提取通用字段
            if "content" in data:
                # 检查作者信息位置
                author = data.get("author", {})
                content = data.get("content", "")
                
                # 尝试获取各种可能的ID
                user_id = (
                    author.get("user_openid") or 
                    author.get("member_openid") or 
                    author.get("id") or 
                    "未知用户"
                )
                
                # 检查是否有群组ID
                group_id = data.get("group_openid", "") or data.get("group_id", "")
                
                if group_id:
                    return f"{user_id}（{group_id}）：{content}（回调消息）"
                else:
                    return f"{user_id}：{content}（回调消息）"
        
        # 对于不符合预期结构的数据，返回原始字符串
        return str(message_data)
    except Exception as e:
        return f"消息解析错误: {str(e)}"

# 日志函数定义
def add_received_message(message):
    """添加接收到的消息日志"""
    global socketio
    if socketio is None:
        return
        
    # 如果message已经是格式化后的字符串，直接使用，否则进行格式化
    if isinstance(message, str) and not message.startswith('{'):
        formatted_message = message
    else:
        formatted_message = parse_message_content(message)
        
    entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'content': formatted_message,
        # 不再保存原始消息到日志中
        # 'raw': message  # 保存原始消息，以便需要时使用
    }
    received_messages.append(entry)  # 使用deque尾部添加，最新的消息在队列尾部
    socketio.emit('new_message', {
        'type': 'received',
        'data': entry
    }, namespace=PREFIX)

def add_plugin_log(log):
    global socketio
    if socketio is None:
        return
        
    entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'content': log
    }
    plugin_logs.append(entry)  # 使用deque尾部添加，最新的消息在队列尾部
    socketio.emit('new_message', {
        'type': 'plugin',
        'data': entry
    }, namespace=PREFIX)

def add_framework_log(log):
    global socketio
    if socketio is None:
        return
        
    entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'content': log
    }
    framework_logs.append(entry)  # 使用deque尾部添加，最新的消息在队列尾部
    socketio.emit('new_message', {
        'type': 'framework',
        'data': entry
    }, namespace=PREFIX)

def add_error_log(log, traceback_info=None):
    """添加错误日志"""
    global socketio
    if socketio is None:
        return
        
    entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'content': log,
        'traceback': traceback_info
    }
    error_logs.append(entry)  # 使用deque尾部添加，最新的消息在队列尾部
    socketio.emit('new_message', {
        'type': 'error',
        'data': entry
    }, namespace=PREFIX)

@web_panel.route('/')
def index():
    return render_template('index.html', prefix=PREFIX)

@web_panel.route('/api/logs/<log_type>')
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
    
    # 所有类型的日志都逆序排列，最新的在前面
    logs.reverse()
    
    # 计算分页
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

def get_system_info():
    """获取系统信息，包括细化的内存使用情况"""
    process = psutil.Process(os.getpid())
    
    # 触发内存回收但不记录日志（框架内记录日志）
    collected = gc.collect()
    
    # 获取主要内存信息
    memory_info = process.memory_info()
    rss = memory_info.rss / 1024 / 1024  # MB
    
    # 获取插件内存占用（估计值）
    plugins_memory = 0
    framework_memory = 0
    web_panel_memory = 0
    other_memory = 0
    
    # 分析对象占用的内存
    objects = gc.get_objects()
    total_tracked_size = 0
    
    # 计算Python内部对象的内存占用
    for obj in objects:
        try:
            obj_size = sys.getsizeof(obj)
            if hasattr(obj, "__module__"):
                module_name = obj.__module__ or ""
                if module_name.startswith('plugins.'):
                    plugins_memory += obj_size
                    total_tracked_size += obj_size
                elif module_name.startswith('core.'):
                    framework_memory += obj_size
                    total_tracked_size += obj_size
                elif module_name.startswith('web_panel.') or module_name.startswith('flask.') or module_name.startswith('socketio.'):
                    web_panel_memory += obj_size
                    total_tracked_size += obj_size
                else:
                    other_memory += obj_size
                    total_tracked_size += obj_size
        except:
            pass
    
    # 深度内存分析 - 模块分类
    for module_name, module in sys.modules.items():
        if module:
            try:
                module_size = sys.getsizeof(module)
                total_tracked_size += module_size
                
                if module_name.startswith('plugins.'):
                    plugins_memory += module_size
                elif module_name.startswith('core.'):
                    framework_memory += module_size
                elif module_name.startswith('web_panel.') or module_name.startswith('flask') or module_name.startswith('socketio'):
                    web_panel_memory += module_size
                else:
                    other_memory += module_size
            except:
                pass
    
    # 确保内存总和等于真实内存使用量
    tracked_memory_mb = (total_tracked_size / 1024 / 1024)
    
    # 将实际内存与追踪内存的差值按比例分配
    remaining_memory = max(0, rss - tracked_memory_mb)
    
    # 分配剩余内存到各组件，按比例分配
    if total_tracked_size > 0:
        plugins_ratio = plugins_memory / total_tracked_size
        framework_ratio = framework_memory / total_tracked_size
        web_panel_ratio = web_panel_memory / total_tracked_size
        other_ratio = other_memory / total_tracked_size
        
        # 转为MB并添加剩余内存
        plugins_memory = (plugins_memory / 1024 / 1024) + (remaining_memory * plugins_ratio)
        framework_memory = (framework_memory / 1024 / 1024) + (remaining_memory * framework_ratio)
        web_panel_memory = (web_panel_memory / 1024 / 1024) + (remaining_memory * web_panel_ratio)
        other_memory = (other_memory / 1024 / 1024) + (remaining_memory * other_ratio)
    else:
        # 如果没有可以追踪的对象，将所有内存归入"其他"类别
        other_memory = rss
        plugins_memory = 0
        framework_memory = 0
        web_panel_memory = 0
    
    # 获取最大的10个对象的大小和类型，用于调试内存问题
    largest_objects = []
    for obj in objects:
        try:
            obj_size = sys.getsizeof(obj)
            if obj_size > 1024 * 1024:  # 大于1MB的对象
                largest_objects.append({
                    'type': type(obj).__name__,
                    'size_mb': obj_size / 1024 / 1024
                })
        except:
            pass  # 忽略无法获取大小的对象
    
    # 排序并保留最大的10个
    largest_objects.sort(key=lambda x: x['size_mb'], reverse=True)
    largest_objects = largest_objects[:10]
    
    # 返回系统信息
    return {
        'cpu_percent': process.cpu_percent(interval=0.1),
        'memory_percent': process.memory_percent(),
        'memory_info': {
            'total_mb': rss,
            'plugins_mb': plugins_memory,
            'framework_mb': framework_memory,
            'web_panel_mb': web_panel_memory,
            'other_mb': other_memory,
            'largest_objects': largest_objects
        },
        'memory_collection': {
            'collected_count': gc.get_count(),
            'objects_count': len(objects)
        }
    }

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
            
            try:
                # 尝试导入模块检查状态
                spec = importlib.util.spec_from_file_location(f"plugins.system.{plugin_name}", plugin_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    
                    try:
                        spec.loader.exec_module(module)
                        
                        # 搜索模块中所有可能的插件类
                        plugin_classes_found = False
                        
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
                                    plugin_info = {
                                        'name': f"system/{plugin_name}/{attr_name}",
                                        'class_name': attr_name,
                                        'status': 'loaded',
                                        'error': '',
                                        'path': plugin_file,
                                        'is_hot_reload': False,
                                        'is_cold_load': True
                                    }
                                    
                                    # 获取处理器
                                    handlers = attr.get_regex_handlers()
                                    plugin_info['handlers'] = len(handlers) if handlers else 0
                                    plugin_info['handlers_list'] = list(handlers.keys()) if handlers else []
                                    # 获取插件优先级
                                    plugin_info['priority'] = getattr(attr, 'priority', 10)
                                    # 获取处理器的owner_only属性
                                    handlers_owner_only = {}
                                    for pattern, handler_info in handlers.items():
                                        if isinstance(handler_info, dict):
                                            handlers_owner_only[pattern] = handler_info.get('owner_only', False)
                                        else:
                                            handlers_owner_only[pattern] = False
                                    plugin_info['handlers_owner_only'] = handlers_owner_only
                                    
                                    plugins_info.append(plugin_info)
                        
                        if not plugin_classes_found:
                            plugin_info = {
                                'name': f"system/{plugin_name}",
                                'class_name': 'unknown',
                                'status': 'error',
                                'error': '未在模块中找到有效的插件类',
                                'path': plugin_file,
                                'is_hot_reload': False,
                                'is_cold_load': True
                            }
                            plugins_info.append(plugin_info)
                    except Exception as e:
                        plugin_info = {
                            'name': f"system/{plugin_name}",
                            'class_name': 'unknown',
                            'status': 'error',
                            'error': str(e),
                            'path': plugin_file,
                            'is_hot_reload': False,
                            'is_cold_load': True,
                            'traceback': traceback.format_exc()
                        }
                        plugins_info.append(plugin_info)
                else:
                    plugin_info = {
                        'name': f"system/{plugin_name}",
                        'class_name': 'unknown',
                        'status': 'error',
                        'error': '无法加载插件文件',
                        'path': plugin_file,
                        'is_hot_reload': False,
                        'is_cold_load': True
                    }
                    plugins_info.append(plugin_info)
            except Exception as e:
                plugin_info = {
                    'name': f"system/{plugin_name}",
                    'class_name': 'unknown',
                    'status': 'error',
                    'error': str(e),
                    'path': plugin_file,
                    'is_hot_reload': False,
                    'is_cold_load': True
                }
                plugins_info.append(plugin_info)
    
    # 2. 处理非example和system目录下的插件 (仅main.py)
    for item in os.listdir(os.path.join(script_dir, 'plugins')):
        if item not in ['example', 'system'] and os.path.isdir(os.path.join(script_dir, 'plugins', item)):
            main_py = os.path.join(script_dir, 'plugins', item, 'main.py')
            if os.path.exists(main_py):
                # 获取插件名称
                plugin_name = item
                class_name = f"{plugin_name}_plugin"
                plugin_info = {
                    'name': plugin_name,
                    'class_name': class_name,
                    'status': 'unknown',
                    'error': '',
                    'path': main_py,
                    'is_hot_reload': False,
                    'is_cold_load': True
                }
                
                try:
                    # 尝试导入模块检查状态
                    spec = importlib.util.spec_from_file_location(f"plugins.{plugin_name}.main", main_py)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        
                        try:
                            spec.loader.exec_module(module)
                            
                            # 检查插件类是否存在
                            if hasattr(module, class_name):
                                plugin_class = getattr(module, class_name)
                                # 检查是否实现了必要方法
                                if hasattr(plugin_class, 'get_regex_handlers'):
                                    plugin_info['status'] = 'loaded'
                                    # 获取处理器数量
                                    handlers = plugin_class.get_regex_handlers()
                                    plugin_info['handlers'] = len(handlers) if handlers else 0
                                    # 获取处理器列表
                                    plugin_info['handlers_list'] = list(handlers.keys()) if handlers else []
                                    # 获取插件优先级
                                    plugin_info['priority'] = getattr(plugin_class, 'priority', 10)
                                    # 获取处理器的owner_only属性
                                    handlers_owner_only = {}
                                    for pattern, handler_info in handlers.items():
                                        if isinstance(handler_info, dict):
                                            handlers_owner_only[pattern] = handler_info.get('owner_only', False)
                                        else:
                                            handlers_owner_only[pattern] = False
                                    plugin_info['handlers_owner_only'] = handlers_owner_only
                                else:
                                    plugin_info['status'] = 'error'
                                    plugin_info['error'] = '插件未实现必要的get_regex_handlers方法'
                            else:
                                plugin_info['status'] = 'error'
                                plugin_info['error'] = f'未找到插件类 {class_name}'
                        except Exception as e:
                            plugin_info['status'] = 'error'
                            plugin_info['error'] = str(e)
                            plugin_info['traceback'] = traceback.format_exc()
                    else:
                        plugin_info['status'] = 'error'
                        plugin_info['error'] = '无法加载插件文件'
                except Exception as e:
                    plugin_info['status'] = 'error'
                    plugin_info['error'] = str(e)
                
                plugins_info.append(plugin_info)
    
    # 3. 处理example目录下的所有py文件
    example_dir = os.path.join(script_dir, 'plugins', 'example')
    if os.path.exists(example_dir) and os.path.isdir(example_dir):
        py_files = [f for f in os.listdir(example_dir) if f.endswith('.py') and f != '__init__.py']
        
        for py_file in py_files:
            # 获取模块名（不含.py后缀）
            module_name = os.path.splitext(py_file)[0]
            plugin_file = os.path.join(example_dir, py_file)
            
            # 获取文件修改时间
            last_modified = os.path.getmtime(plugin_file)
            last_modified_str = datetime.fromtimestamp(last_modified).strftime('%Y-%m-%d %H:%M:%S')
            
            try:
                # 动态导入模块
                spec = importlib.util.spec_from_file_location(f"plugins.example.{module_name}", plugin_file)
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    
                    # 搜索模块中所有可能的插件类
                    plugin_classes_found = False
                    
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
                                plugin_info = {
                                    'name': f"example/{module_name}/{attr_name}",
                                    'class_name': attr_name,
                                    'status': 'loaded',
                                    'error': '',
                                    'path': plugin_file,
                                    'is_hot_reload': True,
                                    'last_modified': last_modified_str
                                }
                                
                                # 获取处理器
                                handlers = attr.get_regex_handlers()
                                plugin_info['handlers'] = len(handlers) if handlers else 0
                                plugin_info['handlers_list'] = list(handlers.keys()) if handlers else []
                                # 获取插件优先级
                                plugin_info['priority'] = getattr(attr, 'priority', 10)
                                # 获取处理器的owner_only属性
                                handlers_owner_only = {}
                                for pattern, handler_info in handlers.items():
                                    if isinstance(handler_info, dict):
                                        handlers_owner_only[pattern] = handler_info.get('owner_only', False)
                                    else:
                                        handlers_owner_only[pattern] = False
                                plugin_info['handlers_owner_only'] = handlers_owner_only
                                
                                plugins_info.append(plugin_info)
                    
                    if not plugin_classes_found:
                        plugin_info = {
                            'name': f"example/{module_name}",
                            'class_name': 'unknown',
                            'status': 'error',
                            'error': '未在模块中找到有效的插件类',
                            'path': plugin_file,
                            'is_hot_reload': True,
                            'last_modified': last_modified_str
                        }
                        plugins_info.append(plugin_info)
            except Exception as e:
                plugin_info = {
                    'name': f"example/{module_name}",
                    'class_name': 'unknown',
                    'status': 'error',
                    'error': str(e),
                    'path': plugin_file,
                    'is_hot_reload': True,
                    'last_modified': last_modified_str
                }
                if 'traceback' in locals():
                    plugin_info['traceback'] = traceback.format_exc()
                plugins_info.append(plugin_info)
    
    # 按状态排序：已加载的排在前面，然后按热更新排序
    plugins_info.sort(key=lambda x: (0 if x['status'] == 'loaded' else 1, 0 if x.get('is_hot_reload', False) else 1))
    return plugins_info

@catch_error
def save_logs_to_file():
    """将内存中的日志保存到文件并清空内存日志"""
    global received_messages, plugin_logs, framework_logs, error_logs
    
    # 当前日期
    today = datetime.now().strftime('%Y-%m-%d')
    current_time = datetime.now().strftime('%H-%M-%S')
    
    # 创建日期文件夹
    date_dir = os.path.join(LOG_DIR, today)
    if not os.path.exists(date_dir):
        os.makedirs(date_dir)
    
    # 记录当前内存中的日志数量
    received_count = len(received_messages)
    plugin_count = len(plugin_logs)
    framework_count = len(framework_logs)
    error_count = len(error_logs)
    total_count = received_count + plugin_count + framework_count + error_count
    
    # 如果没有日志，直接返回
    if total_count == 0:
        print(f"没有新日志需要保存")
        return
        
    # 创建当前日志的本地副本
    received_copy = list(received_messages)
    plugin_copy = list(plugin_logs)
    framework_copy = list(framework_logs)
    error_copy = list(error_logs)
    
    # 1. 保存接收消息日志 - 仅保存格式化后的内容，不保存原始消息
    if received_copy:
        message_file = os.path.join(date_dir, f"messages.log")
        with open(message_file, 'a', encoding='utf-8') as f:
            f.write(f"\n--- 接收消息日志 {today} {current_time} ---\n")
            for msg in received_copy:
                f.write(f"[{msg['timestamp']}] {msg['content']}\n")
    
    # 2. 保存插件日志
    if plugin_copy:
        plugin_file = os.path.join(date_dir, f"plugin.log")
        with open(plugin_file, 'a', encoding='utf-8') as f:
            f.write(f"\n--- 插件日志 {today} {current_time} ---\n")
            for log in plugin_copy:
                f.write(f"[{log['timestamp']}] {log['content']}\n")
    
    # 3. 保存框架日志
    if framework_copy:
        framework_file = os.path.join(date_dir, f"framework.log")
        with open(framework_file, 'a', encoding='utf-8') as f:
            f.write(f"\n--- 框架日志 {today} {current_time} ---\n")
            for log in framework_copy:
                f.write(f"[{log['timestamp']}] {log['content']}\n")
    
    # 4. 保存错误日志
    if error_copy:
        error_file = os.path.join(date_dir, f"error.log")
        with open(error_file, 'a', encoding='utf-8') as f:
            f.write(f"\n--- 错误日志 {today} {current_time} ---\n")
            for log in error_copy:
                f.write(f"[{log['timestamp']}] {log['content']}\n")
                if log.get('traceback'):
                    f.write(f"调用栈信息:\n{log['traceback']}\n")
                f.write("\n")
    
    # 清空内存中的日志
    received_messages.clear()
    plugin_logs.clear()
    framework_logs.clear()
    error_logs.clear()
    
    # 直接打印而不是使用add_framework_log避免循环引用
    print(f"日志已保存到文件夹 {date_dir}，共 {total_count} 条日志")
    
    # 在清空后再添加一条新的记录
    entry = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'content': f"日志已保存到文件夹，共 {total_count} 条 (接收:{received_count}, 插件:{plugin_count}, 框架:{framework_count}, 错误:{error_count})"
    }
    framework_logs.append(entry)
    if socketio:
        socketio.emit('new_message', {
            'type': 'framework',
            'data': entry
        }, namespace=PREFIX)

@catch_error
def log_saving_thread():
    """每5分钟保存日志的线程函数"""
    while True:
        try:
            # 获取当前时间
            now = datetime.now()
            
            # 检查是否是5的倍数分钟
            if now.minute % 5 == 0 and now.second < 10:  # 只在每5分钟的前10秒执行
                print(f"定时保存日志：当前时间 {now.strftime('%H:%M:%S')}")
                save_logs_to_file()
                
                # 等待50秒，确保不会在同一个分钟内多次保存
                # 例如，5:00分保存后，休眠到5:00:50，下次检查时已经是5:01分，不会重复保存
                time.sleep(50)
            else:
                # 计算距离下一个5分钟点还有多少秒
                next_5min = 5 - (now.minute % 5)
                if next_5min == 5:
                    next_5min = 0
                seconds_to_next = next_5min * 60 - now.second
                
                # 如果时间太短就增加一个周期
                if seconds_to_next < 10:
                    seconds_to_next += 300  # 增加5分钟
                    
                # 休眠时间不超过10秒，便于及时响应
                sleep_time = min(seconds_to_next, 10)
                time.sleep(sleep_time)
        except Exception as e:
            error_msg = f"日志保存线程出错: {str(e)}"
            tb_info = traceback.format_exc()
            print(error_msg)
            print(tb_info)
            
            # 记录错误
            if 'add_error_log' in globals() and 'socketio' in globals() and socketio is not None:
                add_error_log(error_msg, tb_info)
            
            # 出错时等待10秒后继续
            time.sleep(10)

@catch_error
def register_socketio_handlers(sio):
    """注册Socket.IO事件处理函数"""
    @sio.on('connect', namespace=PREFIX)
    def handle_connect():
        # 降低日志输出详细程度
        print(f"Web面板：新客户端连接")
        
        # 扫描插件状态
        scan_plugins()
        
        # 发送初始数据 - 只发送最新的50条日志
        logs_to_send = {
            'received_messages': list(received_messages)[-50:],
            'plugin_logs': list(plugin_logs)[-50:],
            'framework_logs': list(framework_logs)[-50:],
            'error_logs': list(error_logs)[-50:]  # 添加错误日志
        }
        
        # 所有类型的日志都逆序排列，最新的在最上面
        logs_to_send['received_messages'].reverse()
        logs_to_send['plugin_logs'].reverse()
        logs_to_send['framework_logs'].reverse()
        logs_to_send['error_logs'].reverse()  # 错误日志也逆序
        
        sio.emit('initial_data', {
            'logs': logs_to_send,
            'system_info': get_system_info(),
            'plugins_info': plugins_info,
            'prefix': PREFIX
        }, room=request.sid, namespace=PREFIX)

    @sio.on('disconnect', namespace=PREFIX)
    def handle_disconnect():
        # 减少输出
        print("Web面板：客户端断开连接")

    @sio.on('get_system_info', namespace=PREFIX)
    def handle_get_system_info():
        """处理客户端请求系统信息的事件"""
        sio.emit('system_info_update', get_system_info(), 
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
        
        if log_type == 'received':
            logs = list(received_messages)
        elif log_type == 'plugin':
            logs = list(plugin_logs)
        elif log_type == 'framework':
            logs = list(framework_logs)
        elif log_type == 'error':  # 添加处理错误日志的逻辑
            logs = list(error_logs)
        else:
            return
        
        # 所有类型的日志都进行逆序排列，确保最新的在最上面
        logs.reverse()
        
        # 计算分页
        start = (page - 1) * page_size
        end = start + page_size
        page_logs = logs[start:end] if start < len(logs) else []
        
        sio.emit('logs_update', {
            'type': log_type,
            'logs': page_logs,
            'total': len(logs),
            'page': page,
            'page_size': page_size
        }, room=request.sid, namespace=PREFIX)

def start_web_panel(main_app=None):
    """
    集成web面板到主应用中
    
    Args:
        main_app: 主Flask应用实例
    
    Returns:
        如果提供main_app，则直接集成并返回None
        否则创建新的Flask应用并返回，供外部挂载
    """
    global socketio
    
    print(f"初始化Web面板，URL前缀: {PREFIX}")
    
    # 启动日志保存线程
    log_thread = threading.Thread(target=log_saving_thread, daemon=True)
    log_thread.start()
    print("日志自动保存功能已启动，每5分钟保存一次")
    
    if main_app is None:
        # 没有提供主应用，创建新的Flask应用
        app = Flask(__name__)
        app.register_blueprint(web_panel, url_prefix=PREFIX)
        
        # 设置CORS允许所有来源
        CORS(app, resources={r"/*": {"origins": "*"}})
        
        # 初始化Socket.IO
        try:
            print(f"正在初始化独立Socket.IO，路径为: /socket.io")
            socketio = SocketIO(app, 
                            cors_allowed_origins="*",
                            path="/socket.io",
                            logger=True,
                            engineio_logger=True)
            
            # 注册Socket.IO处理函数
            register_socketio_handlers(socketio)
            print("Socket.IO处理函数注册成功")
        except Exception as e:
            print(f"Socket.IO初始化错误: {str(e)}")
            import traceback
            error_tb = traceback.format_exc()
            print(error_tb)
            # 此时还不能使用add_error_log，因为socketio还没初始化成功

        print("创建独立的Web面板应用")
        return app, socketio
    else:
        # 已提供主应用，直接注册蓝图
        main_app.register_blueprint(web_panel, url_prefix=PREFIX)
        
        # 设置CORS允许所有来源
        try:
            CORS(main_app, resources={r"/*": {"origins": "*"}})
        except Exception as e:
            print(f"CORS设置错误: {str(e)}")
        
        # 检查主应用是否已有Socket.IO
        try:
            if hasattr(main_app, 'socketio'):
                socketio = main_app.socketio
                print("使用主应用已有的Socket.IO实例")
            else:
                # 初始化Socket.IO
                print(f"正在初始化Socket.IO，路径为: /socket.io")
                socketio = SocketIO(main_app, 
                                cors_allowed_origins="*",
                                path="/socket.io",
                                logger=True,
                                engineio_logger=True)
                main_app.socketio = socketio
                print("Socket.IO实例创建成功")
            
            # 注册Socket.IO处理函数
            register_socketio_handlers(socketio)
            print("Socket.IO处理函数注册成功")
        except Exception as e:
            print(f"Socket.IO初始化错误: {str(e)}")
            import traceback
            error_tb = traceback.format_exc()
            print(error_tb)
            # 记录错误到日志文件，因为socketio可能未初始化
            try:
                today = datetime.now().strftime('%Y-%m-%d')
                date_dir = os.path.join(LOG_DIR, today)
                if not os.path.exists(date_dir):
                    os.makedirs(date_dir)
                error_file = os.path.join(date_dir, f"error.log")
                with open(error_file, 'a', encoding='utf-8') as f:
                    f.write(f"\n--- Socket.IO初始化错误 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                    f.write(str(e) + "\n")
                    f.write(error_tb + "\n")
            except:
                pass
        
        print(f"Web面板已集成到主应用，URL前缀: {PREFIX}")
        return None 