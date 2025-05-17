#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
import time
import json
import base64
import random
import threading
import logging
import logging.handlers
from flask import Flask, request, jsonify, make_response
from flask_socketio import SocketIO

# 导入配置
from config import appid, secret, LOG_CONFIG

# 配置日志系统
def setup_logging():
    """初始化日志系统"""
    # 确保日志目录存在
    log_file = LOG_CONFIG.get('file', 'logs/mbot.log')
    log_dir = os.path.dirname(log_file)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 获取日志配置
    log_level = getattr(logging, LOG_CONFIG.get('level', 'INFO'))
    log_format = LOG_CONFIG.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    max_size = LOG_CONFIG.get('max_size', 10485760)  # 10MB
    backup_count = LOG_CONFIG.get('backup_count', 5)
    
    # 配置日志处理器
    formatter = logging.Formatter(log_format)
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    
    # 文件处理器
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_file,
        maxBytes=max_size,
        backupCount=backup_count,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    
    # 配置根日志记录器
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    # 设置特定模块的日志级别
    logging.getLogger('werkzeug').setLevel(logging.WARNING)
    logging.getLogger('socketio').setLevel(logging.WARNING)
    logging.getLogger('engineio').setLevel(logging.WARNING)
    
    logging.info("日志系统初始化完成")

# 导入功能模块
from function.Access import BOT凭证, BOTAPI, Json取, Json

# 导入Web面板
from web_panel.app import start_web_panel, add_received_message, add_plugin_log, add_framework_log, parse_message_content

# 创建主应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'mbot_secret'

# 创建全局Socket.IO实例
socketio = SocketIO(app, 
                   cors_allowed_origins="*",
                   async_mode='threading',
                   logger=False,
                   engineio_logger=False)

# 将Socket.IO实例绑定到app
app.socketio = socketio

def format_bytes(bytes_num, precision=2):
    """转换字节为易读格式"""
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    bytes_num = max(bytes_num, 0)
    pow_val = min(int(pow(bytes_num, 1/1024) if bytes_num > 0 else 0), len(units) - 1)
    bytes_num /= (1 << (10 * pow_val))
    return f"{round(bytes_num, precision)} {units[pow_val]}"

def 上传群图片(group, content):
    """上传图片到群"""
    content_base64 = base64.b64encode(content).decode()
    return json.loads(BOTAPI(f"/v2/groups/{group}/files", "POST", Json({"srv_send_msg": False, "file_type": 1, "file_data": content_base64})))

def 发群(group, content):
    """发送消息到群"""
    return BOTAPI(f"/v2/groups/{group}/messages", "POST", Json(content))

def 发群2(group, content, type=0, id=None):
    """使用ark模板发送消息到群"""
    ark = {
        'template_id': 23,
        'kv': [
            {'key': '#DESC#', 'value': 'TSmoe'},
            {'key': '#PROMPT#', 'value': '闲仁Bot'},
            {
                'key': '#LIST#',
                'obj': [
                    {
                        'obj_kv': [
                            {'key': 'desc', 'value': content}
                        ]
                    }
                ]
            }
        ]
    }
    return BOTAPI(f"/v2/groups/{group}/messages", "POST", json.dumps({
        "msg_id": id,
        "msg_type": 3,
        "ark": ark,
        "msg_seq": random.randint(10000, 999999)
    }))

def convert_url(url):
    """转换URL格式"""
    if url is None:
        return ''
    
    url_str = str(url)
    parts = url_str.split('://', 1)
    
    if len(parts) == 1:
        return url_str.upper()
    
    protocol = parts[0].lower()
    rest = parts[1]
    
    host_part = rest.split('/', 1)[0] if '/' in rest else rest.split('?', 1)[0] if '?' in rest else rest.split('#', 1)[0] if '#' in rest else rest
    separator_index = rest.find(host_part) + len(host_part)
    
    return protocol + '://' + host_part.upper() + rest[separator_index:]

# 主应用路由
@app.route('/', methods=['GET', 'POST'])
def handle_request():
    # 立即返回200响应
    if request.method == 'GET':
        type_param = request.args.get('type')
        if type_param:
            if type_param == 'test':
                msg = request.args.get('msg')
                data = {
                    't': 'test',
                    'd': {
                        'id': 'test',
                        'content': msg,
                        'timestamp': int(time.time()),
                        'group_id': 'test_group',
                        'author': {'id': 'test_user'}
                    }
                }
                
                # 导入需要的模块
                from core.plugin.PluginManager import PluginManager
                from core.event.MessageEvent import MessageEvent
                
                # 初始化插件管理器并加载插件
                plugin_manager = PluginManager()
                plugin_manager.load_plugins()
                
                # 创建消息事件并分发
                event = MessageEvent(Json(data))
                plugin_manager.dispatch_message(event)
                
                return "OK"
            
            return "Type handled"
        
        # 收集服务状态信息
        import platform
        import psutil
        
        info = "服务状态信息\n"
        me = BOTAPI("/users/@me", "GET", "")
        name = Json取(me, 'username')
        info += "==================\n"
        info += f"Bot: {name}\n"
        info += f"Python 版本: {platform.python_version()}\n"
        info += "服务状态: 运行中\n"
        
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        
        info += f"当前内存使用: {format_bytes(memory_info.rss)}\n"
        info += f"服务器时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        info += f"Python 运行模式: {sys.implementation.name}\n"
        info += f"操作系统: {platform.system()} {platform.release()}\n"
        
        return me
    
    # 处理POST请求
    data = request.get_data()
    if not data:
        return "No data received", 400
    
    json_data = json.loads(data)
    op = json_data.get("op")
    t = json_data.get("t")
    
    # 签名校验
    if op == 13:
        from function.sign import Signs
        sign = Signs()
        return sign.sign(data.decode())
    
    # 消息事件
    if op == 0:
        from core.plugin.PluginManager import PluginManager
        from core.event.MessageEvent import MessageEvent
        
        # 记录接收到的消息
        formatted_message = parse_message_content(json_data)
        add_received_message(formatted_message)
        
        plugin_manager = PluginManager()
        plugin_manager.load_plugins()
        event = MessageEvent(data.decode())
        
        # 分发消息处理
        plugin_manager.dispatch_message(event)
        
        return "OK"
    
    return "Event not handled", 400

if __name__ == "__main__":
    # 初始化日志系统
    setup_logging()
    logging.info("MBot服务启动")
    
    # 初始化Web面板
    start_web_panel(app)
    
    # 日志记录
    add_framework_log(f"MBot服务启动，监听端口: 5001")
    
    # 使用socketio启动应用（这将同时服务于主应用和web面板）
    socketio.run(app, host='0.0.0.0', port=5001, debug=False, allow_unsafe_werkzeug=True)  