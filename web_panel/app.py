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
import gc
import warnings
import logging
import hashlib
import hmac
import base64
import uuid
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, render_template, request, jsonify, Blueprint, make_response
from flask_socketio import SocketIO
from flask_cors import CORS
import psutil
import requests
from config import LOG_DB_CONFIG, WEB_SECURITY, ROBOT_QQ, appid, WEBSOCKET_CONFIG
try:
    from function.log_db import add_log_to_db
except ImportError:
    def add_log_to_db(log_type, log_data):
        return False

def get_websocket_status():
    """获取WebSocket连接状态"""
    try:
        from function.ws_client import get_client
        client = get_client("qq_bot")
        if client and hasattr(client, 'connected'):
            return "连接中" if client.connected else "连接失败"
        else:
            return "连接失败"
    except Exception as e:
        print(f"检查WebSocket状态失败: {e}")
        return "连接失败"

PREFIX = '/web'
web_panel = Blueprint('web_panel', __name__, 
                     static_url_path=f'{PREFIX}/static',
                     static_folder='static',  
                     template_folder='templates')
socketio = None
MAX_LOGS = 1000
received_messages = deque(maxlen=MAX_LOGS)
plugin_logs = deque(maxlen=MAX_LOGS)
framework_logs = deque(maxlen=MAX_LOGS)
error_logs = deque(maxlen=MAX_LOGS)
_last_gc_time = 0
_gc_interval = 30
plugins_info = []
START_TIME = datetime.now()
from core.event.MessageEvent import MessageEvent

valid_sessions = {}
_last_session_cleanup = 0
IP_DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'ip.json')
ip_access_data = {}
_last_ip_cleanup = 0

statistics_cache = None
statistics_cache_time = 0
STATISTICS_CACHE_DURATION = 300


def extract_device_info(request):
    """提取设备信息"""
    user_agent = request.headers.get('User-Agent', '')
    
    device_info = {
        'user_agent': user_agent[:500],
        'accept_language': request.headers.get('Accept-Language', '')[:100],
        'accept_encoding': request.headers.get('Accept-Encoding', '')[:100],
        'last_update': datetime.now().isoformat()
    }
    

    user_agent_lower = user_agent.lower()
    if any(keyword in user_agent_lower for keyword in ['android', 'iphone', 'ipad', 'mobile', 'phone']):
        device_info['device_type'] = 'mobile'
    elif any(keyword in user_agent_lower for keyword in ['tablet']):
        device_info['device_type'] = 'tablet'
    else:
        device_info['device_type'] = 'desktop'
    

    if 'chrome' in user_agent_lower:
        device_info['browser'] = 'chrome'
    elif 'firefox' in user_agent_lower:
        device_info['browser'] = 'firefox'
    elif 'safari' in user_agent_lower and 'chrome' not in user_agent_lower:
        device_info['browser'] = 'safari'
    elif 'edge' in user_agent_lower:
        device_info['browser'] = 'edge'
    else:
        device_info['browser'] = 'unknown'
    
    return device_info

def load_ip_data():
    """加载IP访问数据"""
    global ip_access_data
    try:
        if os.path.exists(IP_DATA_FILE):
            with open(IP_DATA_FILE, 'r', encoding='utf-8') as f:
                ip_access_data = json.load(f)
        else:
            ip_access_data = {}
    except Exception as e:
        ip_access_data = {}

