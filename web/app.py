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

# 导入日志数据库函数
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
        return "连接中" if (client and hasattr(client, 'connected') and client.connected) else "连接失败"
    except Exception:
        return "连接失败"

PREFIX = '/web'
web = Blueprint('web', __name__, 
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

# ===== 通用工具函数 =====
def format_datetime(dt_str):
    """格式化日期时间字符串"""
    try:
        if isinstance(dt_str, str):
            dt = datetime.fromisoformat(dt_str)
        else:
            dt = dt_str
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def cleanup_expired_records(data_dict, time_field, expiry_seconds, cleanup_interval=3600):
    """通用的过期记录清理函数"""
    current_time = datetime.now()
    cleaned_count = 0
    
    for key in list(data_dict.keys()):
        record = data_dict[key]
        if time_field in record:
            try:
                record_time = datetime.fromisoformat(record[time_field])
                if (current_time - record_time).total_seconds() >= expiry_seconds:
                    if isinstance(record.get(time_field.replace('_time', '_times')), list):
                        record[time_field.replace('_time', '_times')] = []
                    cleaned_count += 1
            except Exception:
                pass
    
    return cleaned_count

def safe_file_operation(operation, file_path, data=None, default_return=None):
    """安全的文件操作函数"""
    try:
        if operation == 'read':
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return default_return or {}
        elif operation == 'write' and data is not None:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except Exception:
        return default_return

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
    elif 'tablet' in user_agent_lower:
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
    ip_access_data = safe_file_operation('read', IP_DATA_FILE, default_return={})

def save_ip_data():
    """保存IP数据到文件"""
    safe_file_operation('write', IP_DATA_FILE, ip_access_data)

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
        return True

def cleanup_expired_ip_bans():
    """清理过期的IP封禁记录"""
    global ip_access_data, _last_ip_cleanup
    
    current_time = time.time()
    if current_time - _last_ip_cleanup < 3600:
        return
    
    _last_ip_cleanup = current_time
    current_datetime = datetime.now()
    
    cleaned_count = 0
    for ip_address in list(ip_access_data.keys()):
        ip_data = ip_access_data[ip_address]
        
        cleanup_old_password_fails(ip_address)
        
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

# ===== 统一响应处理系统 =====
def create_response(success=True, data=None, error=None, status_code=200, response_type='api', **extra_data):
    """统一响应创建函数"""
    response_data = {'success': success}
    
    if success:
        if data is not None:
            response_data.update(data if isinstance(data, dict) else {'data': data})
        response_data.update(extra_data)
    else:
        error_field = 'message' if response_type == 'openapi' else 'error'
        response_data[error_field] = str(error) if error else 'Unknown error'
        response_data.update(extra_data)
    
    return jsonify(response_data), status_code

def api_error_response(error_msg, status_code=500, **extra_data):
    """API错误响应"""
    return create_response(False, error=error_msg, status_code=status_code, response_type='api', **extra_data)

def api_success_response(data=None, **extra_data):
    """API成功响应"""
    return create_response(True, data=data, response_type='api', **extra_data)

def openapi_error_response(error_msg, status_code=200):
    """OpenAPI错误响应"""
    return create_response(False, error=error_msg, status_code=status_code, response_type='openapi')

def openapi_success_response(data=None, **extra_data):
    """OpenAPI成功响应"""
    return create_response(True, data=data, response_type='openapi', **extra_data)

def check_openapi_login(user_id):
    """检查OpenAPI用户登录状态"""
    return openapi_user_data.get(user_id)

def catch_error(func):
    """统一错误捕获装饰器"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_msg = f"{func.__name__} 错误: {str(e)}"
            
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

# ===== 装饰器 =====
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
        cleanup_expired_ip_bans()
        cleanup_expired_sessions()
        
        client_ip = request.remote_addr
        if is_ip_banned(client_ip):
            return False
        
        token = request.args.get('token')
        if not token or token != WEB_SECURITY['access_token']:
            return False
        
        cookie_value = request.cookies.get(WEB_SECURITY['cookie_name'])
        if not cookie_value:
            return False
        
        is_valid, session_token = verify_cookie_value(cookie_value, WEB_SECURITY['cookie_secret'])
        if not is_valid or session_token not in valid_sessions:
            return False
        
        session_info = valid_sessions[session_token]
        
        if datetime.now() >= session_info['expires']:
            del valid_sessions[session_token]
            return False
        
        if WEB_SECURITY.get('production_mode', False):
            if (session_info.get('ip') != client_ip or 
                session_info.get('user_agent', '')[:200] != request.headers.get('User-Agent', '')[:200]):
                del valid_sessions[session_token]
                return False
        
        device_info = extract_device_info(request)
        record_ip_access(client_ip, access_type='token_success', device_info=device_info)
        
        return f(*args, **kwargs)
    return decorated_function

def check_ip_ban(f):
    """检查IP是否被封禁的装饰器"""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        cleanup_expired_ip_bans()
        
        client_ip = request.remote_addr
        if is_ip_banned(client_ip):
            return '', 403
        
        return f(*args, **kwargs)
    return decorated_function

def cleanup_expired_sessions():
    """清理过期的会话"""
    global valid_sessions, _last_session_cleanup
    
    current_time = time.time()
    if current_time - _last_session_cleanup < 300:
        return
    
    _last_session_cleanup = current_time
    current_datetime = datetime.now()
    
    expired_sessions = []
    for session_token, session_info in valid_sessions.items():
        if current_datetime >= session_info['expires']:
            expired_sessions.append(session_token)
    
    for session_token in expired_sessions:
        del valid_sessions[session_token]

def limit_session_count():
    """限制同时活跃的会话数量"""
    max_sessions = 10
    if len(valid_sessions) > max_sessions:
        sorted_sessions = sorted(valid_sessions.items(), 
                               key=lambda x: x[1]['created'])
        while len(valid_sessions) > max_sessions:
            oldest_session = sorted_sessions.pop(0)
            del valid_sessions[oldest_session[0]]

# ===== 日志处理类 =====
class LogHandler:
    """统一日志处理基类"""
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
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        entry = {'timestamp': timestamp, 'content': content}
        
        if traceback_info:
            entry['traceback'] = traceback_info
            
        self.logs.append(entry)
        self.global_logs.append(entry)
        
        if not skip_db and LOG_DB_CONFIG.get('enabled', False):
            try:
                add_log_to_db(self.log_type, entry)
            except Exception:
                pass
        
        if socketio is not None:
            try:
                socketio.emit('new_message', {
                    'type': self.log_type,
                    'data': entry
                }, namespace=PREFIX)
            except Exception:
                pass
                
        return entry

# ===== 日志处理器实例 =====
received_handler = LogHandler('received')
plugin_handler = LogHandler('plugin')
framework_handler = LogHandler('framework')
error_handler = LogHandler('error')

# ===== 日志与消息相关API =====
@catch_error
def add_received_message(message):
    user_id, group_id, pure_content, formatted_message = _parse_message_info(message)
    
    display_entry = received_handler.add(formatted_message, skip_db=True)
    
    db_entry = {
        'timestamp': display_entry['timestamp'],
        'content': pure_content,
        'user_id': user_id,
        'group_id': group_id
    }
    try:
        add_log_to_db('received', db_entry)
    except Exception:
        pass
        
    return display_entry



def _parse_message_info(message):
    """解析消息信息，提取用户ID、群组ID和内容"""
    try:
        if isinstance(message, str) and not message.startswith('{'):
            return "未知用户", "c2c", message, message
        
        event = MessageEvent(message)
        user_id = event.user_id or "未知用户"
        group_id = event.group_id or "c2c"
        pure_content = event.content or ""
        
        # 特殊处理邀请进群事件
        if getattr(event, 'event_type', None) == 'GROUP_ADD_ROBOT':
            inviter_id = user_id  # 邀请者ID
            group_id = event.group_id or "未知群组"
            pure_content = f"机器人被邀请进群"
            formatted_message = f"{inviter_id} 邀请机器人进入群组 {group_id}"
            return inviter_id, group_id, pure_content, formatted_message
        
        if getattr(event, 'event_type', None) == 'INTERACTION_CREATE':
            chat_type = event.get('d/chat_type')
            scene = event.get('d/scene')
            if chat_type == 2 or scene == 'c2c':
                group_id = "c2c"
                user_id = event.get('d/user_openid') or user_id
            button_data = event.get('d/data/resolved/button_data')
            if button_data and not pure_content:
                pure_content = button_data
        
        if group_id != "c2c":
            formatted_message = f"{user_id}（{group_id}）：{pure_content}"
        else:
            formatted_message = f"{user_id}：{pure_content}"
            
        return user_id, group_id, pure_content, formatted_message
        
    except Exception as e:
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

# ===== 路由 =====
@web.route('/login', methods=['POST'])
@check_ip_ban
@require_token
@catch_error
def login():
    """处理密码验证"""
    password = request.form.get('password')
    token = request.form.get('token')
    
    if password == WEB_SECURITY['admin_password']:
        cleanup_expired_sessions()
        limit_session_count()
        
        device_info = extract_device_info(request)
        record_ip_access(request.remote_addr, access_type='password_success', device_info=device_info)
        
        session_token = generate_session_token()
        expires = datetime.now() + timedelta(days=WEB_SECURITY['cookie_expires_days'])
        
        valid_sessions[session_token] = {
            'created': datetime.now(),
            'expires': expires,
            'ip': request.remote_addr,
            'user_agent': request.headers.get('User-Agent', '')[:200]
        }
        
        signed_token = sign_cookie_value(session_token, WEB_SECURITY['cookie_secret'])
        
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
            secure=False,
            samesite='Lax'
        )
        return response
    else:
        record_ip_access(request.remote_addr, access_type='password_fail')
        return render_template('login.html', token=token, error='密码错误，请重试')

@web.route('/')
@check_ip_ban
@require_token
@require_auth
@catch_error
def index():
    """Web面板首页"""
    user_agent = request.headers.get('User-Agent', '').lower()
    device_type = request.args.get('device', None)
    
    if device_type is None:
        mobile_keywords = ['android', 'iphone', 'ipad', 'mobile', 'phone', 'tablet']
        is_mobile = any(keyword in user_agent for keyword in mobile_keywords)
        device_type = 'mobile' if is_mobile else 'pc'
    
    template_name = 'mobile.html' if device_type == 'mobile' else 'index.html'
    
    response = make_response(render_template(template_name, 
                                           prefix=PREFIX, 
                                           device_type=device_type,
                                           ROBOT_QQ=ROBOT_QQ,
                                           appid=appid,
                                           WEBSOCKET_CONFIG=WEBSOCKET_CONFIG))
    
    if WEB_SECURITY.get('secure_headers', True):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com; font-src 'self' cdn.jsdelivr.net cdnjs.cloudflare.com; img-src 'self' data: *.myqcloud.com thirdqq.qlogo.cn api.2dcode.biz; connect-src 'self' i.elaina.vin"
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        response.headers['Strict-Transport-Security'] = 'max-age=0'
    
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    return response

@web.route('/api/logs/<log_type>')
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

@web.route('/status')
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

@web.route('/api/statistics')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_statistics():
    """获取统计数据API"""
    force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
    
    selected_date = request.args.get('date')
    
    if selected_date and selected_date != 'today':
        date_data = get_specific_date_data(selected_date)
        if not date_data:
            return api_error_response(f'未找到日期 {selected_date} 的数据', 404)
        return api_success_response({
            'selected_date_data': date_data,
            'date': selected_date
        })
    else:
        data = get_statistics_data(force_refresh=force_refresh)
        return api_success_response(data)

@web.route('/api/complete_dau', methods=['POST'])
@check_ip_ban
@require_token
@catch_error
def complete_dau():
    """补全DAU数据API"""
    result = complete_dau_data()
    return api_success_response(result=result)

@web.route('/api/get_nickname/<user_id>')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_user_nickname(user_id):
    """获取用户昵称API"""
    nickname = fetch_user_nickname(user_id)
    return api_success_response(nickname=nickname, user_id=user_id)

@web.route('/api/available_dates')
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

@web.route('/api/robot_info')
@catch_error  
def get_robot_info():
    """获取机器人信息API"""
    robot_share_url = f"https://qun.qq.com/qunpro/robot/qunshare?robot_uin={ROBOT_QQ}"
    is_websocket = WEBSOCKET_CONFIG.get('enabled', False)
    connection_type = 'WebSocket' if is_websocket else 'WebHook'
    connection_status = get_websocket_status() if is_websocket else 'WebHook'
    
    try:
        api_url = f"https://qun.qq.com/qunng/http2rpc/gotrpc/noauth/trpc.group_pro_robot.manager.TrpcHandler/GetShareInfo?robot_uin={ROBOT_QQ}"
        
        response = requests.get(api_url, timeout=10)
        response.raise_for_status()
        api_response = response.json()
        
        if api_response.get('retcode') != 0:
            raise Exception(f"API返回错误: {api_response.get('message', 'Unknown error')}")
        
        robot_data = api_response.get('data', {}).get('data', {})
        
        avatar_url = robot_data.get('robot_avatar', '')
        if avatar_url and 'myqcloud.com' in avatar_url:
            avatar_url += '&imageMogr2/format/png' if '?' in avatar_url else '?imageMogr2/format/png'
        
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
        return jsonify(_build_fallback_robot_info(str(e), robot_share_url, connection_type, connection_status)), 500

@web.route('/api/robot_qrcode')
@catch_error
def get_robot_qrcode():
    """生成机器人分享链接的二维码"""
    url = request.args.get('url')
    if not url:
        return api_error_response('缺少URL参数', 400)
    
    qr_api_url = f"https://api.2dcode.biz/v1/create-qr-code?data={url}"
    
    response = requests.get(qr_api_url, timeout=10)
    response.raise_for_status()
    
    return response.content, 200, {
        'Content-Type': 'image/png',
        'Cache-Control': 'public, max-age=3600'
    }

@catch_error
def get_system_info():
    global _last_gc_time, _last_gc_log_time
    
    try:
        process = psutil.Process(os.getpid())
        current_time = time.time()
        collected = 0
        
        if current_time - _last_gc_time >= _gc_interval:
            collected = gc.collect(0)
            _last_gc_time = current_time
        
        memory_info = process.memory_info()
        rss = memory_info.rss / 1024 / 1024
        
        system_memory = psutil.virtual_memory()
        system_memory_total = system_memory.total / (1024 * 1024)
        system_memory_used = system_memory.used / (1024 * 1024)
        system_memory_percent = system_memory.percent
        
        process_memory_used = rss
        
        try:
            cpu_cores = psutil.cpu_count(logical=True)
            
            cpu_percent = process.cpu_percent(interval=0.05)
            system_cpu_percent = psutil.cpu_percent(interval=0.05)
            
            if cpu_percent <= 0:
                cpu_percent = 1.0
            if system_cpu_percent <= 0:
                system_cpu_percent = 5.0
        except Exception as e:
            error_msg = f"获取CPU信息失败: {str(e)}"
            add_error_log(error_msg)
            cpu_cores = 1
            cpu_percent = 1.0
            system_cpu_percent = 5.0
        
        app_uptime_seconds = int((datetime.now() - START_TIME).total_seconds())
        
        try:
            boot_time = datetime.fromtimestamp(psutil.boot_time())
            system_uptime = datetime.now() - boot_time
            system_uptime_seconds = int(system_uptime.total_seconds())
            boot_time_str = boot_time.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            system_uptime_seconds = app_uptime_seconds
            boot_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        try:
            start_time_str = START_TIME.strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            start_time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        try:
            import platform
            system_version = platform.platform()
        except Exception:
            system_version = "未知"
        
        try:
            disk_path = os.path.abspath(os.getcwd())
            disk_usage = psutil.disk_usage(disk_path)
            
            disk_info = {
                'total': float(disk_usage.total),
                'used': float(disk_usage.used),
                'free': float(disk_usage.free),
                'percent': float(disk_usage.percent)
            }
            
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
            disk_info = {
                'total': float(100 * 1024 * 1024 * 1024),
                'used': float(50 * 1024 * 1024 * 1024),
                'free': float(50 * 1024 * 1024 * 1024),
                'percent': float(50.0),
                'framework_usage': float(1 * 1024 * 1024 * 1024)
            }
        
        system_info = {
            'cpu_percent': float(system_cpu_percent),
            'framework_cpu_percent': float(cpu_percent),
            'cpu_cores': cpu_cores,
            
            'memory_percent': float(system_memory_percent),
            'memory_used': float(system_memory_used),
            'memory_total': float(system_memory_total),
            'total_memory': float(system_memory_total),
            'system_memory_total_bytes': float(system_memory.total),
            'framework_memory_percent': float((rss / system_memory_total) * 100 if system_memory_total > 0 else 5.0),
            'framework_memory_total': float(rss),
            
            'gc_counts': list(gc.get_count()),
            'objects_count': len(gc.get_objects()),
            
            'disk_info': disk_info,
            
            'uptime': app_uptime_seconds,
            'system_uptime': system_uptime_seconds,
            'start_time': start_time_str,
            'boot_time': boot_time_str,
            
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
            'memory_used': 400.0,
            'memory_total': 8192.0,
            'total_memory': 8192.0,
            'system_memory_total_bytes': 8192.0 * 1024 * 1024,
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
    
    last_modified_str = ""
    try:
        last_modified = os.path.getmtime(plugin_path)
        last_modified_str = datetime.fromtimestamp(last_modified).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        pass
        
    for attr_name in dir(module):
        if attr_name.startswith('__') or not hasattr(getattr(module, attr_name), '__class__'):
            continue
            
        attr = getattr(module, attr_name)
        if isinstance(attr, type) and attr.__module__ == module.__name__:
            if hasattr(attr, 'get_regex_handlers'):
                plugin_classes_found = True
                
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
                
                try:
                    handlers = attr.get_regex_handlers()
                    plugin_info['handlers'] = len(handlers) if handlers else 0
                    plugin_info['handlers_list'] = list(handlers.keys()) if handlers else []
                    
                    plugin_info['priority'] = getattr(attr, 'priority', 10)
                    
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
                
                plugin_info_list.append(plugin_info)
    
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
        dir_name = os.path.basename(os.path.dirname(plugin_file))
        
        full_module_name = f"plugins.{dir_name}.{module_name}"
        
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
    
    for dir_name in os.listdir(plugins_dir):
        plugin_dir = os.path.join(plugins_dir, dir_name)
        if os.path.isdir(plugin_dir):
            py_files = [f for f in os.listdir(plugin_dir) if f.endswith('.py') and f != '__init__.py']
            
            for py_file in py_files:
                plugin_file = os.path.join(plugin_dir, py_file)
                plugin_name = os.path.splitext(py_file)[0]
                
                plugin_info_list = load_plugin_module(
                    plugin_file, 
                    plugin_name,
                    is_system=(dir_name == 'system')
                )
                
                plugins_info.extend(plugin_info_list)
    
    plugins_info.sort(key=lambda x: (0 if x['status'] == 'loaded' else 1))
    return plugins_info

@catch_error
def register_socketio_handlers(sio):
    """注册Socket.IO事件处理函数"""
    @sio.on('connect', namespace=PREFIX)
    @require_socketio_token
    def handle_connect():
        sid = request.sid
        
        def async_load_initial_data():
            system_info = get_system_info()
            
            try:
                sio.emit('system_info', system_info, room=sid, namespace=PREFIX)
            except Exception:
                pass
                
            plugins = scan_plugins()
            
            try:
                sio.emit('plugins_update', plugins, room=sid, namespace=PREFIX)
                
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
                
                for log_type in logs_data:
                    if 'logs' in logs_data[log_type]:
                        logs_data[log_type]['logs'].reverse()
                
                sio.emit('logs_batch', logs_data, room=sid, namespace=PREFIX)
            except Exception:
                pass
        
        threading.Thread(target=async_load_initial_data, daemon=True).start()

    @sio.on('disconnect', namespace=PREFIX)
    def handle_disconnect():
        pass

    @sio.on('get_system_info', namespace=PREFIX)
    @require_socketio_token
    def handle_get_system_info():
        system_info = get_system_info()
        
        sio.emit('system_info', system_info, room=request.sid, namespace=PREFIX)



    @sio.on('request_logs', namespace=PREFIX)
    @require_socketio_token  
    def handle_request_logs(data):
        log_type = data.get('type', 'received')
        page = data.get('page', 1)
        page_size = data.get('page_size', 50)
        
        logs_map = {
            'received': received_handler.logs,
            'plugin': plugin_handler.logs,
            'framework': framework_handler.logs,
            'error': error_handler.logs
        }
        
        logs = list(logs_map.get(log_type, []))
        logs.reverse()
        
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

def get_statistics_data(force_refresh=False):
    global statistics_cache, statistics_cache_time
    
    current_time = time.time()
    current_date = datetime.now().date()
    
    cache_valid = (not force_refresh and 
                  statistics_cache is not None and 
                  current_time - statistics_cache_time < STATISTICS_CACHE_DURATION)
    
    if cache_valid and statistics_cache:
        cache_date_str = statistics_cache.get('cache_date')
        if cache_date_str:
            try:
                cache_date = datetime.strptime(cache_date_str, '%Y-%m-%d').date()
                if cache_date != current_date:
                    cache_valid = False
            except Exception as e:
                cache_valid = False
        else:
            cache_valid = False
    
    if cache_valid:
        return statistics_cache
    
    try:
        historical_data = load_historical_dau_data()
        
        today_data = get_today_dau_data(force_refresh)
        
        result = {
            'historical': historical_data,
            'today': today_data,
            'cache_time': current_time,
            'cache_date': current_date.strftime('%Y-%m-%d')
        }
        
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
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        dau_analytics = get_dau_analytics()
        historical_data = []
        
        today = datetime.now()
        
        for i in range(1, 31):
            target_date = today - timedelta(days=i)
            
            try:
                dau_data = dau_analytics.load_dau_data(target_date)
                
                if dau_data:
                    display_date = target_date.strftime('%m-%d')
                    dau_data['display_date'] = display_date
                    historical_data.append(dau_data)
            except Exception:
                continue
        
        historical_data.sort(key=lambda x: x.get('date', ''))
        
        return historical_data
        
    except Exception as e:
        add_error_log(f"加载历史DAU数据失败: {str(e)}")
        return []

def get_today_dau_data(force_refresh=False):
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        dau_analytics = get_dau_analytics()
        today = datetime.now()
        
        today_data = None
        if not force_refresh:
            today_data = dau_analytics.load_dau_data(today)
        
        if not today_data:
            try:
                today_data = dau_analytics.collect_dau_data(today)
                
                if today_data:
                    today_data['is_realtime'] = True
                    today_data['cache_time'] = time.time()
            except Exception as e:
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

def start_web(main_app=None):
    global socketio
    if main_app is None:
        app = Flask(__name__)
        app.register_blueprint(web, url_prefix=PREFIX)
        CORS(app, resources={r"/*": {"origins": "*"}})
        try:
            socketio = SocketIO(app, 
                            cors_allowed_origins="*",
                            path="/socket.io",
                            async_mode='eventlet',
                            logger=False,
                            engineio_logger=False)
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
        if not any(bp.name == 'web' for bp in main_app.blueprints.values()):
            main_app.register_blueprint(web, url_prefix=PREFIX)
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
                                async_mode='eventlet',
                                logger=False,
                                engineio_logger=False)
                main_app.socketio = socketio
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
    import os
    import sys
    
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        dau_analytics = get_dau_analytics()
        today = datetime.now()
        
        missing_dates = []
        
        for i in range(1, 31):
            target_date = today - timedelta(days=i)
            
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
    import requests
    
    try:
        if not user_id or len(user_id) < 3:
            return None
            
        from config import appid
        
        api_url = f"https://i.elaina.vin/api/bot/xx.php?openid={user_id}&appid={appid}"
        
        response = requests.get(api_url, timeout=3)
        
        if response.status_code == 200:
            data = response.json()
            nickname = data.get('名字', '').strip()
            
            if nickname and nickname != user_id and len(nickname) > 0 and len(nickname) <= 20:
                return nickname
            else:
                return None
        else:
                    return None
        
    except Exception as e:
        return None

@web.route('/api/sandbox/test', methods=['POST'])
@require_auth
@catch_error
def sandbox_test():
    data = request.get_json()
    if not data:
        return api_error_response('缺少请求数据', 400)
    
    message_content = data.get('message', '').strip()
    group_id = data.get('group_id', '').strip()
    user_id = data.get('user_id', '').strip()
    
    if not message_content:
        return api_error_response('消息内容不能为空', 400)
    if not user_id:
        return api_error_response('用户ID不能为空', 400)
    
    is_private = not group_id
    message_type = "C2C_MESSAGE_CREATE" if is_private else "GROUP_AT_MESSAGE_CREATE"
    
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
    
    if is_private:
        pass
    else:
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
    
    try:
        event = MessageEvent(mock_data)
        
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
        
        event.reply = mock_reply
        
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            
            from core.plugin.PluginManager import PluginManager
            
            PluginManager.dispatch_message(event)
            
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
    import os
    import glob
    from datetime import datetime, timedelta
    
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        dau_folder = os.path.join(project_root, 'data', 'dau')
        
        if not os.path.exists(dau_folder):
            return []
        
        json_files = glob.glob(os.path.join(dau_folder, '*.json'))
        
        available_dates = []
        today = datetime.now().date()
        thirty_days_ago = today - timedelta(days=30)
        
        for file_path in json_files:
            filename = os.path.basename(file_path)
            if filename.endswith('.json'):
                date_str = filename[:-5]
                try:
                    file_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                    
                    if file_date >= thirty_days_ago and file_date <= today:
                        is_today = file_date == today
                        display_name = "今日数据" if is_today else f"{file_date.strftime('%m-%d')} ({file_date.strftime('%Y-%m-%d')})"
                        
                        available_dates.append({
                            'value': 'today' if is_today else date_str,
                            'date': date_str,
                            'display': display_name,
                            'is_today': is_today
                        })
                except ValueError:
                    continue
        
        today_exists = any(item['is_today'] for item in available_dates)
        if not today_exists:
            available_dates.append({
                'value': 'today',
                'date': today.strftime('%Y-%m-%d'),
                'display': '今日数据 (实时)',
                'is_today': True
            })
        
        available_dates.sort(key=lambda x: (not x['is_today'], -int(x['date'].replace('-', ''))))
        
        return available_dates
        
    except Exception as e:
        return [{
            'value': 'today',
            'date': datetime.now().strftime('%Y-%m-%d'),
            'display': '今日数据',
                         'is_today': True
         }]

def get_specific_date_data(date_str):
    import os
    from datetime import datetime
    
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        
        dau_analytics = get_dau_analytics()
        dau_data = dau_analytics.load_dau_data(target_date)
        
        if not dau_data:
            return None
        
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

openapi_user_data = {}
openapi_login_tasks = {}

OPENAPI_DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'openapi.json')

OPENAPI_BASE = 'https://i.elaina.vin/api/bot'
OPENAPI_LOGIN_URL = f"{OPENAPI_BASE}/get_login.php"
OPENAPI_GET_LOGIN = f"{OPENAPI_BASE}/robot.php"
OPENAPI_MESSAGE = f"{OPENAPI_BASE}/message.php"
OPENAPI_BOTLIST = f"{OPENAPI_BASE}/bot_list.php"
OPENAPI_BOTDATA = f"{OPENAPI_BASE}/bot_data.php"
OPENAPI_MSGTPL = f"{OPENAPI_BASE}/md.php"
OPENAPI_MSGTPL_DETAIL = f"{OPENAPI_BASE}/md_detail.php"

def create_openapi_session():
    session = requests.Session()
    session.verify = False
    return session

def save_openapi_data():
    try:
        os.makedirs(os.path.dirname(OPENAPI_DATA_FILE), exist_ok=True)
        
        with open(OPENAPI_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(openapi_user_data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def load_openapi_data():
    global openapi_user_data
    try:
        if os.path.exists(OPENAPI_DATA_FILE):
            with open(OPENAPI_DATA_FILE, 'r', encoding='utf-8') as f:
                openapi_user_data = json.load(f)
        else:
            openapi_user_data = {}
    except Exception:
        openapi_user_data = {}

def verify_openapi_login(user_data):
    try:
        if not user_data or user_data.get('type') != 'ok':
            return False
        
        session = create_openapi_session()
        url = f"{OPENAPI_BOTLIST}?uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}"
        response = session.get(url, verify=False, timeout=10)
        res = response.json()
        
        return res.get('code') == 0
    except Exception:
        return False

def get_app_type_name(app_type):
    if app_type == '0' or app_type == 0:
        return '小程序'
    elif app_type == '1' or app_type == 1:
        return 'QQ小程序' 
    elif app_type == '2' or app_type == 2:
        return 'QQ机器人'
    else:
        return '未知类型'

@web.route('/openapi/start_login', methods=['POST'])
@require_auth
def openapi_start_login():
    try:
        data = request.get_json()
        user_id = data.get('user_id', 'web_user')
        
        current_time = time.time()
        
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

@web.route('/openapi/check_login', methods=['POST'])
@require_auth
def openapi_check_login():
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
        
        response = session.get(f"{OPENAPI_GET_LOGIN}?qrcode={qr}", verify=False)
        res = response.json()
        
        if res.get('code') == 0:
            login_data = res.get('data', {}).get('data', {})
            openapi_user_data[user_id] = {'type': 'ok', **login_data}
            
            if user_id in openapi_login_tasks:
                del openapi_login_tasks[user_id]
            
            app_type = login_data.get('appType')
            app_type_str = get_app_type_name(app_type)
            
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

@web.route('/openapi/get_botlist', methods=['POST'])
@require_auth
def openapi_get_botlist():
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

@web.route('/openapi/get_botdata', methods=['POST'])
@require_auth  
@catch_error
def openapi_get_botdata():
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    target_appid = data.get('appid')
    days = data.get('days', 30)
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    session = create_openapi_session()
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    base_url = f"{OPENAPI_BOTDATA}?appid={appid_to_use}&uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}"
    
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
    
    avg_dau = round(total_up_msg_people / 30, 2) if len(msg_data) > 0 else 0
    
    return openapi_success_response({
        'uin': user_data.get('uin'),
        'appid': appid_to_use,
        'avg_dau': avg_dau,
        'days_data': processed_data
    })

@web.route('/openapi/get_notifications', methods=['POST'])
@require_auth
@catch_error
def openapi_get_notifications():
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

@web.route('/openapi/logout', methods=['POST'])
@require_auth
@catch_error  
def openapi_logout():
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    
    if user_id in openapi_user_data:
        del openapi_user_data[user_id]
    
    save_openapi_data()
    return openapi_success_response(message='登出成功')

@web.route('/openapi/get_login_status', methods=['POST'])
@require_auth
@catch_error
def openapi_get_login_status():
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

def cleanup_openapi_tasks():
    current_time = time.time()
    expired_users = []
    
    for user_id, (start_time, _) in openapi_login_tasks.items():
        if current_time - start_time > 300:
            expired_users.append(user_id)
    
    for user_id in expired_users:
        del openapi_login_tasks[user_id]

import threading
import time as time_module

def start_openapi_cleanup_thread():
    def cleanup_loop():
        while True:
            try:
                cleanup_openapi_tasks()
                time_module.sleep(60)
            except Exception:
                time_module.sleep(60)
    
    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()

start_openapi_cleanup_thread()

@web.route('/openapi/import_templates', methods=['POST'])
@require_auth
@catch_error
def openapi_import_templates():
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    target_appid = data.get('appid')
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    session = create_openapi_session()
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    url = f"{OPENAPI_MSGTPL}?uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}&appid={appid_to_use}"
    response = session.get(url, verify=False)
    res = response.json()
    
    if res.get('retcode') != 0 and res.get('code') != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    
    raw_templates = res.get('data', {}).get('list', [])
    
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
    
    button_templates = [t for t in templates if t.get('type') == '按钮模板']
    markdown_templates = [t for t in templates if t.get('type') == 'markdown模板']
    
    try:
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
    import os
    import re
    from core.event.markdown_templates import MARKDOWN_TEMPLATES
    
    template_file_path = os.path.join(os.getcwd(), 'core', 'event', 'markdown_templates.py')
    
    with open(template_file_path, 'r', encoding='utf-8') as f:
        current_content = f.read()
    
    existing_ids = set()
    for template_config in MARKDOWN_TEMPLATES.values():
        existing_ids.add(template_config['id'])
    
    new_templates = []
    skipped_count = 0
    
    for template in templates:
        template_id = template.get('id', '')
        template_name = template.get('name', '未命名')
        template_content = template.get('content', '')
        
        if template_id in existing_ids:
            skipped_count += 1
            continue
            
        params = _extract_template_params(template_content)
        
        new_templates.append({
            'id': template_id,
            'name': template_name,
            'content': template_content,
            'params': params,
            'raw_data': template
        })
    
    existing_template_names = set(MARKDOWN_TEMPLATES.keys())
    template_counter = 1
    
    new_template_entries = []
    
    for template in new_templates:
        while str(template_counter) in existing_template_names:
            template_counter += 1
        
        template_name = str(template_counter)
        existing_template_names.add(template_name)
        
        escaped_content = template['content'].replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
        
        template_entry = f'''    "{template_name}": {{
        "id": "{template['id']}",
        "params": {template['params']}
    }},
    # 原始模板内容: {escaped_content}
'''
        new_template_entries.append(template_entry)
        template_counter += 1
    
    button_entries = []
    for button in button_templates:
        button_id = button.get('id', '')
        button_name = button.get('name', '未命名按钮')
        
        button_entry = f'''    # 按钮ID: {button_id} - {button_name}
'''
        button_entries.append(button_entry)
    
    if new_template_entries or button_entries:
        pattern = r'(MARKDOWN_TEMPLATES\s*=\s*\{.*?)(\n\})'
        match = re.search(pattern, current_content, re.DOTALL)
        
        if match:
            before_end = match.group(1)
            
            new_content = before_end
            
            if new_template_entries:
                new_content += '\n'
                for entry in new_template_entries:
                    new_content += entry
                    
            if button_entries:
                new_content += '\n    # 按钮模板ID\n'
                for entry in button_entries:
                    new_content += entry
                    
            new_content += match.group(2)
            
            updated_file_content = current_content.replace(match.group(0), new_content)
            
            with open(template_file_path, 'w', encoding='utf-8') as f:
                f.write(updated_file_content)
        else:
            raise Exception("无法找到MARKDOWN_TEMPLATES字典结构")
    
    return {
        'imported_count': len(new_templates),
        'skipped_count': skipped_count,
        'button_count': len(button_templates),
        'message': f'成功导入{len(new_templates)}个模板，跳过{skipped_count}个已存在模板'
    }

def _extract_template_params(template_content):
    import re
    
    param_pattern = r'\{\{\.(\w+)\}\}'
    matches = re.findall(param_pattern, template_content)
    
    params = []
    seen = set()
    for param in matches:
        if param not in seen:
            params.append(param)
            seen.add(param)
    
    return params

@web.route('/openapi/verify_saved_login', methods=['POST'])
@require_auth
def openapi_verify_saved_login():
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

load_openapi_data()

start_openapi_cleanup_thread()

@web.route('/openapi/get_templates', methods=['POST'])
@require_auth
@catch_error
def openapi_get_templates():
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    target_appid = data.get('appid')
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    session = create_openapi_session()
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    url = f"{OPENAPI_MSGTPL}?uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}&appid={appid_to_use}"
    response = session.get(url, verify=False)
    res = response.json()
    
    if res.get('retcode') != 0 and res.get('code') != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    
    templates = res.get('data', {}).get('list', [])
    
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
            'raw_data': template
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

@web.route('/openapi/get_template_detail', methods=['POST'])
@require_auth
@catch_error
def openapi_get_template_detail():
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
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    url = f"{OPENAPI_MSGTPL_DETAIL}?uin={user_data.get('uin')}&ticket={user_data.get('ticket')}&developerId={user_data.get('developerId')}&appid={appid_to_use}&id={template_id}"
    response = session.get(url, verify=False)
    res = response.json()
    
    if res.get('retcode') != 0 and res.get('code') != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    
    template_detail = res.get('data', {})
    
    processed_detail = {
        'id': template_detail.get('模板id', ''),
        'name': template_detail.get('模板名称', '未命名'),
        'type': template_detail.get('模板类型', '未知类型'),
        'status': template_detail.get('模板状态', '未知状态'),
        'content': template_detail.get('模板内容', ''),
        'create_time': template_detail.get('创建时间', ''),
        'update_time': template_detail.get('更新时间', ''),
        'raw_data': template_detail
    }
    
    return jsonify({
        'success': True,
        'data': {
            'uin': user_data.get('uin'),
            'appid': appid_to_use,
            'template': processed_detail
        }
    })

@web.route('/openapi/render_button_template', methods=['POST'])
@require_auth
def openapi_render_button_template():
    try:
        data = request.get_json()
        button_data = data.get('button_data')
        
        if not button_data:
            return jsonify({
                'success': False,
                'message': '缺少按钮数据'
            })
        
        try:
            rows = button_data.get('rows', [])
            rendered_rows = []
            
            for row_idx, row in enumerate(rows[:5]):
                buttons = row.get('buttons', [])
                rendered_buttons = []
                
                for btn in buttons[:5]:
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