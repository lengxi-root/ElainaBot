#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ===== 1. 标准库导入 =====
import sys
import os
import time
import json
import base64
import random
import threading
import logging
import logging.handlers
import gc  # 导入垃圾回收模块
import warnings
import urllib3
import traceback  # 添加traceback模块

# ===== 2. 第三方库导入 =====
from flask import Flask, request, jsonify, make_response
from flask_socketio import SocketIO

# ===== 3. 自定义模块导入 =====
from config import appid, secret, LOG_CONFIG, LOG_DB_CONFIG
from web_panel.app import start_web_panel, add_received_message, add_plugin_log, add_framework_log, add_error_log
try:
    from function.log_db import add_log_to_db
except ImportError:
    def add_log_to_db(log_type, log_data):
        return False
from function.Access import BOT凭证, BOTAPI, Json取, Json

# ===== 4. 全局配置与变量 =====
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)  # 禁用urllib3不安全请求的警告

# ===== 5. 日志系统与全局异常处理 =====
def global_exception_handler(exctype, value, tb):
    """
    处理所有未捕获的异常，记录到错误日志并打印到控制台。
    """
    error_msg = f"未捕获的异常: {exctype.__name__}: {value}"
    tb_str = "".join(traceback.format_tb(tb))
    print(error_msg)
    print(tb_str)
    add_error_log(error_msg, tb_str)
    sys.__excepthook__(exctype, value, tb)

sys.excepthook = global_exception_handler

def setup_logging():
    """
    初始化日志系统，仅控制台输出。
    """
    formatter = logging.Formatter(
        LOG_CONFIG.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_CONFIG.get('level', 'INFO')))
    root_logger.addHandler(console_handler)
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('socketio').setLevel(logging.WARNING)
    logging.getLogger('engineio').setLevel(logging.WARNING)
    print("日志系统初始化完成 (仅控制台输出模式)")

def setup_message_recorder():
    """
    初始化消息记录系统（数据库模式）。
    """
    print("消息记录系统已初始化，使用数据库记录模式")
    return None

def record_message(message_data):
    """
    记录接收到的消息到Web面板和数据库。
    """
    try:
        add_received_message(message_data)
    except Exception as e:
        print(f"记录消息失败: {str(e)}")

# ===== 6. 主 Flask 应用与 SocketIO 实例 =====
app = Flask(__name__)
app.config['SECRET_KEY'] = 'mbot_secret'
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.jinja_env.auto_reload = True
socketio = SocketIO(app, 
                   cors_allowed_origins="*",
                   async_mode='threading',
                   logger=False,
                   engineio_logger=False)
app.socketio = socketio

# ===== 7. 工具函数 =====
# （已淘汰：upload_media、send_message、上传群图片、发群、format_bytes、convert_url等函数，推荐统一通过MessageEvent类实现）

def process_message_event(data, raw_data=None):
    """
    集中处理消息事件，记录消息并分发到插件系统。
    """
    record_message(data)
    from core.plugin.PluginManager import PluginManager
    from core.event.MessageEvent import MessageEvent
    plugin_manager = PluginManager()
    plugin_manager.load_plugins()
    event = MessageEvent(raw_data if raw_data else data)
    return plugin_manager.dispatch_message(event)

# ===== 8. 主路由 =====
@app.route('/', methods=['GET', 'POST'])
def handle_request():
    """
    主入口路由，处理GET/POST请求。
    """
    try:
        if request.method == 'GET':
            type_param = request.args.get('type')
            if type_param:
                return "Type handled"
            return "MBot 服务已启动"
        data = request.get_data()
        if not data:
            return "No data received", 400
        json_data = json.loads(data)
        op = json_data.get("op")
        if op == 13:
            from function.sign import Signs
            sign = Signs()
            return sign.sign(data.decode())
        if op == 0:
            process_message_event(json_data, data.decode())
            return "OK"
        return "Event not handled", 400
    except Exception as e:
        error_msg = f"处理请求时发生错误: {str(e)}"
        add_error_log(error_msg, traceback.format_exc())
        return "Server error", 500

# ===== 9. 系统初始化与主入口 =====
def init_systems():
    """
    初始化所有系统组件（日志、消息记录、内存管理、插件等）。
    """
    success = True
    results = []
    try:
        setup_logging()
        results.append("日志系统初始化成功")
    except Exception as e:
        results.append(f"日志系统初始化失败: {str(e)}")
        success = False
    try:
        setup_message_recorder()
        results.append("消息记录系统已启用 (数据库模式)")
    except Exception as e:
        results.append(f"消息记录系统初始化失败: {str(e)}")
        success = False
    try:
        gc.enable()
        collected = gc.collect()
        results.append(f"内存管理初始化完成，回收了 {collected} 个对象")
    except Exception as e:
        results.append(f"内存管理初始化失败: {str(e)}")
    try:
        from core.plugin.PluginManager import PluginManager
        plugin_manager = PluginManager()
        plugin_count = plugin_manager.load_plugins()
        results.append(f"插件系统预加载完成，加载了 {plugin_count} 个插件")
    except Exception as e:
        results.append(f"插件系统预加载失败: {str(e)}")
    return success, results

if __name__ == "__main__":
    try:
        # 初始化系统组件
        success, results = init_systems()
        logging.info("MBot服务启动")
        for result in results:
            logging.info(result)
        if not success:
            logging.warning("部分系统组件初始化失败，服务可能无法正常工作")
        # 初始化Web面板
        start_web_panel(app)
        # 记录启动日志
        for result in results:
            add_framework_log(result)
        add_framework_log(f"MBot服务启动，监听端口: 5001")
        # 启动SocketIO服务
        socketio.run(app, host='0.0.0.0', port=5001, debug=False, allow_unsafe_werkzeug=True)
    except Exception as e:
        error_msg = f"MBot服务启动失败: {str(e)}"
        traceback_str = traceback.format_exc()
        print(error_msg)
        print(traceback_str)
        try:
            add_error_log(error_msg, traceback_str)
        except Exception as e2:
            print(f"记录启动错误失败: {str(e2)}")
            if LOG_DB_CONFIG.get('enabled', False):
                try:
                    log_data = {
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                        'content': error_msg,
                        'traceback': traceback_str
                    }
                    add_log_to_db('error', log_data)
                except Exception as e3:
                    print(f"写入日志数据库失败: {str(e3)}")
        sys.exit(1)  