def save_ip_data():
    """保存IP数据到文件"""
    try:
        with open(IP_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(ip_access_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        pass

def record_ip_access(ip_address, access_type='token_success', device_info=None):
    """记录IP访问"""
    global ip_access_data
    current_time = datetime.now()
    
    if ip_address not in ip_access_data:
        ip_access_data[ip_address] = {
            'first_access': current_time.isoformat(),
            'last_access': current_time.isoformat(),
            'token_success_count': 0,
            'password_fail_count': 0,
            'password_fail_times': [],
            'password_success_count': 0,
            'password_success_times': [],
            'device_info': {},
            'is_banned': False,
            'ban_time': None
        }
    
    ip_data = ip_access_data[ip_address]
    ip_data['last_access'] = current_time.isoformat()
    
    if access_type == 'token_success':
        ip_data['token_success_count'] += 1

        if device_info:
            ip_data['device_info'] = device_info
    elif access_type == 'password_fail':
        ip_data['password_fail_count'] += 1
        ip_data['password_fail_times'].append(current_time.isoformat())
        
        cleanup_old_password_fails(ip_address)
        recent_fails = len([t for t in ip_data['password_fail_times'] 
                           if (current_time - datetime.fromisoformat(t)).total_seconds() < 24 * 3600])
        
        if recent_fails >= 5:
            ip_data['is_banned'] = True
            ip_data['ban_time'] = current_time.isoformat()
    elif access_type == 'password_success':
        ip_data['password_success_count'] += 1
        ip_data['password_success_times'].append(current_time.isoformat())
        cleanup_old_password_success(ip_address)
        if device_info:
            ip_data['device_info'] = device_info
    
    save_ip_data()

def cleanup_old_password_fails(ip_address):
    """清理24小时前的密码失败记录"""
    if ip_address not in ip_access_data:
        return
    
    current_time = datetime.now()
    ip_data = ip_access_data[ip_address]
    

    ip_data['password_fail_times'] = [
        t for t in ip_data['password_fail_times']
        if (current_time - datetime.fromisoformat(t)).total_seconds() < 24 * 3600
    ]

def cleanup_old_password_success(ip_address):
    """清理超过30天的密码成功记录"""
    if ip_address not in ip_access_data:
        return
    
    current_time = datetime.now()
    ip_data = ip_access_data[ip_address]
    

    ip_data['password_success_times'] = [
        t for t in ip_data['password_success_times']
        if (current_time - datetime.fromisoformat(t)).total_seconds() < 30 * 24 * 3600
    ]

def is_ip_banned(ip_address):
    """检查IP是否被封禁"""
    global ip_access_data
    
    if ip_address not in ip_access_data:
        return False
    
    ip_data = ip_access_data[ip_address]
    

    if not ip_data.get('is_banned', False):
        return False
    

    ban_time_str = ip_data.get('ban_time')
    if not ban_time_str:
        return True
    
    try:
        ban_time = datetime.fromisoformat(ban_time_str)
        current_time = datetime.now()
        

        if (current_time - ban_time).total_seconds() >= 24 * 3600:

            ip_data['is_banned'] = False
            ip_data['ban_time'] = None
            ip_data['password_fail_times'] = []
            save_ip_data()
            return False
        else:
            return True
    except Exception:
        # 如果日期解析出错，保持封禁状态
        return True

def cleanup_expired_ip_bans():
    """清理过期的IP封禁记录"""
    global ip_access_data, _last_ip_cleanup
    
    current_time = time.time()
    # 每小时清理一次
    if current_time - _last_ip_cleanup < 3600:
        return
    
    _last_ip_cleanup = current_time
    current_datetime = datetime.now()
    
    cleaned_count = 0
    for ip_address in list(ip_access_data.keys()):
        ip_data = ip_access_data[ip_address]
        
        # 清理密码失败记录
        cleanup_old_password_fails(ip_address)
        
        # 检查封禁是否过期
        if ip_data.get('is_banned', False):
            ban_time_str = ip_data.get('ban_time')
            if ban_time_str:
                try:
                    ban_time = datetime.fromisoformat(ban_time_str)
                    if (current_datetime - ban_time).total_seconds() >= 24 * 3600:
                        ip_data['is_banned'] = False
                        ip_data['ban_time'] = None
                        ip_data['password_fail_times'] = []
                        cleaned_count += 1
                except Exception:
                    pass
    
    if cleaned_count > 0:
        save_ip_data()



load_ip_data()


# ===== 统一错误处理系统 =====
def api_error_response(error_msg, status_code=500, **extra_data):
    """标准API错误响应"""
    response_data = {
        'success': False,
        'error': str(error_msg)
    }
    response_data.update(extra_data)
    return jsonify(response_data), status_code

def api_success_response(data=None, **extra_data):
    """标准API成功响应"""
    response_data = {'success': True}
    if data is not None:
        response_data.update(data if isinstance(data, dict) else {'data': data})
    response_data.update(extra_data)
    return jsonify(response_data)

def openapi_error_response(error_msg, status_code=200):
    """OpenAPI错误响应（使用message字段）"""
    return jsonify({
        'success': False,
        'message': str(error_msg)
    }), status_code

def openapi_success_response(data=None, **extra_data):
    """OpenAPI成功响应"""
    response_data = {'success': True}
    if data is not None:
        response_data.update(data if isinstance(data, dict) else {'data': data})
    response_data.update(extra_data)
    return jsonify(response_data)

def check_openapi_login(user_id):
    """检查OpenAPI用户登录状态"""
    if user_id not in openapi_user_data:
        return None
    return openapi_user_data[user_id]

def catch_error(func):
    """捕获错误的装饰器 - 自动返回JSON错误响应"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_msg = f"{func.__name__} 执行出错: {str(e)}"
            print(error_msg)  # 简化日志记录
            
            # 简化的错误记录
            try:
                add_log_to_db('error', {
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'content': error_msg
                })
            except:
                pass
                
            return api_error_response(str(e))
    return wrapper


def generate_session_token():
    """生成安全的会话令牌"""
    return base64.urlsafe_b64encode(uuid.uuid4().bytes).decode('utf-8').rstrip('=')

def sign_cookie_value(value, secret):
    """对cookie值进行签名"""
    signature = hmac.new(
        secret.encode('utf-8'),
        value.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return f"{value}.{signature}"

def verify_cookie_value(signed_value, secret):
    """验证cookie值的签名"""
    try:
        value, signature = signed_value.rsplit('.', 1)
        expected_signature = hmac.new(
            secret.encode('utf-8'),
            value.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected_signature), value
    except:
        return False, None

def require_token(f):
    """要求访问令牌的装饰器"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):

        token = request.args.get('token') or request.form.get('token')
        if not token or token != WEB_SECURITY['access_token']:
            return '', 403
        

        device_info = extract_device_info(request)
        record_ip_access(request.remote_addr, access_type='token_success', device_info=device_info)
        
        return f(*args, **kwargs)
    return decorated_function

def require_auth(f):
    """要求身份验证的装饰器"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        cleanup_expired_sessions()
        cookie_value = request.cookies.get(WEB_SECURITY['cookie_name'])
        if cookie_value:
            is_valid, session_token = verify_cookie_value(cookie_value, WEB_SECURITY['cookie_secret'])
            if is_valid and session_token in valid_sessions:
                session_info = valid_sessions[session_token]

                if datetime.now() < session_info['expires']:

                    if WEB_SECURITY.get('production_mode', False):
                        if (session_info.get('ip') != request.remote_addr or 
                            session_info.get('user_agent', '')[:200] != request.headers.get('User-Agent', '')[:200]):

                            del valid_sessions[session_token]
                        else:
                            return f(*args, **kwargs)
                    else:
                        return f(*args, **kwargs)
                else:
                    del valid_sessions[session_token]
        token = request.args.get('token', '')
        return render_template('login.html', token=token)
    return decorated_function

def require_socketio_token(f):
    """SocketIO事件要求token和cookie双重验证的装饰器"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        # 清理过期的封禁记录和会话
        cleanup_expired_ip_bans()
        cleanup_expired_sessions()
        
        # 检查IP是否被封禁
        client_ip = request.remote_addr
        if is_ip_banned(client_ip):
            return False  # 拒绝连接
        
        # 第一层验证：检查token
        token = request.args.get('token')
        if not token or token != WEB_SECURITY['access_token']:
            return False  # 拒绝连接
        
        # 第二层验证：检查cookie（与web页面保持一致的安全标准）
        cookie_value = request.cookies.get(WEB_SECURITY['cookie_name'])
        if not cookie_value:
            return False  # 没有cookie，拒绝连接
        
        # 验证cookie签名和会话有效性
        is_valid, session_token = verify_cookie_value(cookie_value, WEB_SECURITY['cookie_secret'])
        if not is_valid or session_token not in valid_sessions:
            return False  # cookie无效或会话不存在，拒绝连接
        
        session_info = valid_sessions[session_token]
        
        # 检查会话是否过期
        if datetime.now() >= session_info['expires']:
            # 会话过期，清理并拒绝连接
            del valid_sessions[session_token]
            return False
        
        # 生产环境：验证IP和User-Agent一致性（可选）
        if WEB_SECURITY.get('production_mode', False):
            if (session_info.get('ip') != client_ip or 
                session_info.get('user_agent', '')[:200] != request.headers.get('User-Agent', '')[:200]):
                # IP或User-Agent发生变化，出于安全考虑删除会话并拒绝连接
                del valid_sessions[session_token]
                return False
        
        # 记录访问（token验证成功）
        device_info = extract_device_info(request)
        record_ip_access(client_ip, access_type='token_success', device_info=device_info)
        
        return f(*args, **kwargs)
    return decorated_function

def check_ip_ban(f):
    """检查IP是否被封禁的装饰器"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        # 清理过期的封禁记录
        cleanup_expired_ip_bans()
        
        # 检查IP是否被封禁
        client_ip = request.remote_addr
        if is_ip_banned(client_ip):
            return '', 403
        
        return f(*args, **kwargs)
    return decorated_function

def cleanup_expired_sessions():
    """清理过期的会话（生产环境安全机制）"""
    global valid_sessions, _last_session_cleanup
    
    current_time = time.time()
    # 每5分钟清理一次过期会话
    if current_time - _last_session_cleanup < 300:
        return
    
    _last_session_cleanup = current_time
    current_datetime = datetime.now()
    
    # 找出过期的会话
    expired_sessions = []
    for session_token, session_info in valid_sessions.items():
        if current_datetime >= session_info['expires']:
            expired_sessions.append(session_token)
    
    # 删除过期会话
    for session_token in expired_sessions:
        del valid_sessions[session_token]
    
    if expired_sessions:
        pass

def limit_session_count():
    """限制同时活跃的会话数量（生产环境安全机制）"""
    max_sessions = 10  # 最大同时会话数
    if len(valid_sessions) > max_sessions:
        # 删除最旧的会话
        sorted_sessions = sorted(valid_sessions.items(), 
                               key=lambda x: x[1]['created'])
        while len(valid_sessions) > max_sessions:
            oldest_session = sorted_sessions.pop(0)
            del valid_sessions[oldest_session[0]]

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
        global socketio
        
        # 预先生成时间戳，避免重复调用
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entry = {'timestamp': timestamp, 'content': content}
        
        if traceback_info:
            entry['traceback'] = traceback_info
            
        # 添加到内存队列
        self.logs.append(entry)
        self.global_logs.append(entry)
        
        # 异步数据库写入
        if not skip_db and LOG_DB_CONFIG.get('enabled', False):
            # 使用异步方式写入数据库，避免阻塞
            try:
                add_log_to_db(self.log_type, entry)
            except Exception:
                pass  # 数据库写入失败不应影响主流程
        
        # 异步SocketIO推送
        if socketio is not None:
            try:
                socketio.emit('new_message', {
                    'type': self.log_type,
                    'data': entry
                }, namespace=PREFIX)
            except Exception:
                pass  # SocketIO推送失败不应影响主流程
                
        return entry

# ===== 7. 日志处理器实例 =====
received_handler = LogHandler('received')
plugin_handler = LogHandler('plugin')
framework_handler = LogHandler('framework')
error_handler = LogHandler('error')

# ===== 8. 日志与消息相关API =====
@catch_error
def add_received_message(message):
    # 快速检查是否为被拉进群事件，直接跳过
    if _is_group_add_robot_event(message):
        return None
    
    user_id, group_id, pure_content, formatted_message = _parse_message_info(message)
    
    # 添加显示日志（跳过数据库写入）
    display_entry = received_handler.add(formatted_message, skip_db=True)
    
    # 异步写入数据库
    db_entry = {
        'timestamp': display_entry['timestamp'],
        'content': pure_content,
        'user_id': user_id,
        'group_id': group_id
    }
    try:
        add_log_to_db('received', db_entry)
    except Exception:
        pass  # 数据库写入失败不应影响主流程
        
    return display_entry

def _is_group_add_robot_event(message):
    """快速检查是否为被拉进群事件"""
    if isinstance(message, dict):
        return message.get('t') == 'GROUP_ADD_ROBOT'
    elif isinstance(message, str) and message.startswith('{'):
        try:
            msg_dict = json.loads(message)
            return msg_dict.get('t') == 'GROUP_ADD_ROBOT'
        except:
            return False
    return False

def _parse_message_info(message):
    """解析消息信息，提取用户ID、群组ID和内容"""
    try:
        if isinstance(message, str) and not message.startswith('{'):
            # 纯文本消息
            return "未知用户", "c2c", message, message
        
        # 结构化消息，使用MessageEvent解析
        event = MessageEvent(message)
        user_id = event.user_id or "未知用户"
        group_id = event.group_id or "c2c"
        pure_content = event.content or ""
        
        # 处理交互消息
        if getattr(event, 'event_type', None) == 'INTERACTION_CREATE':
            chat_type = event.get('d/chat_type')
            scene = event.get('d/scene')
            if chat_type == 2 or scene == 'c2c':
                group_id = "c2c"
                user_id = event.get('d/user_openid') or user_id
            button_data = event.get('d/data/resolved/button_data')
            if button_data and not pure_content:
                pure_content = button_data
        
        # 生成格式化消息
        if group_id != "c2c":
            formatted_message = f"{user_id}（{group_id}）：{pure_content}"
        else:
            formatted_message = f"{user_id}：{pure_content}"
            
        return user_id, group_id, pure_content, formatted_message
        
    except Exception as e:
        # 解析失败时的回退处理
        try:
            pure_content = json.dumps(message, ensure_ascii=False)
        except:
            pure_content = str(message)
        add_error_log(f"消息解析失败: {str(e)}")
        return "未知用户", "c2c", pure_content, pure_content

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
@web_panel.route('/login', methods=['POST'])
@check_ip_ban
@require_token
@catch_error
def login():
    """处理密码验证"""
    password = request.form.get('password')
    token = request.form.get('token')
    
    if password == WEB_SECURITY['admin_password']:
        # 生产环境安全检查
        cleanup_expired_sessions()
        limit_session_count()
        
        # 记录密码验证成功
        device_info = extract_device_info(request)
        record_ip_access(request.remote_addr, access_type='password_success', device_info=device_info)
        
        # 密码正确，生成会话
        session_token = generate_session_token()
        expires = datetime.now() + timedelta(days=WEB_SECURITY['cookie_expires_days'])
        
        # 保存会话信息
        valid_sessions[session_token] = {
            'created': datetime.now(),
            'expires': expires,
            'ip': request.remote_addr,
            'user_agent': request.headers.get('User-Agent', '')[:200]  # 记录用户代理（限制长度）
        }
        
        # 创建签名cookie
        signed_token = sign_cookie_value(session_token, WEB_SECURITY['cookie_secret'])
        
        # 重定向到主页面
        response = make_response(f'''
        <script>
            window.location.href = "/web/?token={token}";
        </script>
        ''')
        response.set_cookie(
            WEB_SECURITY['cookie_name'],
            signed_token,
            max_age=WEB_SECURITY['cookie_expires_days'] * 24 * 60 * 60,
            httponly=True,
            secure=False,   # 不使用SSL验证
            samesite='Lax'  # 调整为Lax模式
        )
        return response
    else:
        # 密码错误
        record_ip_access(request.remote_addr, access_type='password_fail')
        return render_template('login.html', token=token, error='密码错误，请重试')



@web_panel.route('/')
@check_ip_ban
@require_token
@require_auth
@catch_error
def index():
    """Web面板首页 - 自动检测设备类型并选择模板"""
    # 获取用户代理字符串
    user_agent = request.headers.get('User-Agent', '').lower()
    
    # 检查是否有手动指定设备类型的参数
    device_type = request.args.get('device', None)
    
    # 自动检测设备类型
    if device_type is None:
        # 检测是否为移动设备
        mobile_keywords = ['android', 'iphone', 'ipad', 'mobile', 'phone', 'tablet']
        is_mobile = any(keyword in user_agent for keyword in mobile_keywords)
        device_type = 'mobile' if is_mobile else 'pc'
    
    # 根据设备类型选择模板
    if device_type == 'mobile':
        template_name = 'mobile.html'
    else:
        template_name = 'index.html'
    
    response = make_response(render_template(template_name, 
                                           prefix=PREFIX, 
                                           device_type=device_type,
                                           ROBOT_QQ=ROBOT_QQ,
                                           appid=appid,
                                           WEBSOCKET_CONFIG=WEBSOCKET_CONFIG))
    
    # 添加生产环境安全头信息
    if WEB_SECURITY.get('secure_headers', True):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'  # 生产环境：完全禁止iframe
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com; font-src 'self' cdn.jsdelivr.net cdnjs.cloudflare.com; img-src 'self' data: *.myqcloud.com thirdqq.qlogo.cn api.2dcode.biz; connect-src 'self' i.elaina.vin"
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        response.headers['Strict-Transport-Security'] = 'max-age=0'
    
    # 添加缓存控制头
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'  # 生产环境：禁用缓存敏感页面
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

@web_panel.route('/api/logs/<log_type>')
@check_ip_ban
@require_token
@require_auth
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
@check_ip_ban
@require_token
@require_auth
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

@web_panel.route('/api/statistics')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_statistics():
    """获取统计数据API"""
    # 检查是否强制刷新
    force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
    
    # 检查是否查询特定日期
    selected_date = request.args.get('date')
    
    if selected_date and selected_date != 'today':
        # 查询特定历史日期的数据
        date_data = get_specific_date_data(selected_date)
        if not date_data:
            return api_error_response(f'未找到日期 {selected_date} 的数据', 404)
        return api_success_response({
            'selected_date_data': date_data,
            'date': selected_date
        })
    else:
        # 查询完整统计数据（包含图表数据）
        data = get_statistics_data(force_refresh=force_refresh)
        return api_success_response(data)

@web_panel.route('/api/complete_dau', methods=['POST'])
@check_ip_ban
@require_token
@catch_error
def complete_dau():
    """补全DAU数据API"""
    result = complete_dau_data()
    return api_success_response(result=result)

@web_panel.route('/api/get_nickname/<user_id>')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_user_nickname(user_id):
    """获取用户昵称API"""
    nickname = fetch_user_nickname(user_id)
    return api_success_response(nickname=nickname, user_id=user_id)

@web_panel.route('/api/available_dates')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_available_dates():
    """获取可用的DAU日期列表API"""
    dates = get_available_dau_dates()
    return api_success_response(dates=dates)

def _build_fallback_robot_info(error_msg, robot_share_url, connection_type, connection_status):
    """构建错误情况下的机器人信息响应"""
    return {
        'success': False,
        'error': error_msg,
        'qq': ROBOT_QQ,
        'name': '加载失败',
        'description': '无法获取机器人信息',
        'avatar': '',
        'appid': appid,
        'developer': '未知',
        'link': robot_share_url,
        'status': '未知',
        'connection_type': connection_type,
        'connection_status': connection_status,
        'data_source': 'fallback',
        'qr_code_api': f'/web/api/robot_qrcode?url={robot_share_url}'
    }

@web_panel.route('/api/robot_info')
@catch_error  
def get_robot_info():
    """获取机器人信息API"""
    # 预先构建通用数据
    robot_share_url = f"https://qun.qq.com/qunpro/robot/qunshare?robot_uin={ROBOT_QQ}"
    is_websocket = WEBSOCKET_CONFIG.get('enabled', False)
    connection_type = 'WebSocket' if is_websocket else 'WebHook'
    connection_status = get_websocket_status() if is_websocket else 'WebHook'
    
    try:
        # 调用腾讯官方API
        api_url = f"https://qun.qq.com/qunng/http2rpc/gotrpc/noauth/trpc.group_pro_robot.manager.TrpcHandler/GetShareInfo?robot_uin={ROBOT_QQ}"
        print(f"正在调用机器人信息API: {api_url}")
        
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        api_response = response.json()
        
        # 检查API返回状态
        if api_response.get('retcode') != 0:
            raise Exception(f"API返回错误: {api_response.get('message', 'Unknown error')}")
        
        # 提取并处理机器人数据
        robot_data = api_response.get('data', {}).get('data', {})
        print(f"获取到机器人数据: {robot_data.get('robot_name', '未知')}")
        
        # 处理头像URL
        avatar_url = robot_data.get('robot_avatar', '')
        if avatar_url and 'myqcloud.com' in avatar_url:
            avatar_url += '&imageMogr2/format/png' if '?' in avatar_url else '?imageMogr2/format/png'
        
        # 返回成功响应
        return jsonify({
            'success': True,
            'qq': robot_data.get('robot_uin', ROBOT_QQ),
            'name': robot_data.get('robot_name', '未知机器人'),
            'description': robot_data.get('robot_desc', '暂无描述'),
            'avatar': avatar_url,
            'appid': robot_data.get('appid', appid),
            'developer': robot_data.get('create_name', '未知'),
            'link': robot_share_url,
            'status': '正常' if robot_data.get('robot_offline', 1) == 0 else '离线',
            'connection_type': connection_type,
            'connection_status': connection_status,
            'data_source': 'api',
            'is_banned': robot_data.get('robot_ban', False),
            'mute_status': robot_data.get('mute_status', 0),
            'commands_count': len(robot_data.get('commands', [])),
            'is_sharable': robot_data.get('is_sharable', False),
            'service_note': robot_data.get('service_note', ''),
            'qr_code_api': f'/web/api/robot_qrcode?url={robot_share_url}'
        })
        
    except Exception as e:
        print(f"获取机器人信息失败: {e}")
        return jsonify(_build_fallback_robot_info(str(e), robot_share_url, connection_type, connection_status)), 500

@web_panel.route('/api/robot_qrcode')
@catch_error
def get_robot_qrcode():
    """生成机器人分享链接的二维码"""
    # 获取URL参数
    url = request.args.get('url')
    if not url:
        return api_error_response('缺少URL参数', 400)
    
    # 调用草料二维码API
    qr_api_url = f"https://api.2dcode.biz/v1/create-qr-code?data={url}"
    print(f"正在生成二维码: {qr_api_url}")
    
    response = requests.get(qr_api_url, timeout=10)
    response.raise_for_status()
    
    # 返回二维码图片数据
    return response.content, 200, {
        'Content-Type': 'image/png',
        'Cache-Control': 'public, max-age=3600'  # 缓存1小时
    }

# ===== 10. 系统信息与插件管理 =====
@catch_error
def get_system_info():
    global _last_gc_time, _last_gc_log_time
    
    try:
        process = psutil.Process(os.getpid())
        current_time = time.time()
        collected = 0
        
        # 有选择性地执行垃圾回收，减少不必要的性能开销
        if current_time - _last_gc_time >= _gc_interval:
            collected = gc.collect(0)  # 只收集第0代对象，减少停顿时间
            _last_gc_time = current_time
        
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
        add_error_log(f"获取系统信息失败: {str(e)}")
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
def process_plugin_module(module, plugin_path, module_name, is_system=False, dir_name=None):
    """处理单个插件模块，提取插件信息"""
    plugin_info_list = []
    plugin_classes_found = False
    
    # 获取文件修改时间
    last_modified_str = ""
    try:
        last_modified = os.path.getmtime(plugin_path)
        last_modified_str = datetime.fromtimestamp(last_modified).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        pass
        
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
                else:
                    name = f"{dir_name}/{module_name}/{attr_name}"
                
                plugin_info = {
                    'name': name,
                    'class_name': attr_name,
                    'status': 'loaded',
                    'error': '',
                    'path': plugin_path,
                    'is_system': is_system,
                    'directory': dir_name,
                    'last_modified': last_modified_str
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
                        
                except Exception as e:
                    plugin_info['status'] = 'error'
                    plugin_info['error'] = f"获取处理器失败: {str(e)}"
                    plugin_info['traceback'] = traceback.format_exc()
                
                # 添加到结果列表
                plugin_info_list.append(plugin_info)
    
    # 如果没有找到插件类，添加一个错误记录
    if not plugin_classes_found:
        name_prefix = "system/" if is_system else ""
        plugin_info = {
            'name': f"{name_prefix}{dir_name}/{module_name}",
            'class_name': 'unknown',
            'status': 'error',
            'error': '未在模块中找到有效的插件类',
            'path': plugin_path,
            'directory': dir_name,
            'last_modified': last_modified_str
        }
            
        plugin_info_list.append(plugin_info)
    
    return plugin_info_list

@catch_error
def load_plugin_module(plugin_file, module_name, is_system=False):
    """加载插件模块并进行错误处理"""
    try:
        # 确定目录名
        dir_name = os.path.basename(os.path.dirname(plugin_file))
        
        # 确定完整模块名称
        full_module_name = f"plugins.{dir_name}.{module_name}"
        
        # 动态导入模块
        spec = importlib.util.spec_from_file_location(full_module_name, plugin_file)
        if not spec or not spec.loader:
            return [{
                'name': f"{dir_name}/{module_name}",
                'class_name': 'unknown',
                'status': 'error',
                'error': '无法加载插件文件',
                'path': plugin_file,
                'directory': dir_name
            }]
            
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        
        # 处理模块中的插件类
        return process_plugin_module(module, plugin_file, module_name, is_system=is_system, dir_name=dir_name)
        
    except Exception as e:
        dir_name = os.path.basename(os.path.dirname(plugin_file))
        plugin_name = f"{dir_name}/{module_name}"
        return [{
            'name': plugin_name,
            'class_name': 'unknown',
            'status': 'error',
            'error': str(e),
            'path': plugin_file,
            'directory': dir_name,
            'traceback': traceback.format_exc()
        }]

@catch_error
def scan_plugins():
    """扫描所有插件并获取其状态"""
    global plugins_info
    plugins_info = []
    
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plugins_dir = os.path.join(script_dir, 'plugins')
    
    # 遍历plugins目录下的所有子目录
    for dir_name in os.listdir(plugins_dir):
        plugin_dir = os.path.join(plugins_dir, dir_name)
        if os.path.isdir(plugin_dir):
            # 获取目录下所有.py文件
            py_files = [f for f in os.listdir(plugin_dir) if f.endswith('.py') and f != '__init__.py']
            
            for py_file in py_files:
                plugin_file = os.path.join(plugin_dir, py_file)
                plugin_name = os.path.splitext(py_file)[0]
                
                # 加载插件（统一处理模式，不区分热加载和冷加载）
                plugin_info_list = load_plugin_module(
                    plugin_file, 
                    plugin_name,
                    is_system=(dir_name == 'system')
                )
                
                plugins_info.extend(plugin_info_list)
    
    # 按状态排序：已加载的排在前面
    plugins_info.sort(key=lambda x: (0 if x['status'] == 'loaded' else 1))
    return plugins_info

@catch_error
def register_socketio_handlers(sio):
    """注册Socket.IO事件处理函数"""
    @sio.on('connect', namespace=PREFIX)
    @require_socketio_token
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
    @require_socketio_token
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
    @require_socketio_token
    def handle_refresh_plugins():
        """处理刷新插件信息的请求"""
        # 获取当前请求的会话ID
        sid = request.sid
        
        # 在后台线程中执行插件扫描
        def async_scan_plugins():
            try:
                plugins = scan_plugins()
                # 尝试发送数据，不做复杂的连接检查
                try:
                    sio.emit('plugins_update', plugins, room=sid, namespace=PREFIX)
                except Exception:
                    # 如果直接发送失败，可能是连接已断开，静默处理
                    pass
            except Exception:
                # 静默处理异常，避免中断
                pass
                
        # 启动后台线程
        threading.Thread(target=async_scan_plugins, daemon=True).start()

    @sio.on('request_logs', namespace=PREFIX)
    @require_socketio_token  
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

# ===== 10.5. 统计数据处理函数 =====
def get_statistics_data(force_refresh=False):
    """获取统计数据，包含历史数据和今日实时数据"""
    global statistics_cache, statistics_cache_time
    
    current_time = time.time()
    current_date = datetime.now().date()
    
    # 检查缓存是否有效：
    # 1. 不是强制刷新
    # 2. 缓存存在且未过期
    # 3. 缓存的日期是今天（避免跨日期问题）
    cache_valid = (not force_refresh and 
                  statistics_cache is not None and 
                  current_time - statistics_cache_time < STATISTICS_CACHE_DURATION)
    
    # 额外检查：如果缓存存在，验证缓存的日期是否为今天
    if cache_valid and statistics_cache:
        cache_date_str = statistics_cache.get('cache_date')
        if cache_date_str:
            try:
                cache_date = datetime.strptime(cache_date_str, '%Y-%m-%d').date()
                if cache_date != current_date:
                    cache_valid = False  # 缓存日期不是今天，失效
            except Exception as e:
                cache_valid = False  # 日期解析失败，失效
        else:
            cache_valid = False  # 没有缓存日期，失效
    
    if cache_valid:
        return statistics_cache
    
    try:
        # 获取历史DAU数据（30天）
        historical_data = load_historical_dau_data()
        
        # 获取今日实时数据
        today_data = get_today_dau_data(force_refresh)
        
        # 构建返回数据
        result = {
            'historical': historical_data,
            'today': today_data,
            'cache_time': current_time,
            'cache_date': current_date.strftime('%Y-%m-%d')  # 添加缓存日期
        }
        
        # 更新缓存
        statistics_cache = result
        statistics_cache_time = current_time
        
        return result
        
    except Exception as e:
        add_error_log(f"获取统计数据失败: {str(e)}")
        return {
            'historical': [],
            'today': {},
            'cache_time': current_time,
            'error': str(e)
        }

def load_historical_dau_data():
    """加载历史DAU数据（最近30天，不包括今天）"""
    try:
        # 尝试导入DAU分析模块
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            # 如果导入失败，尝试添加路径后导入
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        dau_analytics = get_dau_analytics()
        historical_data = []
        
        today = datetime.now()
        
        # 获取最近30天的数据（不包括今天）
        for i in range(1, 31):
            target_date = today - timedelta(days=i)
            
            try:
                dau_data = dau_analytics.load_dau_data(target_date)
                
                if dau_data:
                    # 格式化日期为MM-DD格式用于显示
                    display_date = target_date.strftime('%m-%d')
                    dau_data['display_date'] = display_date
                    historical_data.append(dau_data)
            except Exception:
                continue
        
        # 按日期排序（从旧到新）
        historical_data.sort(key=lambda x: x.get('date', ''))
        
        return historical_data
        
    except Exception as e:
        add_error_log(f"加载历史DAU数据失败: {str(e)}")
        return []

def get_today_dau_data(force_refresh=False):
    """获取今日实时DAU数据"""
    try:
        # 尝试导入相关模块
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            # 如果导入失败，尝试添加路径后导入
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        dau_analytics = get_dau_analytics()
        today = datetime.now()
        
        # 如果不是强制刷新，尝试先从文件加载今日数据
        today_data = None
        if not force_refresh:
            today_data = dau_analytics.load_dau_data(today)
        
        # 如果文件中没有今日数据或者是强制刷新，则实时计算
        if not today_data:
            try:
                today_data = dau_analytics.collect_dau_data(today)
                
                # 如果实时计算成功，添加标记表示这是实时数据
                if today_data:
                    today_data['is_realtime'] = True
                    today_data['cache_time'] = time.time()
            except Exception as e:
                # 返回空数据结构，避免前端报错
                today_data = {
                    'message_stats': {
                        'total_messages': 0,
                        'active_users': 0,
                        'active_groups': 0,
                        'private_messages': 0,
                        'peak_hour': 0,
                        'peak_hour_count': 0,
                        'top_groups': [],
                        'top_users': []
                    },
                    'user_stats': {
                        'total_users': 0,
                        'total_groups': 0,
                        'total_friends': 0
                    },
                    'command_stats': [],
                    'error': str(e)
                }
        
        return today_data or {}
        
    except Exception as e:
        add_error_log(f"获取今日DAU数据失败: {str(e)}")
        return {
            'message_stats': {},
            'user_stats': {},
            'command_stats': [],
            'error': str(e)
        }

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
                            async_mode='eventlet',  # 使用eventlet作为异步模式，与gunicorn兼容
                            logger=False,
                            engineio_logger=False)
            # 关键：注册Socket.IO处理函数
            register_socketio_handlers(socketio)
        except Exception as e:
            error_tb = traceback.format_exc()
            add_log_to_db('error', {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'content': f"Socket.IO初始化错误: {str(e)}",
                'traceback': error_tb
            })

        return app, socketio
    else:
        # 检查blueprint是否已经注册，避免重复注册
        if not any(bp.name == 'web_panel' for bp in main_app.blueprints.values()):
            main_app.register_blueprint(web_panel, url_prefix=PREFIX)
        else:
            pass
        
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
                                async_mode='eventlet',  # 使用eventlet作为异步模式，与gunicorn兼容
                                logger=False,
                                engineio_logger=False)
                main_app.socketio = socketio
            # 关键：注册Socket.IO处理函数
            register_socketio_handlers(socketio)
        except Exception as e:
            error_tb = traceback.format_exc()
            add_log_to_db('error', {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'content': f"Socket.IO初始化错误: {str(e)}",
                'traceback': error_tb
            })

        return None

def complete_dau_data():
    """补全DAU数据的具体实现"""
    import os
    import sys
    
    try:
        # 尝试导入dau_analytics
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            # 如果导入失败，尝试添加路径后导入
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        dau_analytics = get_dau_analytics()
        today = datetime.now()
        
        # 检查30天内的DAU数据（除了今天）
        missing_dates = []
        
        for i in range(1, 31):  # 从昨天开始，检查30天
            target_date = today - timedelta(days=i)
            
            # 检查是否存在DAU数据文件
            try:
                dau_data = dau_analytics.load_dau_data(target_date)
                if not dau_data:
                    missing_dates.append(target_date)
            except Exception as e:
                missing_dates.append(target_date)
        
        if not missing_dates:
            return {
                'generated_count': 0,
                'failed_count': 0,
                'total_missing': 0,
                'generated_dates': [],
                'failed_dates': [],
                'message': '近30天DAU数据完整，无需补全'
            }
        
        # 开始生成缺失的DAU数据
        generated_count = 0
        failed_count = 0
        generated_dates = []
        failed_dates = []
        
        for target_date in missing_dates:
            try:
                success = dau_analytics.manual_generate_dau(target_date)
                if success:
                    generated_count += 1
                    generated_dates.append(target_date.strftime('%Y-%m-%d'))
                else:
                    failed_count += 1
                    failed_dates.append(target_date.strftime('%Y-%m-%d'))
            except Exception as e:
                failed_count += 1
                failed_dates.append(target_date.strftime('%Y-%m-%d'))
        
        return {
            'generated_count': generated_count,
            'failed_count': failed_count,
            'total_missing': len(missing_dates),
            'generated_dates': generated_dates,
            'failed_dates': failed_dates,
            'message': f'检测到{len(missing_dates)}天的DAU数据缺失，成功生成{generated_count}天，失败{failed_count}天'
        }
        
    except Exception as e:
        raise Exception(f"补全DAU数据失败: {str(e)}")

def fetch_user_nickname(user_id):
    """获取用户昵称"""
    import requests
    
    try:
        # 参数验证
        if not user_id or len(user_id) < 3:
            return None
            
        # 从配置文件获取appid
        from config import appid
        
        # 构建API URL
        api_url = f"https://i.elaina.vin/api/bot/xx.php?openid={user_id}&appid={appid}"
        
        # 发送请求
        response = requests.get(api_url, timeout=3)
        
        if response.status_code == 200:
            data = response.json()
            nickname = data.get('名字', '').strip()
            
            # 验证昵称有效性
            if nickname and nickname != user_id and len(nickname) > 0 and len(nickname) <= 20:
                return nickname
            else:
                return None  # 没有获取到有效昵称
        else:
                    return None
        
    except Exception as e:
        return None

# 沙盒测试路由
@web_panel.route('/api/sandbox/test', methods=['POST'])
@require_auth
@catch_error
def sandbox_test():
    """沙盒测试插件功能"""
    data = request.get_json()
    if not data:
        return api_error_response('缺少请求数据', 400)
    
    # 获取测试参数
    message_content = data.get('message', '').strip()
    group_id = data.get('group_id', '').strip()
    user_id = data.get('user_id', '').strip()
    
    if not message_content:
        return api_error_response('消息内容不能为空', 400)
    if not user_id:
        return api_error_response('用户ID不能为空', 400)
    
    # 群组ID可以为空（私聊模式）
    
    # 根据是否有群组ID决定消息类型
    is_private = not group_id  # 群组ID为空则为私聊
    message_type = "C2C_MESSAGE_CREATE" if is_private else "GROUP_AT_MESSAGE_CREATE"
    
    # 构造模拟的MessageEvent数据
    mock_data = {
        "s": 1,
        "op": 0,
        "t": message_type,
        "d": {
            "id": f"sandbox_test_{int(time.time())}",
            "content": message_content,
            "timestamp": datetime.now().isoformat(),
            "author": {
                "id": user_id,
                "username": f"测试用户{user_id}",
                "avatar": "",
                "bot": False
            },
            "attachments": [],
            "embeds": [],
            "mentions": [],
            "mention_roles": [],
            "pinned": False,
            "mention_everyone": False,
            "tts": False,
            "edited_timestamp": None,
            "flags": 0,
            "referenced_message": None,
            "interaction": None,
            "thread": None,
            "components": [],
            "sticker_items": [],
            "position": None
        }
    }
    
    # 根据消息类型添加特定字段
    if is_private:
        # 私聊消息不需要群组相关字段
        pass
    else:
        # 群聊消息需要群组相关字段
        mock_data["d"]["channel_id"] = group_id
        mock_data["d"]["guild_id"] = group_id
        mock_data["d"]["group_id"] = group_id
        mock_data["d"]["member"] = {
            "user": {
                "id": user_id,
                "username": f"测试用户{user_id}",
                "avatar": "",
                "bot": False
            },
            "nick": f"测试用户{user_id}",
            "roles": [],
            "joined_at": datetime.now().isoformat()
        }
    
    # 创建MessageEvent实例
    try:
        event = MessageEvent(mock_data)
        
        # 收集回复内容
        replies = []
        original_reply = event.reply
        
        def mock_reply(content, buttons=None, media=None, *args, **kwargs):
            reply_data = {
                'type': 'reply',
                'content': str(content) if content else '',
                'buttons': buttons,
                'media': media
            }
            replies.append(reply_data)
            return reply_data
        
        # 替换回复方法
        event.reply = mock_reply
        
        # 获取插件管理器并处理消息
        try:
            # 导入插件管理器
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            
            from core.plugin.PluginManager import PluginManager
            
            # 处理消息事件（使用类方法）
            PluginManager.dispatch_message(event)
            
            # 恢复原始方法
            event.reply = original_reply
            
            return api_success_response({
                'replies': replies,
                'message_info': {
                    'content': message_content,
                    'group_id': group_id or '(私聊)',
                    'user_id': user_id,
                    'message_type': '私聊消息' if is_private else '群聊消息',
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            })
            
        except Exception as plugin_error:
            return api_error_response(f'插件处理错误: {str(plugin_error)}')
            
    except Exception as event_error:
        return api_error_response(f'MessageEvent创建错误: {str(event_error)}')

def get_available_dau_dates():
    """获取可用的DAU日期列表（近30天）"""
    import os
    import glob
    from datetime import datetime, timedelta
    
    try:
        # 获取项目根目录下的data/dau文件夹
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dau_folder = os.path.join(project_root, 'data', 'dau')
        
        if not os.path.exists(dau_folder):
            return []
        
        # 获取所有.json文件
        json_files = glob.glob(os.path.join(dau_folder, '*.json'))
        
        # 提取日期并过滤近30天
        available_dates = []
        today = datetime.now().date()
        thirty_days_ago = today - timedelta(days=30)
        
        for file_path in json_files:
            filename = os.path.basename(file_path)
            if filename.endswith('.json'):
                date_str = filename[:-5]  # 移除.json后缀
                try:
                    # 尝试解析日期 (YYYY-MM-DD格式)
                    file_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    
                    # 只包含近30天的日期
                    if file_date >= thirty_days_ago and file_date <= today:
                        # 判断是否为今日
                        is_today = file_date == today
                        display_name = "今日数据" if is_today else f"{file_date.strftime('%m-%d')} ({file_date.strftime('%Y-%m-%d')})"
                        
                        available_dates.append({
                            'value': 'today' if is_today else date_str,
                            'date': date_str,
                            'display': display_name,
                            'is_today': is_today
                        })
                except ValueError:
                    # 如果文件名不是有效日期格式，跳过
                    continue
        
        # 如果今日没有DAU文件，添加今日选项（实时计算）
        today_exists = any(item['is_today'] for item in available_dates)
        if not today_exists:
            available_dates.append({
                'value': 'today',
                'date': today.strftime('%Y-%m-%d'),
                'display': '今日数据 (实时)',
                'is_today': True
            })
        
        # 按日期排序（今日在最前，然后按日期倒序）
        available_dates.sort(key=lambda x: (not x['is_today'], -int(x['date'].replace('-', ''))))
        
        return available_dates
        
    except Exception as e:
        # 返回默认的今日选项
        return [{
            'value': 'today',
            'date': datetime.now().strftime('%Y-%m-%d'),
            'display': '今日数据',
                         'is_today': True
         }]

def get_specific_date_data(date_str):
    """获取特定日期的DAU数据"""
    import os
    from datetime import datetime
    
    try:
        # 尝试导入dau_analytics
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            # 如果导入失败，尝试添加路径后导入
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        # 解析日期
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        
        # 获取DAU数据
        dau_analytics = get_dau_analytics()
        dau_data = dau_analytics.load_dau_data(target_date)
        
        if not dau_data:
            return None
        
        # 格式化数据以匹配前端期望的格式
        message_stats = dau_data.get('message_stats', {})
        user_stats = dau_data.get('user_stats', {})
        command_stats = dau_data.get('command_stats', [])
        
        return {
            'message_stats': message_stats,
            'user_stats': user_stats,
            'command_stats': command_stats,
            'date': date_str,
            'generated_at': dau_data.get('generated_at', ''),
            'is_historical': True
        }
        
    except Exception as e:
        return None

# 在文件末尾添加开放平台相关的代码

# 开放平台相关的全局变量
openapi_user_data = {}
openapi_login_tasks = {}

# 开放平台数据文件路径
OPENAPI_DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'openapi.json')

# 开放平台相关的常量
OPENAPI_BASE = 'https://i.elaina.vin/api/bot'
OPENAPI_LOGIN_URL = f"{OPENAPI_BASE}/get_login.php"
OPENAPI_GET_LOGIN = f"{OPENAPI_BASE}/robot.php"
OPENAPI_MESSAGE = f"{OPENAPI_BASE}/message.php"
OPENAPI_BOTLIST = f"{OPENAPI_BASE}/bot_list.php"
OPENAPI_BOTDATA = f"{OPENAPI_BASE}/bot_data.php"
OPENAPI_MSGTPL = f"{OPENAPI_BASE}/md.php"
OPENAPI_MSGTPL_DETAIL = f"{OPENAPI_BASE}/md_detail.php"

def create_openapi_session():
    """创建开放平台专用的requests session"""
    import warnings
    warnings.filterwarnings('ignore', message='Unverified HTTPS request')
    return requests.Session()

def save_openapi_data():
    """保存开放平台数据到文件"""
    try:
        # 确保data目录存在
        os.makedirs(os.path.dirname(OPENAPI_DATA_FILE), exist_ok=True)
        
        # 保存数据
        with open(OPENAPI_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(openapi_user_data, f, indent=2, ensure_ascii=False)
        
        print(f"开放平台数据已保存到: {OPENAPI_DATA_FILE}")
    except Exception as e:
        print(f"保存开放平台数据失败: {e}")

def load_openapi_data():
    """从文件加载开放平台数据"""
    global openapi_user_data
    try:
        if os.path.exists(OPENAPI_DATA_FILE):
            with open(OPENAPI_DATA_FILE, 'r', encoding='utf-8') as f:
                openapi_user_data = json.load(f)
            print(f"开放平台数据已从文件加载: {len(openapi_user_data)} 个用户")
        else:
            print("开放平台数据文件不存在，使用空数据")
            openapi_user_data = {}
    except Exception as e:
        print(f"加载开放平台数据失败: {e}")
        openapi_user_data = {}

def verify_openapi_login(user_data):
    """验证开放平台登录状态是否有效"""
    try:
        if not user_data or user_data.get('type') != 'ok':
            return False
        
        session = create_openapi_session()
        # 尝试获取机器人列表来验证登录状态
        url = f"{OPENAPI_BOTLIST}?uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}"
        response = session.get(url, verify=False, timeout=10)
        res = response.json()
        
        # 如果能正常获取数据，说明登录有效
        return res.get('code') == 0
    except Exception as e:
        print(f"验证开放平台登录状态失败: {e}")
        return False

def get_app_type_name(app_type):
    """获取应用类型名称"""
    if app_type == '0' or app_type == 0:
        return '小程序'
    elif app_type == '1' or app_type == 1:
        return 'QQ小程序' 
    elif app_type == '2' or app_type == 2:
        return 'QQ机器人'
    else:
        return '未知类型'

@web_panel.route('/openapi/start_login', methods=['POST'])
@require_auth
def openapi_start_login():
    """开始开放平台登录流程"""
    try:
        data = request.get_json()
        user_id = data.get('user_id', 'web_user')
        
        current_time = time.time()
        
        # 开始登录流程
        session = create_openapi_session()
        response = session.get(OPENAPI_LOGIN_URL, verify=False)
        login_data = response.json()
        
        url = login_data.get('url')
        qr = login_data.get('qr')
        
        if not url or not qr:
            return jsonify({
                'success': False,
                'message': '获取登录二维码失败，请稍后重试'
            })
        
        # 记录登录任务
        openapi_login_tasks[user_id] = (time.time(), {'qr': qr, 'session': session})
        
        return jsonify({
            'success': True,
            'login_url': url,
            'qr_code': qr,
            'message': '请扫描二维码登录'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'启动登录失败: {str(e)}'
        })

@web_panel.route('/openapi/check_login', methods=['POST'])
@require_auth
def openapi_check_login():
    """检查开放平台登录状态"""
    try:
        data = request.get_json()
        user_id = data.get('user_id', 'web_user')
        
        if user_id not in openapi_login_tasks:
            return jsonify({
                'success': False,
                'status': 'not_started',
                'message': '未找到登录任务'
            })
        
        task_data = openapi_login_tasks[user_id][1]
        qr = task_data['qr']
        session = task_data['session']
        
        # 检查登录状态
        response = session.get(f"{OPENAPI_GET_LOGIN}?qrcode={qr}", verify=False)
        res = response.json()
        
        if res.get('code') == 0:
            # 登录成功
            login_data = res.get('data', {}).get('data', {})
            openapi_user_data[user_id] = {'type': 'ok', **login_data}
            
            # 清理登录任务
            if user_id in openapi_login_tasks:
                del openapi_login_tasks[user_id]
            
            app_type = login_data.get('appType')
            app_type_str = get_app_type_name(app_type)
            
            # 保存到文件
            save_openapi_data()
            
            return jsonify({
                'success': True,
                'status': 'logged_in',
                'data': {
                    'uin': login_data.get('uin'),
                    'appId': login_data.get('appId'),
                    'appType': app_type_str,
                    'developerId': login_data.get('developerId')
                },
                'message': '登录成功'
            })
        else:
            # 还在等待登录
            return jsonify({
                'success': True,
                'status': 'waiting',
                'message': '等待扫码登录'
            })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'status': 'error',
            'message': f'检查登录状态失败: {str(e)}'
        })

@web_panel.route('/openapi/get_botlist', methods=['POST'])
@require_auth
def openapi_get_botlist():
    """获取机器人列表"""
    try:
        data = request.get_json()
        user_id = data.get('user_id', 'web_user')
        
        if user_id not in openapi_user_data:
            return jsonify({
                'success': False,
                'message': '未登录，请先登录开放平台'
            })
        
        user_data = openapi_user_data[user_id]
        session = create_openapi_session()
        
        url = f"{OPENAPI_BOTLIST}?uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}"
        response = session.get(url, verify=False)
        res = response.json()
        
        if res.get('code') != 0:
            return jsonify({
                'success': False,
                'message': '登录状态失效，请重新登录'
            })
        
        apps = res.get('data', {}).get('apps', [])
        
        return jsonify({
            'success': True,
            'data': {
                'uin': user_data.get('uin'),
                'apps': apps
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取机器人列表失败: {str(e)}'
        })

@web_panel.route('/openapi/get_botdata', methods=['POST'])
@require_auth  
@catch_error
def openapi_get_botdata():
    """获取机器人30天数据"""
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    target_appid = data.get('appid')
    days = data.get('days', 30)
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    session = create_openapi_session()
    
    # 如果指定了appid，则使用指定的appid，否则使用当前登录的appid
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    base_url = f"{OPENAPI_BOTDATA}?appid={appid_to_use}&uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}"
    
    # 同步获取三种数据
    response1 = session.get(f"{base_url}&type=1", verify=False)
    data1_json = response1.json()
    
    response2 = session.get(f"{base_url}&type=2", verify=False)
    data2_json = response2.json()
    
    response3 = session.get(f"{base_url}&type=3", verify=False)
    data3_json = response3.json()
    
    if any(x.get('retcode', -1) != 0 for x in [data1_json, data2_json, data3_json]):
        return openapi_error_response('登录状态失效，请重新登录')
    
    msg_data = data1_json.get('data', {}).get('msg_data', [])
    group_data = data2_json.get('data', {}).get('group_data', [])
    friend_data = data3_json.get('data', {}).get('friend_data', [])
    
    # 处理数据，最多返回指定天数的数据
    max_days = min(len(msg_data), len(group_data), len(friend_data))
    actual_days = min(days, max_days)
    
    processed_data = []
    total_up_msg_people = 0
    
    for i in range(actual_days):
        msg_item = msg_data[i] if i < len(msg_data) else {}
        group_item = group_data[i] if i < len(group_data) else {}
        friend_item = friend_data[i] if i < len(friend_data) else {}
        
        day_data = {
            "date": msg_item.get('报告日期', '0'),
            "up_messages": msg_item.get('上行消息量', '0'),
            "up_users": msg_item.get('上行消息人数', '0'),
            "down_messages": msg_item.get('下行消息量', '0'),
            "total_messages": msg_item.get('总消息量', '0'),
            "current_groups": group_item.get('现有群组', '0'),
            "used_groups": group_item.get('已使用群组', '0'),
            "new_groups": group_item.get('新增群组', '0'),
            "removed_groups": group_item.get('移除群组', '0'),
            "current_friends": friend_item.get('现有好友数', '0'),
            "used_friends": friend_item.get('已使用好友数', '0'),
            "new_friends": friend_item.get('新增好友数', '0'),
            "removed_friends": friend_item.get('移除好友数', '0')
        }
        processed_data.append(day_data)
        total_up_msg_people += int(day_data['up_users'])
    
    # 计算平均DAU
    avg_dau = round(total_up_msg_people / 30, 2) if len(msg_data) > 0 else 0
    
    return openapi_success_response({
        'uin': user_data.get('uin'),
        'appid': appid_to_use,
        'avg_dau': avg_dau,
        'days_data': processed_data
    })

@web_panel.route('/openapi/get_notifications', methods=['POST'])
@require_auth
@catch_error
def openapi_get_notifications():
    """获取机器人通知"""
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    session = create_openapi_session()
    
    url = f"{OPENAPI_MESSAGE}?uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}"
    response = session.get(url, verify=False)
    res = response.json()
    
    if res.get('code') != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    
    messages = res.get('messages', [])
    
    # 处理消息，最多返回前20条
    processed_messages = []
    for msg in messages[:20]:
        processed_messages.append({
            'content': msg.get('content', ''),
            'send_time': msg.get('send_time', ''),
            'type': msg.get('type', ''),
            'title': msg.get('title', '')
        })
    
    return openapi_success_response({
        'uin': user_data.get('uin'),
        'appid': user_data.get('appId'),
        'messages': processed_messages
    })

@web_panel.route('/openapi/logout', methods=['POST'])
@require_auth
@catch_error  
def openapi_logout():
    """用户登出"""
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    
    if user_id in openapi_user_data:
        del openapi_user_data[user_id]
        print(f"用户 {user_id} 已登出开放平台")
    
    save_openapi_data()
    return openapi_success_response(message='登出成功')

@web_panel.route('/openapi/get_login_status', methods=['POST'])
@require_auth
@catch_error
def openapi_get_login_status():
    """获取登录状态"""
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_success_response(logged_in=False)
    
    return openapi_success_response(
        logged_in=True,
        uin=user_data.get('uin'),
        appid=user_data.get('appId')
    )

# 定期清理过期的登录任务
def cleanup_openapi_tasks():
    """清理过期的开放平台登录任务"""
    current_time = time.time()
    expired_users = []
    
    for user_id, (start_time, _) in openapi_login_tasks.items():
        if current_time - start_time > 300:  # 5分钟超时
            expired_users.append(user_id)
    
    for user_id in expired_users:
        del openapi_login_tasks[user_id]

# 在现有的定时任务中添加清理函数
import threading
import time as time_module

def start_openapi_cleanup_thread():
    """启动开放平台清理线程"""
    def cleanup_loop():
        while True:
            try:
                cleanup_openapi_tasks()
                time_module.sleep(60)  # 每分钟清理一次
            except Exception as e:
                print(f"开放平台清理任务出错: {e}")
                time_module.sleep(60)
    
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()

# 启动清理线程
start_openapi_cleanup_thread()

@web_panel.route('/openapi/import_templates', methods=['POST'])
@require_auth
@catch_error
def openapi_import_templates():
    """导入模板到markdown_templates.py文件"""
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    target_appid = data.get('appid')
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    session = create_openapi_session()
    
    # 获取markdown模板和按钮模板
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    # 获取markdown模板
    url = f"{OPENAPI_MSGTPL}?uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}&appid={appid_to_use}"
    response = session.get(url, verify=False)
    res = response.json()
    
    if res.get('retcode') != 0 and res.get('code') != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    
    raw_templates = res.get('data', {}).get('list', [])
    
    # 处理模板数据，统一字段格式
    templates = []
    for template in raw_templates:
        processed_template = {
            'id': template.get('模板id', ''),
            'name': template.get('模板名称', '未命名'),
            'type': template.get('模板类型', '未知类型'),
            'status': template.get('模板状态', '未知状态'),
            'content': template.get('模板内容', ''),
            'create_time': template.get('创建时间', ''),
            'update_time': template.get('更新时间', ''),
            'raw_data': template
        }
        templates.append(processed_template)
    
    # 获取按钮模板 (从现有数据中筛选)
    button_templates = [t for t in templates if t.get('type') == '按钮模板']
    markdown_templates = [t for t in templates if t.get('type') == 'markdown模板']
    
    try:
        # 写入模板到文件
        import_result = _write_templates_to_file(markdown_templates, button_templates)
        
        return jsonify({
            'success': True,
            'data': {
                'imported_count': import_result['imported_count'],
                'skipped_count': import_result['skipped_count'],
                'button_count': import_result['button_count'],
                'message': import_result['message']
            }
        })
    except Exception as e:
        return openapi_error_response(f'导入模板失败: {str(e)}')

def _write_templates_to_file(templates, button_templates):
    """将模板写入markdown_templates.py文件"""
    import os
    import re
    from core.event.markdown_templates import MARKDOWN_TEMPLATES
    
    template_file_path = os.path.join(os.getcwd(), 'core', 'event', 'markdown_templates.py')
    
    # 读取现有文件内容
    with open(template_file_path, 'r', encoding='utf-8') as f:
        current_content = f.read()
    
    # 获取现有模板ID列表
    existing_ids = set()
    for template_config in MARKDOWN_TEMPLATES.values():
        existing_ids.add(template_config['id'])
    
    # 处理新模板
    new_templates = []
    skipped_count = 0
    
    for template in templates:
        template_id = template.get('id', '')
        template_name = template.get('name', '未命名')
        template_content = template.get('content', '')
        
        if template_id in existing_ids:
            skipped_count += 1
            continue
            
        # 解析模板参数
        params = _extract_template_params(template_content)
        
        new_templates.append({
            'id': template_id,
            'name': template_name,
            'content': template_content,
            'params': params,
            'raw_data': template
        })
    
    # 从1开始重新命名所有模板
    existing_template_names = set(MARKDOWN_TEMPLATES.keys())
    template_counter = 1
    
    # 构建新的模板字典内容
    new_template_entries = []
    
    for template in new_templates:
        # 生成模板名称 - 从1开始
        while str(template_counter) in existing_template_names:
            template_counter += 1
        
        template_name = str(template_counter)
        existing_template_names.add(template_name)
        
        # 构建简化的模板条目
        # 转义模板内容中的换行符和特殊字符
        escaped_content = template['content'].replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
        
        template_entry = f'''    "{template_name}": {{
        "id": "{template['id']}",
        "params": {template['params']}
    }},
    # 原始模板内容: {escaped_content}
'''
        new_template_entries.append(template_entry)
        template_counter += 1
    
    # 处理按钮模板
    button_entries = []
    for button in button_templates:
        button_id = button.get('id', '')
        button_name = button.get('name', '未命名按钮')
        
        button_entry = f'''    # 按钮ID: {button_id} - {button_name}
'''
        button_entries.append(button_entry)
    
    # 如果有新模板或按钮，更新文件
    if new_template_entries or button_entries:
        # 找到MARKDOWN_TEMPLATES字典的结束位置
        pattern = r'(MARKDOWN_TEMPLATES\s*=\s*\{.*?)(\n\})'
        match = re.search(pattern, current_content, re.DOTALL)
        
        if match:
            # 在字典结束前插入新模板
            before_end = match.group(1)
            
            # 构建新内容
            new_content = before_end
            
            # 添加新模板
            if new_template_entries:
                new_content += '\n'
                for entry in new_template_entries:
                    new_content += entry
                    
            # 添加按钮注释
            if button_entries:
                new_content += '\n    # 按钮模板ID\n'
                for entry in button_entries:
                    new_content += entry
                    
            new_content += match.group(2)  # 添加结束的 }
            
            # 替换文件内容
            updated_file_content = current_content.replace(match.group(0), new_content)
            
            # 写入文件
            with open(template_file_path, 'w', encoding='utf-8') as f:
                f.write(updated_file_content)
        else:
            # 如果没有找到字典结构，说明文件格式有问题
            raise Exception("无法找到MARKDOWN_TEMPLATES字典结构")
    
    return {
        'imported_count': len(new_templates),
        'skipped_count': skipped_count,
        'button_count': len(button_templates),
        'message': f'成功导入{len(new_templates)}个模板，跳过{skipped_count}个已存在模板'
    }

def _extract_template_params(template_content):
    """从模板内容中提取参数列表"""
    import re
    
    # 匹配 {{.参数名}} 格式的参数
    param_pattern = r'\{\{\.(\w+)\}\}'
    matches = re.findall(param_pattern, template_content)
    
    # 去重并保持顺序
    params = []
    seen = set()
    for param in matches:
        if param not in seen:
            params.append(param)
            seen.add(param)
    
    return params

@web_panel.route('/openapi/verify_saved_login', methods=['POST'])
@require_auth
def openapi_verify_saved_login():
    """验证保存的登录状态"""
    try:
        data = request.get_json()
        user_id = data.get('user_id', 'web_user')
        
        if user_id not in openapi_user_data:
            return jsonify({
                'success': True,
                'valid': False,
                'message': '没有保存的登录信息'
            })
        
        user_data = openapi_user_data[user_id]
        
        # 验证登录状态是否有效
        if verify_openapi_login(user_data):
            app_type = user_data.get('appType')
            app_type_str = get_app_type_name(app_type)
            
            return jsonify({
                'success': True,
                'valid': True,
                'data': {
                    'uin': user_data.get('uin'),
                    'appId': user_data.get('appId'),
                    'appType': app_type_str,
                    'developerId': user_data.get('developerId')
                },
                'message': '登录状态有效'
            })
        else:
            # 登录已失效，清除保存的数据
            if user_id in openapi_user_data:
                del openapi_user_data[user_id]
                save_openapi_data()
            
            return jsonify({
                'success': True,
                'valid': False,
                'message': '登录状态已失效'
            })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'valid': False,
            'message': f'验证登录状态失败: {str(e)}'
        })

# 启动时加载保存的开放平台数据
load_openapi_data()

# 启动清理线程
start_openapi_cleanup_thread()

# 在开放平台logout路由之后添加bot模板相关的API

@web_panel.route('/openapi/get_templates', methods=['POST'])
@require_auth
@catch_error
def openapi_get_templates():
    """获取机器人模板列表"""
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    target_appid = data.get('appid')
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    session = create_openapi_session()
    
    # 如果指定了appid，则使用指定的appid，否则使用当前登录的appid
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    url = f"{OPENAPI_MSGTPL}?uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}&appid={appid_to_use}"
    response = session.get(url, verify=False)
    res = response.json()
    
    if res.get('retcode') != 0 and res.get('code') != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    
    templates = res.get('data', {}).get('list', [])
    
    # 处理模板数据
    processed_templates = []
    for template in templates:
        processed_templates.append({
            'id': template.get('模板id', ''),
            'name': template.get('模板名称', '未命名'),
            'type': template.get('模板类型', '未知类型'),
            'status': template.get('模板状态', '未知状态'),
            'content': template.get('模板内容', ''),
            'create_time': template.get('创建时间', ''),
            'update_time': template.get('更新时间', ''),
            'raw_data': template  # 保留原始数据
        })
    
    return jsonify({
        'success': True,
        'data': {
            'uin': user_data.get('uin'),
            'appid': appid_to_use,
            'templates': processed_templates,
            'total': len(processed_templates)
        }
    })

@web_panel.route('/openapi/get_template_detail', methods=['POST'])
@require_auth
@catch_error
def openapi_get_template_detail():
    """获取模板详情"""
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    template_id = data.get('id')
    target_appid = data.get('appid')
    
    if not template_id:
        return openapi_error_response('缺少模板ID参数')
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    session = create_openapi_session()
    
    # 如果指定了appid，则使用指定的appid，否则使用当前登录的appid
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    url = f"{OPENAPI_MSGTPL_DETAIL}?uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}&appid={appid_to_use}&id={template_id}"
    response = session.get(url, verify=False)
    res = response.json()
    
    if res.get('retcode') != 0 and res.get('code') != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    
    template_detail = res.get('data', {})
    
    # 处理模板详情数据
    processed_detail = {
        'id': template_detail.get('模板id', ''),
        'name': template_detail.get('模板名称', '未命名'),
        'type': template_detail.get('模板类型', '未知类型'),
        'status': template_detail.get('模板状态', '未知状态'),
        'content': template_detail.get('模板内容', ''),
        'create_time': template_detail.get('创建时间', ''),
        'update_time': template_detail.get('更新时间', ''),
        'raw_data': template_detail  # 保留原始数据
    }
    
    return jsonify({
        'success': True,
        'data': {
            'uin': user_data.get('uin'),
            'appid': appid_to_use,
            'template': processed_detail
        }
    })

@web_panel.route('/openapi/render_button_template', methods=['POST'])
@require_auth
def openapi_render_button_template():
    """渲染按钮模板预览"""
    try:
        data = request.get_json()
        button_data = data.get('button_data')
        
        if not button_data:
            return jsonify({
                'success': False,
                'message': '缺少按钮数据'
            })
        
        # 处理按钮数据，生成HTML预览
        try:
            rows = button_data.get('rows', [])
            rendered_rows = []
            
            for row_idx, row in enumerate(rows[:5]):  # 最多显示5行
                buttons = row.get('buttons', [])
                rendered_buttons = []
                
                for btn in buttons[:5]:  # 每行最多5个按钮
                    render_data = btn.get('render_data', {})
                    action = btn.get('action', {})
                    
                    button_info = {
                        'label': render_data.get('label', 'Button'),
                        'style': render_data.get('style', 0),
                        'action_type': action.get('type', 2),
                        'action_data': action.get('data', ''),
                        'permission': action.get('permission', {}),
                        'unsupport_tips': action.get('unsupport_tips', ''),
                        'reply': action.get('reply', '')
                    }
                    rendered_buttons.append(button_info)
                
                if rendered_buttons:
                    rendered_rows.append({
                        'row_index': row_idx,
                        'buttons': rendered_buttons
                    })
            
            return jsonify({
                'success': True,
                'data': {
                    'rendered_rows': rendered_rows,
                    'total_rows': len(rendered_rows),
                    'max_buttons_per_row': 5
                }
            })
            
        except Exception as e:
            return jsonify({
                'success': False,
                'message': f'按钮渲染失败: {str(e)}'
            })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'渲染按钮模板失败: {str(e)}'
        })