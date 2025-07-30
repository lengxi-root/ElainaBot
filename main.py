#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Elaina框架 - 性能优化版本

主要优化点：
1. 插件加载优化：避免每次消息都重新加载插件，只在文件变化时才重新加载
2. 正则表达式优化：预排序处理器列表，避免每次重新排序
3. JSON解析缓存：避免重复解析相同数据
4. 异步垃圾回收：将垃圾回收异步化，减少阻塞
5. 线程池复用：使用线程池处理消息，避免频繁创建线程
6. 数据库连接池简化：移除不必要的复杂逻辑
7. HTTP连接池优化：调整连接参数提升网络性能
8. 内存管理优化：减少对象创建，优化生命周期
9. 日志系统优化：简化日志处理逻辑，提升性能

关键Bug修复（Python 3.13兼容性）：
- 修复正则表达式补丁在Python 3.13中的递归问题
- 使用数值常量替代枚举操作，避免无限递归
- 改进Pattern对象检查逻辑，确保插件正则补丁正常工作
"""

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
try:
    import eventlet
    eventlet.monkey_patch()  # 必须在导入其他模块前进行monkey patch
except ImportError:
    pass  # 如果没有安装eventlet，则跳过

# ===== 2. 第三方库导入 =====
from flask import Flask, request, jsonify
from flask_socketio import SocketIO
# ===== 3. 自定义模块导入 =====
from config import LOG_CONFIG, LOG_DB_CONFIG
from web_panel.app import start_web_panel, add_received_message, add_plugin_log, add_framework_log, add_error_log
try:
    from function.log_db import add_log_to_db
except ImportError:
    def add_log_to_db(log_type, log_data):
        return False
from function.Access import BOT凭证, BOTAPI, Json取, Json
from function.httpx_pool import async_get, async_post, sync_get, sync_post, get_pool_manager

# ===== 4. 全局状态变量 =====
_logging_initialized = False  # 日志系统初始化标志
_app_initialized = False      # 应用初始化标志
http_pool = get_pool_manager() # HTTP连接池

# JSON解析缓存，避免重复解析相同数据
_json_cache = {}
_json_cache_max_size = 100
_json_cache_access_count = {}

# 异步垃圾回收相关
_gc_thread_pool = None
_last_async_gc_time = 0
_async_gc_interval = 30  # 异步垃圾回收间隔(秒)

# 消息处理线程池
_message_thread_pool = None

# 性能监控相关
_performance_stats = {
    'total_messages': 0,
    'plugin_hits': 0,
    'cache_hits': 0,
    'avg_response_time': 0.0,
    'start_time': 0
}

# ===== 5. 日志系统 =====
class NullHandler(logging.Handler):
    """空日志处理器，不输出任何内容"""
    def emit(self, record):
        pass

def setup_logging():
    """初始化日志系统，允许显示预热和初始化日志"""
    global _logging_initialized
    
    if _logging_initialized:
        return
    
    # 配置根日志记录器
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # 创建格式化器
    formatter = logging.Formatter(
        LOG_CONFIG.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    
    # 创建控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # 设置根日志级别为INFO，允许显示更多日志
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(console_handler)
    
    # 为主模块设置特殊处理
    main_logger = logging.getLogger(__name__)
    main_logger.setLevel(logging.DEBUG)
    
    # 允许httpx_pool和预热相关的INFO级别日志显示
    for logger_name in ['httpx_pool', 'db_pool', 'log_db']:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.INFO)
    
    # 仅禁用一些不必要的第三方库日志
    for logger_name in ['werkzeug', 'socketio', 'engineio', 'urllib3']:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.WARNING)
        logger.propagate = False
    
    _logging_initialized = True
    print("日志系统初始化完成 (允许显示预热和初始化日志)")

# 全局异常处理
def global_exception_handler(exctype, value, tb):
    """处理所有未捕获的异常，记录到错误日志"""
    error_msg = f"未捕获的异常: {exctype.__name__}: {value}"
    tb_str = "".join(traceback.format_tb(tb))
    print(error_msg)
    print(tb_str)
    add_error_log(error_msg, tb_str)
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = global_exception_handler

# ===== 5.5. JSON解析优化 =====
def cached_json_loads(data):
    """
    带缓存的JSON解析，避免重复解析相同数据
    使用LRU策略管理缓存大小
    """
    global _json_cache, _json_cache_access_count, _json_cache_max_size
    
    # 计算数据哈希作为缓存键
    data_hash = hash(data) if isinstance(data, str) else hash(data.decode('utf-8', errors='ignore'))
    
    # 如果缓存中存在，直接返回并更新访问计数
    if data_hash in _json_cache:
        _json_cache_access_count[data_hash] = _json_cache_access_count.get(data_hash, 0) + 1
        _performance_stats['cache_hits'] += 1
        return _json_cache[data_hash]
    
    # 解析JSON
    try:
        if isinstance(data, bytes):
            parsed_data = json.loads(data.decode('utf-8'))
        else:
            parsed_data = json.loads(data)
        
        # 添加到缓存，如果缓存已满则删除最少使用的项
        if len(_json_cache) >= _json_cache_max_size:
            # 找到访问次数最少的项并删除
            min_access_key = min(_json_cache_access_count, key=_json_cache_access_count.get)
            del _json_cache[min_access_key]
            del _json_cache_access_count[min_access_key]
        
        _json_cache[data_hash] = parsed_data
        _json_cache_access_count[data_hash] = 1
        
        return parsed_data
    except json.JSONDecodeError as e:
        raise e

def async_garbage_collect():
    """异步执行垃圾回收，避免阻塞主线程"""
    global _last_async_gc_time
    current_time = time.time()
    
    if current_time - _last_async_gc_time >= _async_gc_interval:
        def gc_task():
            try:
                collected = gc.collect()
                logging.debug(f"异步垃圾回收完成，回收了 {collected} 个对象")
                return collected
            except Exception as e:
                logging.error(f"异步垃圾回收失败: {str(e)}")
                return 0
        
        # 提交到线程池异步执行
        if _gc_thread_pool:
            _gc_thread_pool.submit(gc_task)
            _last_async_gc_time = current_time

def schedule_async_gc():
    """调度异步垃圾回收（非阻塞）"""
    global _last_async_gc_time
    current_time = time.time()
    
    # 检查是否需要执行垃圾回收
    if current_time - _last_async_gc_time >= _async_gc_interval:
        async_garbage_collect()

# ===== 6. Flask应用与SocketIO =====
# 禁用Flask启动横幅
import flask.cli
flask.cli.show_server_banner = lambda *args: None

def create_app():
    """创建并配置Flask应用"""
    flask_app = Flask(__name__)
    flask_app.config['SECRET_KEY'] = 'elaina_secret'
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
                json_data = cached_json_loads(data)
            except json.JSONDecodeError as e:
                error_msg = f"JSON解析错误: {str(e)}"
                raw_data = data[:1024].decode('utf-8', errors='replace') + ("..." if len(data) > 1024 else "")
                add_error_log(error_msg, f"原始数据: {raw_data}")
                
                if LOG_DB_CONFIG.get('enabled', False):
                    try:
                        add_log_to_db('error', {
                            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                            'content': error_msg,
                            'traceback': f"原始数据: {raw_data}"
                        })
                    except Exception:
                        pass
                        
                return "Invalid JSON data", 400
                
            op = json_data.get("op")
            
            # 先检查op是否为0（消息处理）
            if op == 0:
                # 使用线程池处理消息，避免频繁创建线程
                if _message_thread_pool:
                    _message_thread_pool.submit(
                        process_message_event, 
                        json_data, 
                        data.decode()
                    )
                else:
                    # 如果线程池不可用，回退到创建线程
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
        print(f"记录消息失败: {str(e)}")

def process_message_event(data, raw_data=None):
    """处理消息事件，分发到插件系统（带性能监控）"""
    if not data:
        return False
    
    # 性能监控开始
    start_time = time.time()
    global _performance_stats
    _performance_stats['total_messages'] += 1
        
    record_message(data)
    
    # 局部导入减少全局命名空间污染
    from core.plugin.PluginManager import PluginManager
    from core.event.MessageEvent import MessageEvent
    
    plugin_manager = PluginManager()
    event = MessageEvent(raw_data if raw_data else data)
    result = plugin_manager.dispatch_message(event)
    
    # 更新性能统计
    if result:
        _performance_stats['plugin_hits'] += 1
    
    # 计算平均响应时间
    response_time = time.time() - start_time
    total_msgs = _performance_stats['total_messages']
    current_avg = _performance_stats['avg_response_time']
    _performance_stats['avg_response_time'] = (current_avg * (total_msgs - 1) + response_time) / total_msgs
    
    # 随机进行异步垃圾回收（非阻塞）
    if random.random() < 0.05:
        schedule_async_gc()
        
    return result

# ===== 8. 系统初始化 =====
def init_systems():
    """初始化所有系统组件"""
    global _gc_thread_pool, _message_thread_pool
    results = []
    success = True
    
    # 初始化线程池
    try:
        import concurrent.futures
        # 异步垃圾回收线程池
        _gc_thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, 
            thread_name_prefix="AsyncGC"
        )
        # 消息处理线程池，根据CPU核心数设置
        max_workers = min(8, (os.cpu_count() or 1) + 4)
        _message_thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="MessageProcessor"
        )
        results.append(f"线程池初始化成功 (垃圾回收:1个, 消息处理:{max_workers}个)")
    except Exception as e:
        results.append(f"线程池初始化失败: {str(e)}")
        success = False
    
    # 初始化日志系统
    try:
        setup_logging()
        results.append("日志系统初始化成功")
    except Exception as e:
        results.append(f"日志系统初始化失败: {str(e)}")
        success = False
    
    # 初始化消息记录系统
    try:
        results.append("消息记录系统已启用 (数据库模式)")
    except Exception as e:
        results.append(f"消息记录系统初始化失败: {str(e)}")
        success = False
    
    # 优化垃圾回收
    try:
        gc.enable()
        gc.set_threshold(700, 10, 5)  # 调整阈值，减少GC频率
        collected = gc.collect(0)
        results.append(f"内存管理初始化完成，回收了 {collected} 个对象")
    except Exception as e:
        results.append(f"内存管理初始化失败: {str(e)}")
    
    # 异步预加载插件
    try:
        def load_plugins_async():
            from core.plugin.PluginManager import PluginManager
            plugin_manager = PluginManager()
            logging.info("开始预加载插件系统...")
            plugins = plugin_manager.load_plugins()
            logging.info(f"插件系统预加载完成, 加载了 {plugins} 个插件")
            return plugins
        
        plugin_thread = threading.Thread(target=load_plugins_async, daemon=True)
        plugin_thread.start()
        plugin_thread.join(timeout=1)  # 减少等待时间，避免阻塞启动过程
        results.append("插件系统预加载启动")
    except Exception as e:
        results.append(f"插件系统预加载失败: {str(e)}")
    
    # 预热HTTP连接池
    try:
        def warmup_pool():
            logging.info("开始预热HTTP连接池...")
            try:
                start_time = time.time()
                sync_get("https://baidu.com", timeout=3)
                elapsed = time.time() - start_time
                logging.info(f"HTTP连接池预热完成，耗时 {elapsed:.2f} 秒")
            except Exception as e:
                logging.warning(f"HTTP连接池预热失败: {str(e)}")
        
        thread = threading.Thread(target=warmup_pool, daemon=True)
        thread.start()
        results.append("HTTP连接池初始化成功")
    except Exception as e:
        results.append(f"HTTP连接池初始化失败: {str(e)}")
    
    return success, results

def initialize_app():
    """初始化应用，确保只执行一次"""
    global _app_initialized, app
    
    if _app_initialized:
        logging.info("应用已初始化，跳过重复初始化")
        return app
    
    # 创建Flask应用
    app = create_app()
    
    # 初始化系统组件
    success, results = init_systems()
    
    # 记录启动日志
    logging.info("Elaina框架服务启动")
    for result in results:
        logging.info(result)
    
    if not success:
        logging.warning("部分系统组件初始化失败，服务可能无法正常工作")
    
    # 初始化Web面板
    if not any(bp.name == 'web_panel' for bp in app.blueprints.values()):
        start_web_panel(app)
        add_framework_log("Elaina框架服务启动，使用生产环境模式")
    
    # 设置性能监控开始时间
    _performance_stats['start_time'] = time.time()
    
    _app_initialized = True
    return app

# ===== 9. 主入口 =====
# WSGI应用入口点 - 用于Gunicorn
wsgi_app = initialize_app()

if __name__ == "__main__":
    try:
        # 确保应用已初始化
        app = initialize_app()
        
        logging.info("以生产环境模式启动")
        
        # 使用eventlet作为服务器
        import eventlet
        from eventlet import wsgi
        
        # 调整werkzeug日志级别
        werkzeug_log = logging.getLogger('werkzeug')
        werkzeug_log.setLevel(logging.WARNING)
        
        logging.info("使用Eventlet WSGI服务器")
        
        # 使用wsgi.server函数
        wsgi.server(
            eventlet.listen(('0.0.0.0', 5001)),
            app,
            log=None,
            log_output=False,
            keepalive=True,
            socket_timeout=30
        )
    except Exception as e:
        error_msg = f"Elaina框架服务启动失败: {str(e)}"
        traceback_str = traceback.format_exc()
        print(error_msg)
        print(traceback_str)
        
        try:
            add_error_log(error_msg, traceback_str)
        except Exception:
            print("记录启动错误失败")
            
        if LOG_DB_CONFIG.get('enabled', False):
            try:
                add_log_to_db('error', {
                    'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                    'content': error_msg,
                    'traceback': traceback_str
                })
            except Exception:
                print("写入日志数据库失败")
                
        sys.exit(1)  