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
from flask import Flask, render_template, request, jsonify, Blueprint, make_response
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
    response = make_response(render_template('index.html', prefix=PREFIX))
    
    # 添加安全头信息
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    # 添加缓存控制头
    response.headers['Cache-Control'] = 'max-age=86400, public'  # 缓存24小时
    
    return response

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
    增加硬盘使用情况信息。
    优化性能，减少不必要的计算。
    """
    global _last_gc_time, _last_gc_log_time
    
    try:
        process = psutil.Process(os.getpid())
        current_time = time.time()
        collected = 0
        
        # 有选择性地执行垃圾回收，减少不必要的性能开销
        if current_time - _last_gc_time >= _gc_interval:
            collected = gc.collect(0)  # 只收集第0代对象，减少停顿时间
            _last_gc_time = current_time
            if current_time - _last_gc_log_time >= _gc_log_interval:
                add_framework_log(f"系统执行垃圾回收，回收了 {collected} 个对象")
                _last_gc_log_time = current_time
        
        # 内存信息 - 使用缓存数据结构提高效率
        memory_info = process.memory_info()
        rss = memory_info.rss / 1024 / 1024  # MB (框架内存使用)
        
        # 系统内存信息
        system_memory = psutil.virtual_memory()
        system_memory_total = system_memory.total / (1024 * 1024)  # MB
        system_memory_used = system_memory.used / (1024 * 1024)    # MB (整个系统内存使用)
        system_memory_percent = system_memory.percent
        
        # 确保物理内存使用量不为0 - 使用系统总内存使用量，而不是进程内存
        process_memory_used = rss  # 进程/框架内存使用
        
        # CPU信息 - 获取CPU核心数和使用率
        try:
            # 获取CPU核心数
            cpu_cores = psutil.cpu_count(logical=True)
            
            # 使用非阻塞方式获取CPU使用率
            cpu_percent = process.cpu_percent(interval=0.05)
            system_cpu_percent = psutil.cpu_percent(interval=0.05)
            
            # 确保值有效
            if cpu_percent <= 0:
                cpu_percent = 1.0
            if system_cpu_percent <= 0:
                system_cpu_percent = 5.0
        except Exception as e:
            error_msg = f"获取CPU信息失败: {str(e)}"
            add_error_log(error_msg)
            cpu_cores = 1
            cpu_percent = 1.0  # 默认值
            system_cpu_percent = 5.0  # 默认值
        
        # 简化内存分配估算，减少计算负担
        framework_mb = max(10.0, rss * 0.40)  # 约40%
        plugins_mb = max(10.0, rss * 0.30)    # 约30%
        web_panel_mb = max(5.0, rss * 0.15)   # 约15%
        other_mb = max(5.0, rss * 0.15)       # 约15%
        
        # 框架运行时间 - 确保格式一致性
        app_uptime_seconds = int((datetime.now() - START_TIME).total_seconds())
        
        # 服务器运行时间
        try:
            # 获取系统启动时间
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            system_uptime = datetime.now() - boot_time
            system_uptime_seconds = int(system_uptime.total_seconds())
            boot_time_str = boot_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            # 使用应用启动时间作为后备
            system_uptime_seconds = app_uptime_seconds
            boot_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 使用特定格式确保日期格式化正确
        try:
            start_time_str = START_TIME.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            # 使用当前时间作为后备
            start_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # 获取系统版本信息
        try:
            import platform
            system_version = platform.platform()
        except Exception:
            system_version = "未知"
        
        # 硬盘使用情况 - 尝试使用缓存
        try:
            # 获取当前工作目录所在的磁盘信息
            disk_path = os.path.abspath(os.getcwd())
            disk_usage = psutil.disk_usage(disk_path)
            
            disk_info = {
                'total': float(disk_usage.total),
                'used': float(disk_usage.used),
                'free': float(disk_usage.free),
                'percent': float(disk_usage.percent)
            }
            
            # 计算框架目录占用空间
            framework_dir_size = 0
            for root, dirs, files in os.walk(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
                for file in files:
                    try:
                        file_path = os.path.join(root, file)
                        if os.path.isfile(file_path):
                            framework_dir_size += os.path.getsize(file_path)
                    except Exception:
                        pass
            
            disk_info['framework_usage'] = float(framework_dir_size)
            
        except Exception as e:
            # 使用示例值
            disk_info = {
                'total': float(100 * 1024 * 1024 * 1024),  # 100GB
                'used': float(50 * 1024 * 1024 * 1024),    # 50GB
                'free': float(50 * 1024 * 1024 * 1024),    # 50GB
                'percent': float(50.0),                     # 50%
                'framework_usage': float(1 * 1024 * 1024 * 1024)  # 1GB
            }
        
        # 构建返回数据 - 确保所有数值都是原生数值类型
        system_info = {
            # 系统CPU数据
            'cpu_percent': float(system_cpu_percent),
            'framework_cpu_percent': float(cpu_percent),
            'cpu_cores': cpu_cores,
            
            # 系统内存数据
            'memory_percent': float(system_memory_percent),
            'memory_used': float(system_memory_used),  # 使用系统实际占用的总内存
            'memory_total': float(system_memory_total),
            'total_memory': float(system_memory_total),  # 单位: MB
            'system_memory_total_bytes': float(system_memory.total),  # 原始字节数
            'framework_memory_percent': float((rss / system_memory_total) * 100 if system_memory_total > 0 else 5.0),
            'framework_memory_total': float(rss),
            
            # 内存分配详情
            'plugins_memory': float(plugins_mb),
            'framework_memory': float(framework_mb),
            'webpanel_memory': float(web_panel_mb),
            'other_memory': float(other_mb),
            
            # 内存管理数据
            'gc_counts': list(gc.get_count()),
            'objects_count': len(gc.get_objects()),
            
            # 硬盘使用情况
            'disk_info': disk_info,
            
            # 运行时间 - 确保是整数
            'uptime': app_uptime_seconds,  # 应用运行时间
            'system_uptime': system_uptime_seconds,  # 系统运行时间
            'start_time': start_time_str,  # 应用启动时间
            'boot_time': boot_time_str,  # 系统启动时间
            
            # 系统版本
            'system_version': system_version
        }
        
        return system_info
    except Exception as e:
        error_msg = f"获取系统信息过程中发生错误: {str(e)}"
        add_error_log(error_msg, traceback.format_exc())
        
        # 返回默认值确保前端能显示内容
        return {
            'cpu_percent': 5.0,
            'framework_cpu_percent': 1.0,
            'cpu_cores': 4,
            'memory_percent': 50.0,
            'memory_used': 400.0,  # 进程实际内存使用量，确保不为零
            'memory_total': 8192.0,
            'total_memory': 8192.0,  # MB
            'system_memory_total_bytes': 8192.0 * 1024 * 1024,  # 字节
            'framework_memory_percent': 5.0,
            'framework_memory_total': 400.0,
            'plugins_memory': 100.0,
            'framework_memory': 150.0,
            'webpanel_memory': 50.0,
            'other_memory': 100.0,
            'gc_counts': [0, 0, 0],
            'objects_count': 1000,
            'disk_info': {
                'total': float(100 * 1024 * 1024 * 1024),
                'used': float(50 * 1024 * 1024 * 1024),
                'free': float(50 * 1024 * 1024 * 1024),
                'percent': 50.0,
                'framework_usage': float(1 * 1024 * 1024 * 1024)
            },
            'uptime': 3600,
            'system_uptime': 86400,
            'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'boot_time': (datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
            'system_version': 'Windows 10 64-bit'
        }

@catch_error
def process_plugin_module(module, plugin_path, module_name, is_hot_reload=False, is_system=False, is_cold_load=False, dir_name=None):
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
                    'is_system': is_system,
                    'directory': dir_name
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
            'is_cold_load': is_cold_load,
            'directory': dir_name
        }
        # 添加修改时间（如果有）
        if last_modified_str:
            plugin_info['last_modified'] = last_modified_str
            
        plugin_info_list.append(plugin_info)
    
    return plugin_info_list

@catch_error
def load_plugin_module(plugin_file, module_name, is_hot_reload=False, is_system=False, is_cold_load=False):
    """加载插件模块并进行错误处理"""
    try:
        # 确定目录名
        dir_name = None
        if is_hot_reload:
            dir_name = os.path.basename(os.path.dirname(plugin_file))
            
        # 确定完整模块名称
        if dir_name:
            full_module_name = f"plugins.{dir_name}.{module_name}"
        else:
            full_module_name = f"plugins.{module_name}.main"
            
        # 动态导入模块
        spec = importlib.util.spec_from_file_location(full_module_name, plugin_file)
        if not spec or not spec.loader:
            return [{
                'name': f"{dir_name}/{module_name}" if dir_name else module_name,
                'class_name': 'unknown',
                'status': 'error',
                'error': '无法加载插件文件',
                'path': plugin_file,
                'is_hot_reload': is_hot_reload,
                'is_cold_load': is_cold_load,
                'directory': dir_name
            }]
            
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # 处理模块中的插件类
        return process_plugin_module(module, plugin_file, module_name, is_hot_reload, is_system, is_cold_load, dir_name)
        
    except Exception as e:
        dir_name = os.path.basename(os.path.dirname(plugin_file)) if is_hot_reload else None
        plugin_name = f"{dir_name}/{module_name}" if dir_name else module_name
        return [{
            'name': plugin_name,
            'class_name': 'unknown',
            'status': 'error',
            'error': str(e),
            'path': plugin_file,
            'is_hot_reload': is_hot_reload,
            'is_cold_load': is_cold_load,
            'directory': dir_name,
            'traceback': traceback.format_exc()
        }]

@catch_error
def scan_plugins():
    """扫描所有插件并获取其状态"""
    global plugins_info
    plugins_info = []
    
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    # 导入PluginManager获取热更新目录列表
    try:
        from core.plugin.PluginManager import PluginManager
        hot_reload_dirs = PluginManager._hot_reload_dirs
    except ImportError:
        hot_reload_dirs = ['example', 'system', 'alone']
        add_error_log("导入PluginManager失败，使用默认热更新目录列表")
    
    # 处理所有热更新目录下的插件
    for dir_name in hot_reload_dirs:
        hot_dir = os.path.join(script_dir, 'plugins', dir_name)
        if os.path.exists(hot_dir) and os.path.isdir(hot_dir):
            py_files = [f for f in os.listdir(hot_dir) if f.endswith('.py') and f != '__init__.py']
            
            for py_file in py_files:
                plugin_file = os.path.join(hot_dir, py_file)
                plugin_name = os.path.splitext(py_file)[0]
                
                # 加载热更新插件
                plugin_info_list = load_plugin_module(plugin_file, plugin_name, is_hot_reload=True, is_system=(dir_name == 'system'))
                for info in plugin_info_list:
                    info['directory'] = dir_name
                plugins_info.extend(plugin_info_list)
    
    # 处理非热更新目录下的插件 (仅main.py)
    for item in os.listdir(os.path.join(script_dir, 'plugins')):
        if item not in hot_reload_dirs and os.path.isdir(os.path.join(script_dir, 'plugins', item)):
            main_py = os.path.join(script_dir, 'plugins', item, 'main.py')
            if os.path.exists(main_py):
                # 加载标准插件
                plugin_info_list = load_plugin_module(main_py, item, is_cold_load=True)
                plugins_info.extend(plugin_info_list)
    
    # 按状态排序：已加载的排在前面，然后按热更新排序
    plugins_info.sort(key=lambda x: (0 if x['status'] == 'loaded' else 1, 0 if x.get('is_hot_reload', False) else 1))
    return plugins_info

@catch_error
def register_socketio_handlers(sio):
    """注册Socket.IO事件处理函数"""
    @sio.on('connect', namespace=PREFIX)
    def handle_connect():
        sid = request.sid
        
        # 在后台线程中扫描插件状态，避免阻塞连接
        def async_load_initial_data():
            # 构建初始系统信息
            system_info = get_system_info()
            
            # 发送系统信息
            try:
                sio.emit('system_info', system_info, room=sid, namespace=PREFIX)
            except Exception:
                pass
                
            # 然后加载插件数据（可能较慢）
            plugins = scan_plugins()
            
            try:
                # 发送插件信息
                sio.emit('plugins_update', plugins, room=sid, namespace=PREFIX)
                
                # 构建日志数据包
                logs_data = {
                    'received': {
                        'logs': list(received_handler.logs)[-30:],
                        'total': len(received_handler.logs),
                        'page': 1,
                        'page_size': 30
                    },
                    'plugin': {
                        'logs': list(plugin_handler.logs)[-30:],
                        'total': len(plugin_handler.logs),
                        'page': 1,
                        'page_size': 30
                    },
                    'framework': {
                        'logs': list(framework_handler.logs)[-30:],
                        'total': len(framework_handler.logs),
                        'page': 1,
                        'page_size': 30
                    },
                    'error': {
                        'logs': list(error_handler.logs)[-30:],
                        'total': len(error_handler.logs),
                        'page': 1,
                        'page_size': 30
                    }
                }
                
                # 所有类型的日志都逆序排列
                for log_type in logs_data:
                    if 'logs' in logs_data[log_type]:
                        logs_data[log_type]['logs'].reverse()
                
                # 发送日志数据
                sio.emit('logs_batch', logs_data, room=sid, namespace=PREFIX)
            except Exception:
                pass
        
        # 启动后台线程加载数据
        threading.Thread(target=async_load_initial_data, daemon=True).start()

    @sio.on('disconnect', namespace=PREFIX)
    def handle_disconnect():
        # 最小化日志，完全不处理断开连接
        pass

    @sio.on('get_system_info', namespace=PREFIX)
    def handle_get_system_info():
        """处理客户端请求系统信息的事件"""
        try:
            # 直接获取系统信息，最小化日志
            system_info = get_system_info()
            
            # 仅发送给当前请求的客户端
            sio.emit('system_info', system_info, room=request.sid, namespace=PREFIX)
        except Exception:
            # 如果出现错误，传递一个默认的空的系统信息
            default_info = {
                'cpu_percent': 5.0,
                'framework_cpu_percent': 1.0,
                'memory_percent': 50.0,
                'memory_used': 4096.0,
                'memory_total': 8192.0,
                'framework_memory_percent': 5.0,
                'framework_memory_total': 400.0,
                'plugins_memory': 100.0,
                'framework_memory': 150.0,
                'webpanel_memory': 50.0,
                'other_memory': 100.0,
                'gc_counts': [0, 0, 0],
                'objects_count': 1000,
                'disk_info': {
                    'total': float(100 * 1024 * 1024 * 1024),
                    'used': float(50 * 1024 * 1024 * 1024),
                    'free': float(50 * 1024 * 1024 * 1024),
                    'percent': 50.0
                },
                'uptime': 3600,
                'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            sio.emit('system_info', default_info, room=request.sid, namespace=PREFIX)

    @sio.on('refresh_plugins', namespace=PREFIX)
    def handle_refresh_plugins():
        """处理刷新插件信息的请求"""
        # 在后台线程中执行插件扫描
        def async_scan_plugins():
            plugins = scan_plugins()
            try:
                sio.emit('plugins_update', plugins, room=request.sid, namespace=PREFIX)
            except Exception:
                pass
                
        # 启动后台线程
        threading.Thread(target=async_scan_plugins, daemon=True).start()

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
        logs.reverse()  # 最新的在前面
        
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
    # 初始化Web面板，不输出日志
    if main_app is None:
        app = Flask(__name__)
        app.register_blueprint(web_panel, url_prefix=PREFIX)
        CORS(app, resources={r"/*": {"origins": "*"}})
        try:
            socketio = SocketIO(app, 
                            cors_allowed_origins="*",
                            path="/socket.io",
                            logger=False,
                            engineio_logger=False)
            # 关键：注册Socket.IO处理函数
            register_socketio_handlers(socketio)
        except Exception as e:
            print(f"Socket.IO初始化错误: {str(e)}")
            error_tb = traceback.format_exc()
            print(error_tb)
            add_log_to_db('error', {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'content': f"Socket.IO初始化错误: {str(e)}",
                'traceback': error_tb
            })

        return app, socketio
    else:
        main_app.register_blueprint(web_panel, url_prefix=PREFIX)
        try:
            CORS(main_app, resources={r"/*": {"origins": "*"}})
        except Exception:
            pass
        try:
            if hasattr(main_app, 'socketio'):
                socketio = main_app.socketio
            else:
                socketio = SocketIO(main_app, 
                                cors_allowed_origins="*",
                                path="/socket.io",
                                logger=False,
                                engineio_logger=False)
                main_app.socketio = socketio
            # 关键：注册Socket.IO处理函数
            register_socketio_handlers(socketio)
        except Exception as e:
            print(f"Socket.IO初始化错误: {str(e)}")
            error_tb = traceback.format_exc()
            print(error_tb)
            add_log_to_db('error', {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'content': f"Socket.IO初始化错误: {str(e)}",
                'traceback': error_tb
            })

        return None