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
import random
from datetime import datetime, timedelta
from collections import deque
from flask import Flask, render_template, request, jsonify, Blueprint, make_response
from flask_socketio import SocketIO
from flask_cors import CORS
import psutil
import requests
from config import LOG_DB_CONFIG, WEB_SECURITY, WEB_INTERFACE, ROBOT_QQ, appid, WEBSOCKET_CONFIG

try:
    from function.log_db import add_log_to_db, add_sent_message_to_db
except ImportError:
    def add_log_to_db(log_type, log_data):
        return False
    def add_sent_message_to_db(chat_type, chat_id, content, raw_message=None, timestamp=None):
        return False

def get_websocket_status():
    try:
        from function.ws_client import get_client
        client = get_client("qq_bot")
        return "连接成功" if (client and hasattr(client, 'connected') and client.connected) else "连接失败"
    except Exception:
        return "连接失败"

PREFIX = '/web'
web = Blueprint('web', __name__, 
                     template_folder='templates',
                     static_folder='static')
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

historical_data_cache = None
historical_cache_loaded = False
today_data_cache = None
today_cache_time = 0
TODAY_CACHE_DURATION = 600
statistics_cache = None
statistics_cache_time = 0
STATISTICS_CACHE_DURATION = 300

# 异步任务管理
statistics_tasks = {}  # 存储统计任务状态
task_results = {}      # 存储任务结果

def format_datetime(dt_str):
    try:
        if isinstance(dt_str, str):
            dt = datetime.fromisoformat(dt_str)
        else:
            dt = dt_str
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def cleanup_expired_records(data_dict, time_field, expiry_seconds, cleanup_interval=3600):
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
    global ip_access_data
    ip_access_data = safe_file_operation('read', IP_DATA_FILE, default_return={})

def save_ip_data():
    safe_file_operation('write', IP_DATA_FILE, ip_access_data)

def record_ip_access(ip_address, access_type='token_success', device_info=None):
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

cleanup_old_password_fails = lambda ip: ip_access_data[ip].update({'password_fail_times': [t for t in ip_access_data[ip]['password_fail_times'] if (datetime.now() - datetime.fromisoformat(t)).total_seconds() < 86400]}) if ip in ip_access_data else None
cleanup_old_password_success = lambda ip: ip_access_data[ip].update({'password_success_times': [t for t in ip_access_data[ip]['password_success_times'] if (datetime.now() - datetime.fromisoformat(t)).total_seconds() < 2592000]}) if ip in ip_access_data else None

def is_ip_banned(ip_address):
    if ip_address not in ip_access_data or not (ip_data := ip_access_data[ip_address]).get('is_banned'):
        return False
    if not (ban_time_str := ip_data.get('ban_time')):
        return True
    try:
        if (datetime.now() - datetime.fromisoformat(ban_time_str)).total_seconds() >= 86400:
            ip_data.update({'is_banned': False, 'ban_time': None, 'password_fail_times': []})
            save_ip_data()
            return False
            return True
    except:
        return True

def cleanup_expired_ip_bans():
    global ip_access_data, _last_ip_cleanup
    if (current_time := time.time()) - _last_ip_cleanup < 3600:
        return
    _last_ip_cleanup, current_datetime, cleaned = current_time, datetime.now(), 0
    for ip_address, ip_data in list(ip_access_data.items()):
        cleanup_old_password_fails(ip_address)
        if ip_data.get('is_banned') and (ban_time_str := ip_data.get('ban_time')):
            try:
                if (current_datetime - datetime.fromisoformat(ban_time_str)).total_seconds() >= 86400:
                    ip_data.update({'is_banned': False, 'ban_time': None, 'password_fail_times': []})
                    cleaned += 1
            except:
                pass
    if cleaned:
        save_ip_data()

load_ip_data()

def create_response(success=True, data=None, error=None, status_code=200, response_type='api', **extra):
    if success:
        result = {'success': True}
        if data:
            result.update(data if isinstance(data, dict) else {'data': data})
        result.update(extra)
    else:
        result = {'success': False, ('message' if response_type == 'openapi' else 'error'): str(error) if error else 'Unknown error'}
        result.update(extra)
    return jsonify(result), status_code

api_error_response = lambda error_msg, status_code=500, **extra: create_response(False, error=error_msg, status_code=status_code, **extra)
api_success_response = lambda data=None, **extra: create_response(True, data=data, **extra)
openapi_error_response = lambda error_msg, status_code=200: create_response(False, error=error_msg, status_code=status_code, response_type='openapi')
openapi_success_response = lambda data=None, **extra: create_response(True, data=data, response_type='openapi', **extra)
check_openapi_login = lambda user_id: openapi_user_data.get(user_id)

def catch_error(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            try:
                add_log_to_db('error', {'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'content': f"{func.__name__} 错误: {str(e)}"})
            except:
                pass
            return api_error_response(str(e))
    return wrapper

generate_session_token = lambda: base64.urlsafe_b64encode(uuid.uuid4().bytes).decode('utf-8').rstrip('=')
sign_cookie_value = lambda value, secret: f"{value}.{hmac.new(secret.encode('utf-8'), value.encode('utf-8'), hashlib.sha256).hexdigest()}"

def verify_cookie_value(signed_value, secret):
    try:
        value, signature = signed_value.rsplit('.', 1)
        return hmac.compare_digest(signature, hmac.new(secret.encode('utf-8'), value.encode('utf-8'), hashlib.sha256).hexdigest()), value
    except:
        return False, None

def require_token(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not (token := request.args.get('token') or request.form.get('token')) or token != WEB_SECURITY['access_token']:
            return '', 403
        record_ip_access(request.remote_addr, 'token_success', extract_device_info(request))
        return f(*args, **kwargs)
    return decorated_function

def require_auth(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        cleanup_expired_sessions()
        if (cookie_value := request.cookies.get('elaina_admin_session')):
            is_valid, session_token = verify_cookie_value(cookie_value, 'elaina_cookie_secret_key_2024_v1')
            if is_valid and session_token in valid_sessions and datetime.now() < (session_info := valid_sessions[session_token])['expires']:
                if not WEB_SECURITY.get('production_mode', False) or (session_info.get('ip') == request.remote_addr and session_info.get('user_agent', '')[:200] == request.headers.get('User-Agent', '')[:200]):
                    return f(*args, **kwargs)
                del valid_sessions[session_token]
            elif session_token in valid_sessions:
                del valid_sessions[session_token]
        return render_template('login.html', token=request.args.get('token', ''), web_interface=WEB_INTERFACE)
    return decorated_function

def require_socketio_token(f):
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
        
        cookie_value = request.cookies.get('elaina_admin_session')
        if not cookie_value:
            return False
        
        is_valid, session_token = verify_cookie_value(cookie_value, 'elaina_cookie_secret_key_2024_v1')
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

check_ip_ban = lambda f: functools.wraps(f)(lambda *args, **kwargs: (cleanup_expired_ip_bans(), '', 403)[1] if is_ip_banned(request.remote_addr) else f(*args, **kwargs))

def cleanup_expired_sessions():
    global valid_sessions, _last_session_cleanup
    if (current_time := time.time()) - _last_session_cleanup < 300:
        return
    _last_session_cleanup, current_datetime = current_time, datetime.now()
    for session_token in [s for s, info in valid_sessions.items() if current_datetime >= info['expires']]:
        del valid_sessions[session_token]

limit_session_count = lambda: [valid_sessions.pop(sorted(valid_sessions.items(), key=lambda x: x[1]['created'])[i][0]) for i in range(len(valid_sessions) - 10)] if len(valid_sessions) > 10 else None

class LogHandler:
    def __init__(self, log_type, max_logs=MAX_LOGS):
        self.log_type, self.logs = log_type, deque(maxlen=max_logs)
        self.global_logs = {'received': received_messages, 'plugin': plugin_logs, 'framework': framework_logs, 'error': error_logs}[log_type]
            
    def add(self, content, traceback_info=None, skip_db=False):
        entry = content.copy() if isinstance(content, dict) else {'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'content': content}
        if 'timestamp' not in entry:
            entry['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if traceback_info:
            entry['traceback'] = traceback_info
        self.logs.append(entry)
        self.global_logs.append(entry)
        if not skip_db and LOG_DB_CONFIG.get('enabled'):
            try:
                add_log_to_db(self.log_type, entry)
            except:
                pass
        if socketio:
            try:
                socketio.emit('new_message', {'type': self.log_type, 'data': {k: entry[k] for k in ['timestamp', 'content'] + (['traceback'] if 'traceback' in entry else [])}}, namespace=PREFIX)
            except:
                pass
        return entry

received_handler, plugin_handler, framework_handler, error_handler = (LogHandler(t) for t in ['received', 'plugin', 'framework', 'error'])

@catch_error
def add_display_message(formatted_message, timestamp=None, user_id=None, group_id=None, message_content=None):
    # 支持结构化数据和旧的字符串格式
    if user_id is not None and message_content is not None:
        # 新格式：结构化数据
        entry = {
            'timestamp': timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'content': formatted_message,  # 保留格式化字符串用于兼容
            'user_id': user_id,
            'group_id': group_id or '-',
            'message': message_content
        }
    else:
        # 旧格式：纯字符串
        entry = {'timestamp': timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'content': formatted_message}
    
    received_handler.logs.append(entry)
    received_handler.global_logs.append(entry)
    if socketio:
        try:
            socketio.emit('new_message', {'type': 'received', 'data': entry}, namespace=PREFIX)
        except:
            pass
    return entry

@catch_error
def add_plugin_log(log, user_id=None, group_id=None, plugin_name=None):
    log_data = ({'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'content': log, 'user_id': user_id or '', 
        'group_id': group_id or 'c2c', 'plugin_name': plugin_name or ''} if isinstance(log, str) 
        else {**({'content': str(log)} if not isinstance(log, dict) else log.copy()), 
        'user_id': user_id or '', 'group_id': group_id or 'c2c', 'plugin_name': plugin_name or ''})
    return plugin_handler.add(log_data)

add_framework_log = catch_error(lambda log: framework_handler.add(log))
add_error_log = catch_error(lambda log, traceback_info=None: error_handler.add(log, traceback_info))

@web.route('/login', methods=['POST'])
@check_ip_ban
@require_token
@catch_error
def login():
    password, token = request.form.get('password'), request.form.get('token')
    if password == WEB_SECURITY['admin_password']:
        cleanup_expired_sessions()
        limit_session_count()
        record_ip_access(request.remote_addr, 'password_success', extract_device_info(request))
        session_token, expires = generate_session_token(), datetime.now() + timedelta(days=7)
        valid_sessions[session_token] = {'created': datetime.now(), 'expires': expires, 'ip': request.remote_addr, 'user_agent': request.headers.get('User-Agent', '')[:200]}
        response = make_response(f'<script>window.location.href = "/web/?token={token}";</script>')
        response.set_cookie('elaina_admin_session', sign_cookie_value(session_token, 'elaina_cookie_secret_key_2024_v1'), max_age=604800, httponly=True, secure=False, samesite='Lax', path='/')
        return response
    record_ip_access(request.remote_addr, 'password_fail')
    return render_template('login.html', token=token, error='密码错误，请重试', web_interface=WEB_INTERFACE)

@web.route('/')
@check_ip_ban
@require_token
@require_auth
@catch_error
def index():
    response = make_response(render_template('index.html', prefix=PREFIX, device_type='pc', ROBOT_QQ=ROBOT_QQ, appid=appid, WEBSOCKET_CONFIG=WEBSOCKET_CONFIG, web_interface=WEB_INTERFACE))
    for header, value in [('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'), ('X-XSS-Protection', '1; mode=block'),
        ('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com; font-src 'self' cdn.jsdelivr.net cdnjs.cloudflare.com; img-src 'self' data: *.myqcloud.com thirdqq.qlogo.cn *.qlogo.cn api.2dcode.biz; connect-src 'self' i.elaina.vin"),
        ('Referrer-Policy', 'strict-origin-when-cross-origin'), ('Permissions-Policy', 'geolocation=(), microphone=(), camera=()'),
        ('Strict-Transport-Security', 'max-age=0'), ('Cache-Control', 'no-cache, no-store, must-revalidate'), ('Pragma', 'no-cache'), ('Expires', '0')]:
        response.headers[header] = value
    return response

@web.route('/api/logs/<log_type>')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_logs(log_type):
    page, page_size = request.args.get('page', 1, type=int), request.args.get('size', 50, type=int)
    logs = list({'received': received_messages, 'plugin': plugin_logs, 'framework': framework_logs}.get(log_type, []))
    logs.reverse()
    return jsonify({'logs': logs[(start := (page - 1) * page_size):start + page_size], 'total': len(logs), 'page': page, 
        'page_size': page_size, 'total_pages': (len(logs) + page_size - 1) // page_size}) if log_type in ['received', 'plugin', 'framework'] else (jsonify({'error': '无效的日志类型'}), 400)

@web.route('/status')
@check_ip_ban
@require_token
@require_auth
@catch_error
def status():
    return jsonify({'status': 'ok', 'version': '1.0', 'logs_count': {'received': len(received_messages), 
        'plugin': len(plugin_logs), 'framework': len(framework_logs)}})

def run_statistics_task(task_id, force_refresh=False, selected_date=None):
    try:
        statistics_tasks[task_id] = {'status': 'running', 'progress': 0, 'start_time': time.time(), 'message': '开始统计计算...'}
        if selected_date and selected_date != 'today':
            statistics_tasks[task_id].update({'message': f'查询日期 {selected_date} 的数据...', 'progress': 50})
            if not (date_data := get_specific_date_data(selected_date)):
                statistics_tasks[task_id] = {'status': 'failed', 'error': f'未找到日期 {selected_date} 的数据', 'end_time': time.time()}
                return
            result = {'selected_date_data': date_data, 'date': selected_date}
        else:
            statistics_tasks[task_id].update({'message': '获取历史数据...', 'progress': 30})
            data = get_statistics_data(force_refresh=force_refresh)
            statistics_tasks[task_id].update({'message': '计算性能指标...', 'progress': 80})
            data['performance'] = {'response_time_ms': round((time.time() - statistics_tasks[task_id]['start_time']) * 1000, 2), 
                'timestamp': datetime.now().isoformat(), 'optimized': True, 'async': True}
            result = data
        statistics_tasks[task_id] = {'status': 'completed', 'progress': 100, 'end_time': time.time(), 'message': '统计计算完成'}
        task_results[task_id] = result
        for tid in [t for t, task in statistics_tasks.items() if task.get('end_time', 0) < time.time() - 3600]:
            statistics_tasks.pop(tid, None)
            task_results.pop(tid, None)
    except Exception as e:
        statistics_tasks[task_id] = {'status': 'failed', 'error': str(e), 'end_time': time.time(), 'message': f'统计计算失败: {str(e)}'}

@web.route('/api/statistics')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_statistics():
    force_refresh, selected_date = request.args.get('force_refresh', 'false').lower() == 'true', request.args.get('date')
    async_mode = request.args.get('async', 'true').lower() == 'true'
    if selected_date and selected_date != 'today':
        return api_success_response({'selected_date_data': date_data, 'date': selected_date}) if (date_data := get_specific_date_data(selected_date)) else api_error_response(f'未找到日期 {selected_date} 的数据', 404)
    if not async_mode:
        start_time = time.time()
        data = get_statistics_data(force_refresh=force_refresh)
        data['performance'] = {'response_time_ms': round((time.time() - start_time) * 1000, 2), 'timestamp': datetime.now().isoformat(), 'optimized': True}
        return api_success_response(data)
    task_id = str(uuid.uuid4())
    threading.Thread(target=lambda: run_statistics_task(task_id, force_refresh, selected_date), daemon=True).start()
    return api_success_response({'task_id': task_id, 'status': 'started', 'message': '统计任务已启动，请使用task_id查询进度'})

@web.route('/api/statistics/task/<task_id>')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_statistics_task_status(task_id):
    if task_id not in statistics_tasks:
        return api_error_response('任务不存在或已过期', 404)
    task_info = statistics_tasks[task_id].copy()
    if task_info['status'] == 'completed' and task_id in task_results:
        return api_success_response({'status': 'completed', 'progress': 100, 'data': task_results[task_id], 'task_info': task_info})
    if task_info['status'] == 'failed':
        return api_error_response(task_info.get('error', '未知错误'), 500, task_info=task_info)
    return api_success_response({'status': task_info['status'], 'progress': task_info.get('progress', 0), 
        'message': task_info.get('message', ''), 'elapsed_time': time.time() - task_info.get('start_time', 0)})

@web.route('/api/statistics/tasks')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_all_statistics_tasks():
    current_time = time.time()
    tasks_info = {task_id: {**task.copy(), 'elapsed_time': current_time - task['start_time']} if 'start_time' in task else task.copy() 
        for task_id, task in statistics_tasks.items()}
    return api_success_response({'tasks': tasks_info, 'total_tasks': len(tasks_info), 
        'active_tasks': len([t for t in tasks_info.values() if t['status'] == 'running'])})

@web.route('/api/complete_dau', methods=['POST'])
@check_ip_ban
@require_token
@catch_error
def complete_dau():
    return api_success_response(result=complete_dau_data())

@web.route('/api/get_nickname/<user_id>')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_user_nickname(user_id):
    return api_success_response(nickname=fetch_user_nickname(user_id), user_id=user_id)

@web.route('/api/available_dates')
@check_ip_ban
@require_token
@require_auth
@catch_error
def get_available_dates():
    return api_success_response(dates=get_available_dau_dates())

@web.route('/api/robot_info')
def get_robot_info():
    try:
        robot_share_url, is_websocket = f"https://qun.qq.com/qunpro/robot/qunshare?robot_uin={ROBOT_QQ}", WEBSOCKET_CONFIG.get('enabled', False)
        connection_type, connection_status = ('WebSocket', get_websocket_status()) if is_websocket else ('WebHook', 'WebHook')
        response = requests.get(f"https://qun.qq.com/qunpro/robot/proxy/domain/qun.qq.com/cgi-bin/group_pro/robot/manager/share_info?bkn=508459323&robot_appid={appid}",
            headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 15; PJX110 Build/UKQ1.231108.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/135.0.7049.111 Mobile Safari/537.36 V1_AND_SQ_9.1.75_10026_HDBM_T PA QQ/9.1.75.25965 NetType/WIFI WebP/0.4.1 AppId/537287845 Pixel/1080 StatusBarHeight/120 SimpleUISwitch/0 QQTheme/1000 StudyMode/0 CurrentMode/0 CurrentFontScale/0.87 GlobalDensityScale/0.9028571 AllowLandscape/false InMagicWin/0',
                'qname-service': '976321:131072', 'qname-space': 'Production'}, timeout=10)
        response.raise_for_status()
        api_response = response.json()
        
        if api_response.get('retcode') != 0:
            error_msg = api_response.get('msg', 'Unknown error')
            raise Exception(f"API返回错误: {error_msg}")
        
        robot_data = api_response.get('data', {}).get('robot_data', {})
        commands = api_response.get('data', {}).get('commands', [])
        
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
            'commands_count': len(commands),
            'is_sharable': robot_data.get('is_sharable', False),
            'service_note': robot_data.get('service_note', ''),
            'qr_code_api': f'/web/api/robot_qrcode?url={robot_share_url}'
        })
        
    except Exception as e:
        # 如果变量未定义，使用默认值
        try:
            robot_share_url_safe = robot_share_url
        except NameError:
            robot_share_url_safe = f"https://qun.qq.com/qunpro/robot/qunshare?robot_uin={ROBOT_QQ}"
        
        try:
            connection_type_safe = connection_type
        except NameError:
            is_websocket = WEBSOCKET_CONFIG.get('enabled', False)
            connection_type_safe = 'WebSocket' if is_websocket else 'WebHook'
        
        try:
            connection_status_safe = connection_status
        except NameError:
            is_websocket = WEBSOCKET_CONFIG.get('enabled', False)
            connection_status_safe = get_websocket_status() if is_websocket else 'WebHook'
        
        return jsonify({
            'success': False,
            'error': str(e),
            'qq': ROBOT_QQ,
            'name': '加载失败',
            'description': '无法获取机器人信息',
            'avatar': '',
            'appid': appid,
            'developer': '未知',
            'link': robot_share_url_safe,
            'status': '未知',
            'connection_type': connection_type_safe,
            'connection_status': connection_status_safe,
            'data_source': 'fallback',
            'qr_code_api': f'/web/api/robot_qrcode?url={robot_share_url_safe}'
        })

@web.route('/api/robot_qrcode')
@catch_error
def get_robot_qrcode():
    if not (url := request.args.get('url')):
        return api_error_response('缺少URL参数', 400)
    response = requests.get(f"https://api.2dcode.biz/v1/create-qr-code?data={url}", timeout=10)
    response.raise_for_status()
    return response.content, 200, {'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=3600'}

@web.route('/api/changelog')
@catch_error
def get_changelog():
    try:
        commits = requests.get("https://i.elaina.vin/api/elainabot/", timeout=10).json()
        return jsonify({'success': True, 'data': [{'sha': commit.get('sha', '')[:8], 'message': commit_info.get('message', '').strip(),
            'author': author.get('name', '未知作者'), 'date': (dt := datetime.fromisoformat(date_str.replace('Z', '+00:00'))).strftime('%Y-%m-%d %H:%M:%S') if (date_str := author.get('date', '')) else '未知时间',
            'url': commit.get('html_url', ''), 'full_sha': commit.get('sha', '')} 
            for commit in commits if (commit_info := commit.get('commit')) and (author := commit_info.get('author', {}))], 'total': len(commits)})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取更新日志失败: {str(e)}'}), 500

@web.route('/api/config/get')
@require_token
@require_auth
@catch_error
def get_config():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_new_path, config_path = os.path.join(base_dir, 'web', 'config_new.py'), os.path.join(base_dir, 'config.py')
    if os.path.exists(config_new_path):
        target_path, is_new = config_new_path, True
    elif os.path.exists(config_path):
        target_path, is_new = config_path, False
    else:
        return jsonify({'success': False, 'message': '配置文件不存在'}), 404
    with open(target_path, 'r', encoding='utf-8') as f:
        return jsonify({'success': True, 'content': f.read(), 'is_new': is_new, 'source': 'config_new.py' if is_new else 'config.py'})

@web.route('/api/config/parse')
@require_token
@require_auth
@catch_error
def parse_config():
    """解析配置文件，提取配置项（优先读取 config_new.py）"""
    try:
        import os
        import re
        import ast
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # 优先读取 web/config_new.py，如果不存在则读取 config.py
        config_new_path = os.path.join(base_dir, 'web', 'config_new.py')
        config_path = os.path.join(base_dir, 'config.py')
        
        if os.path.exists(config_new_path):
            target_path = config_new_path
        elif os.path.exists(config_path):
            target_path = config_path
        else:
            return jsonify({
                'success': False,
                'message': '配置文件不存在'
            }), 404
        
        with open(target_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析配置项
        config_items = []
        lines = content.split('\n')
        current_dict = None  # 当前正在解析的字典名称
        dict_indent = 0      # 字典的缩进级别
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # 跳过空行、导入语句和文档字符串
            if not stripped or stripped.startswith('"""') or stripped.startswith("'''") or stripped.startswith('import ') or stripped.startswith('from '):
                continue
            
            # 如果是注释行，只处理作为section标题的注释（后面会有相关配置）
            if stripped.startswith('#'):
                continue
            
            # 检测字典的开始: VAR_NAME = {
            dict_start_pattern = r'^([A-Z_][A-Z0-9_]*)\s*=\s*\{(.*)$'
            dict_match = re.match(dict_start_pattern, stripped)
            if dict_match:
                current_dict = dict_match.group(1)
                dict_indent = len(line) - len(line.lstrip())
                # 如果是单行字典定义（如 VAR = {}），则不进入字典解析模式
                if dict_match.group(2).strip() == '}':
                    current_dict = None
                continue
            
            # 检测字典的结束: }
            if current_dict and stripped == '}':
                current_dict = None
                continue
            
            # 在字典内部，解析键值对
            if current_dict:
                # 匹配字典内的键值对: 'key': value,  # 可选注释
                dict_item_pattern = r"^['\"]?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?\s*:\s*(.+?)(?:,\s*)?(?:#\s*(.+))?$"
                dict_item_match = re.match(dict_item_pattern, stripped)
                if dict_item_match:
                    key_name = dict_item_match.group(1)
                    value_str = dict_item_match.group(2).strip().rstrip(',').strip()
                    inline_comment = dict_item_match.group(3).strip() if dict_item_match.group(3) else ''
                    
                    # 解析值
                    try:
                        parsed_value = ast.literal_eval(value_str)
                        
                        # 确定类型
                        if isinstance(parsed_value, bool):
                            value_type = 'boolean'
                            value = parsed_value
                        elif isinstance(parsed_value, (int, float)):
                            value_type = 'number'
                            value = parsed_value
                        elif isinstance(parsed_value, str):
                            value_type = 'string'
                            value = parsed_value
                        elif isinstance(parsed_value, list):
                            if all(isinstance(item, str) for item in parsed_value):
                                value_type = 'list'
                                value = parsed_value
                            else:
                                continue
                        else:
                            continue
                        
                        config_items.append({
                            'name': f"{current_dict}.{key_name}",
                            'dict_name': current_dict,
                            'key_name': key_name,
                            'value': value,
                            'type': value_type,
                            'comment': inline_comment,
                            'line': i,
                            'is_dict_item': True
                        })
                    except (ValueError, SyntaxError):
                        continue
                continue
            
            # 识别简单赋值（字符串、数字、布尔值）
            # 匹配: VAR_NAME = value  # 可选注释
            simple_pattern = r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+?)(?:\s*#\s*(.+))?$'
            match = re.match(simple_pattern, stripped)
            
            if match:
                var_name = match.group(1)
                value_str = match.group(2).strip()
                inline_comment = match.group(3).strip() if match.group(3) else ''
                
                # 跳过字典和列表的开始
                if value_str.endswith('{') or value_str.endswith('[') or value_str == '{' or value_str == '[':
                    continue
                
                # 使用ast安全解析值
                try:
                    # 尝试使用ast.literal_eval解析值
                    parsed_value = ast.literal_eval(value_str)
                    
                    # 确定类型
                    if isinstance(parsed_value, bool):
                        value_type = 'boolean'
                        value = parsed_value
                    elif isinstance(parsed_value, (int, float)):
                        value_type = 'number'
                        value = parsed_value
                    elif isinstance(parsed_value, str):
                        value_type = 'string'
                        value = parsed_value
                    elif isinstance(parsed_value, list):
                        # 只处理简单的字符串列表
                        if all(isinstance(item, str) for item in parsed_value):
                            value_type = 'list'
                            value = parsed_value
                        else:
                            continue
                    else:
                        continue
                    
                    config_items.append({
                        'name': var_name,
                        'value': value,
                        'type': value_type,
                        'comment': inline_comment,
                        'line': i,
                        'is_dict_item': False
                    })
                except (ValueError, SyntaxError):
                    # 如果ast解析失败，尝试简单处理
                    # 可能是f-string或其他复杂表达式，跳过
                    continue
        
        # 确定配置文件来源
        is_new = os.path.exists(config_new_path) and target_path == config_new_path
        source = 'config_new.py' if is_new else 'config.py'
        
        return jsonify({
            'success': True,
            'items': config_items,
            'is_new': is_new,
            'source': source
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'解析配置文件失败: {str(e)}'
        }), 500

@web.route('/api/config/update_items', methods=['POST'])
@require_token
@require_auth
@catch_error
def update_config_items():
    """根据表单更新配置项"""
    try:
        import os
        import re
        
        data = request.get_json()
        if not data or 'items' not in data:
            return jsonify({
                'success': False,
                'message': '缺少配置项数据'
            }), 400
        
        items = data['items']
        
        # 读取配置文件（优先读取 config_new.py）
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_new_path = os.path.join(base_dir, 'web', 'config_new.py')
        config_path = os.path.join(base_dir, 'config.py')
        
        if os.path.exists(config_new_path):
            target_path = config_new_path
        else:
            target_path = config_path
        
        with open(target_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # 更新配置项
        for item in items:
            var_name = item['name']
            new_value = item['value']
            value_type = item['type']
            is_dict_item = item.get('is_dict_item', False)
            
            # 格式化新值
            formatted_value = (f'"{new_value}"' if value_type == 'string' else
                             'True' if (value_type == 'boolean' and new_value) else 'False' if value_type == 'boolean' else
                             str(new_value) if value_type == 'number' else
                             '[' + ', '.join([f'"{item}"' for item in new_value]) + ']' if (value_type == 'list' and isinstance(new_value, list)) else
                             '[]' if value_type == 'list' else str(new_value))
            
            # 在文件中查找并替换，保留行尾注释
            if is_dict_item:
                # 字典项：需要在正确的字典内匹配
                dict_name = item.get('dict_name', '')
                key_name = item.get('key_name', '')
                
                # 先找到字典的定义行
                dict_start_pattern = rf'^({re.escape(dict_name)})\s*=\s*\{{'
                in_target_dict = False
                dict_depth = 0
                
                for i, line in enumerate(lines):
                    # 检测目标字典的开始
                    if re.match(dict_start_pattern, line.strip()):
                        in_target_dict = True
                        dict_depth = 1
                        continue
                    
                    # 如果在目标字典内
                    if in_target_dict:
                        # 跟踪嵌套层级
                        dict_depth += line.count('{')
                        dict_depth -= line.count('}')
                        
                        # 如果字典已经结束
                        if dict_depth == 0:
                            in_target_dict = False
                            break
                        
                        # 在字典内匹配键值对
                        pattern = rf"^(\s*)['\"]?({re.escape(key_name)})['\"]?\s*:\s*(.+?)(?:,\s*)?(\s*#.+)?$"
                        match = re.match(pattern, line)
                        if match:
                            indent = match.group(1)
                            comment = match.group(4) if match.group(4) else ''
                            
                            # 计算对齐空格
                            if comment:
                                value_part = f"'{key_name}': {formatted_value},"
                                original_value_part = f"'{match.group(2)}': {match.group(3)},"
                                spaces_count = len(original_value_part) - len(value_part)
                                if spaces_count < 2:
                                    spaces_count = 2
                                spacing = ' ' * spaces_count
                                # 保留注释，确保有至少一个空格分隔
                                clean_comment = comment.strip()
                                if not clean_comment.startswith('#'):
                                    clean_comment = '# ' + clean_comment
                                lines[i] = f'{indent}{value_part}{spacing}{clean_comment}\n'
                            else:
                                lines[i] = f"{indent}'{key_name}': {formatted_value},\n"
                            break
            else:
                # 简单变量：匹配 VAR_NAME = value
                pattern = rf'^(\s*)({re.escape(var_name)})\s*=\s*(.+?)(\s*#.+)?$'
                for i, line in enumerate(lines):
                    match = re.match(pattern, line)
                    if match:
                        indent = match.group(1)
                        comment = match.group(4) if match.group(4) else ''
                        
                        # 计算对齐空格（保持美观）
                        if comment:
                            # 保持原有的空格数量，或者至少20个字符的对齐
                            value_part = f'{var_name} = {formatted_value}'
                            # 计算需要的空格数量以保持对齐
                            original_value_part = match.group(2) + ' = ' + match.group(3)
                            spaces_count = len(original_value_part) - len(value_part)
                            if spaces_count < 2:
                                spaces_count = 2  # 至少2个空格
                            spacing = ' ' * spaces_count
                            # 保留注释，确保有至少一个空格分隔
                            clean_comment = comment.strip()
                            if not clean_comment.startswith('#'):
                                clean_comment = '# ' + clean_comment
                            lines[i] = f'{indent}{value_part}{spacing}{clean_comment}\n'
                        else:
                            lines[i] = f'{indent}{var_name} = {formatted_value}\n'
                        break
        
        # 生成新配置内容
        new_content = ''.join(lines)
        
        # 验证语法
        try:
            compile(new_content, '<string>', 'exec')
        except SyntaxError as e:
            return jsonify({
                'success': False,
                'message': f'配置文件语法错误: 第{e.lineno}行 - {e.msg}'
            }), 400
        
        # 保存到 web/config_new.py
        web_dir = os.path.dirname(os.path.abspath(__file__))
        config_new_path = os.path.join(web_dir, 'config_new.py')
        
        with open(config_new_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        return jsonify({
            'success': True,
            'message': '配置已保存，请重启框架以应用更改',
            'file_path': config_new_path
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'更新配置失败: {str(e)}'
        }), 500

@web.route('/api/config/save', methods=['POST'])
@require_token
@require_auth
@catch_error
def save_config():
    if not (data := request.get_json()) or 'content' not in data:
        return jsonify({'success': False, 'message': '缺少配置内容'}), 400
    try:
        compile(data['content'], '<string>', 'exec')
    except SyntaxError as e:
        return jsonify({'success': False, 'message': f'配置文件语法错误: 第{e.lineno}行 - {e.msg}'}), 400
    config_new_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config_new.py')
    with open(config_new_path, 'w', encoding='utf-8') as f:
        f.write(data['content'])
    return jsonify({'success': True, 'message': '配置文件已保存，请重启框架以应用更改', 'file_path': config_new_path})

@web.route('/api/config/check_pending')
@require_token
@require_auth
@catch_error
def check_pending_config():
    config_new_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config_new.py')
    exists = os.path.exists(config_new_path)
    modified_time = datetime.fromtimestamp(os.path.getmtime(config_new_path)).strftime('%Y-%m-%d %H:%M:%S') if exists else None
    return jsonify({'success': True, 'pending': exists, 'modified_time': modified_time})

@web.route('/api/config/cancel_pending', methods=['POST'])
@require_token
@require_auth
@catch_error
def cancel_pending_config():
    config_new_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config_new.py')
    if os.path.exists(config_new_path):
        os.remove(config_new_path)
        return jsonify({'success': True, 'message': '已取消待应用的配置'})
    return jsonify({'success': False, 'message': '没有待应用的配置文件'}), 404

@web.route('/api/ai/generate_plugin', methods=['POST'])
@check_ip_ban
@require_token
@require_auth
@catch_error
def ai_generate_plugin():
    """AI生成插件代码"""
    data = request.get_json()
    prompt = data.get('prompt', '').strip()
    filename = data.get('filename', '').strip()
    
    if not prompt:
        return jsonify({'success': False, 'message': '请输入需求描述'}), 400
    
    if not filename or not filename.endswith('.py'):
        return jsonify({'success': False, 'message': '文件名必须以 .py 结尾'}), 400
    
    try:
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        example_plugin_path = os.path.join(script_dir, 'plugins', 'example', '示例插件（可喂给AI）.py')
        
        example_code = ''
        if os.path.exists(example_plugin_path):
            with open(example_plugin_path, 'r', encoding='utf-8') as f:
                example_code = f.read()
        
        # 构建提示词
        ai_prompt = f"""参考示例插件代码：
```python
{example_code}
```

关键规范：
- 继承 Plugin 类
- get_regex_handlers() 返回字典
- @staticmethod 装饰器
- 不要用 async/await
- 数据库用 MySQL（%s 占位符）

用户需求：{prompt}

直接输出完整的Python代码，不要解释，不要用markdown包裹："""
        
        # 调用 AI API
        response = requests.post(
            'https://i.elaina.vin/api/ai.php',
            json={'text': ai_prompt},
            headers={'Content-Type': 'application/json'},
            timeout=60
        )
        
        result = response.json()
        
        if result.get('status') == 'success' and result.get('response'):
            code = result['response']
            code = re.sub(r'^```(?:python)?\s*\n', '', code)
            code = re.sub(r'\n```\s*$', '', code)
            generated_code = code.strip()
            
            return jsonify({
                'success': True,
                'code': generated_code,
                'message': '代码生成成功'
            })
        else:
            error_msg = result.get('message', 'AI 调用失败')
            return jsonify({'success': False, 'message': error_msg}), 500
        
    except Exception as e:
        error_msg = f"生成插件失败: {str(e)}"
        add_error_log(error_msg, traceback.format_exc())
        return jsonify({'success': False, 'message': error_msg}), 500

@web.route('/api/ai/save_plugin', methods=['POST'])
@check_ip_ban
@require_token
@require_auth
@catch_error
def ai_save_plugin():
    """保存 AI 生成的插件"""
    data = request.get_json()
    filename = data.get('filename', '').strip()
    code = data.get('code', '').strip()
    
    if not filename or not filename.endswith('.py'):
        return jsonify({'success': False, 'message': '无效的文件名'}), 400
    
    if not code:
        return jsonify({'success': False, 'message': '代码内容不能为空'}), 400
    
    try:
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ai_plugins_dir = os.path.join(script_dir, 'plugins', 'ai')
        os.makedirs(ai_plugins_dir, exist_ok=True)
        
        target_path = os.path.join(ai_plugins_dir, filename)
        
        if os.path.exists(target_path):
            return jsonify({'success': False, 'message': '文件已存在，请更换文件名'}), 409
        
        with open(target_path, 'w', encoding='utf-8') as f:
            f.write(code)
        
        add_framework_log(f"AI 生成插件已保存: {filename}")
        
        return jsonify({
            'success': True,
            'message': '插件已保存',
            'path': f'plugins/ai/{filename}'
        })
        
    except Exception as e:
        error_msg = f"保存插件失败: {str(e)}"
        add_error_log(error_msg, traceback.format_exc())
        return jsonify({'success': False, 'message': error_msg}), 500

@web.route('/api/plugin/toggle', methods=['POST'])
@check_ip_ban
@require_token
@require_auth
@catch_error
def toggle_plugin():
    """启用或禁用插件"""
    data = request.get_json()
    plugin_path = data.get('path')
    action = data.get('action')  # 'enable' 或 'disable'
    
    if not plugin_path or not action:
        return jsonify({'success': False, 'message': '缺少必要参数'}), 400
    
    if action not in ['enable', 'disable']:
        return jsonify({'success': False, 'message': '无效的操作类型'}), 400
    
    # 安全检查：确保路径在 plugins 目录下
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plugins_dir = os.path.join(script_dir, 'plugins')
    abs_plugin_path = os.path.abspath(plugin_path)
    
    if not abs_plugin_path.startswith(os.path.abspath(plugins_dir)):
        return jsonify({'success': False, 'message': '无效的插件路径'}), 403
    
    try:
        if action == 'disable':
            # 禁用插件：将 .py 改为 .py.ban
            if not plugin_path.endswith('.py'):
                return jsonify({'success': False, 'message': '只能禁用 .py 文件'}), 400
            
            new_path = plugin_path + '.ban'
            if os.path.exists(new_path):
                return jsonify({'success': False, 'message': '禁用文件已存在'}), 409
            
            os.rename(plugin_path, new_path)
            add_framework_log(f"插件已禁用: {os.path.basename(plugin_path)}")
            
            return jsonify({
                'success': True,
                'message': '插件已禁用',
                'new_path': new_path
            })
            
        elif action == 'enable':
            # 启用插件：将 .py.ban 改为 .py
            if not plugin_path.endswith('.py.ban'):
                return jsonify({'success': False, 'message': '只能启用 .py.ban 文件'}), 400
            
            new_path = plugin_path[:-4]  # 移除 .ban
            if os.path.exists(new_path):
                return jsonify({'success': False, 'message': '启用文件已存在'}), 409
            
            os.rename(plugin_path, new_path)
            add_framework_log(f"插件已启用: {os.path.basename(new_path)}")
            
            return jsonify({
                'success': True,
                'message': '插件已启用',
                'new_path': new_path
            })
            
    except PermissionError:
        return jsonify({'success': False, 'message': '没有权限操作该文件'}), 403
    except FileNotFoundError:
        return jsonify({'success': False, 'message': '文件不存在'}), 404
    except Exception as e:
        error_msg = f"操作插件失败: {str(e)}"
        add_error_log(error_msg, traceback.format_exc())
        return jsonify({'success': False, 'message': error_msg}), 500

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
        
        # 获取CPU型号
        cpu_model = "未知处理器"
        try:
            import platform
            system_type = platform.system()
            
            if system_type == 'Windows':
                # Windows系统：从注册表读取
                try:
                    import winreg
                    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
                    cpu_model = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
                    winreg.CloseKey(key)
                except:
                    pass
                        
            elif system_type == 'Linux':
                # Linux系统：从/proc/cpuinfo读取
                try:
                    with open('/proc/cpuinfo', 'r') as f:
                        for line in f:
                            if 'model name' in line.lower():
                                cpu_model = line.split(':', 1)[1].strip()
                                break
                except:
                    pass
        except:
            pass
        
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
            'cpu_model': cpu_model,
            
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
            'cpu_model': '未知处理器',
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
            'boot_time': (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d %H:%M:%S'),
            'system_version': 'Windows 10 64-bit'
        }

@catch_error
def process_plugin_module(module, plugin_path, module_name, is_system=False, dir_name=None):
    plugin_info_list, plugin_classes_found = [], False
    last_modified_str = datetime.fromtimestamp(os.path.getmtime(plugin_path)).strftime('%Y-%m-%d %H:%M:%S') if os.path.exists(plugin_path) else ""
    for attr_name in dir(module):
        if attr_name.startswith('__') or not hasattr((attr := getattr(module, attr_name)), '__class__'):
            continue
        if isinstance(attr, type) and attr.__module__ == module.__name__ and hasattr(attr, 'get_regex_handlers'):
            plugin_classes_found = True
            name = f"{'system' if is_system else dir_name}/{module_name}/{attr_name}"
            plugin_info = {'name': name, 'class_name': attr_name, 'status': 'loaded', 'error': '', 'path': plugin_path,
                'is_system': is_system, 'directory': dir_name, 'last_modified': last_modified_str}
            try:
                handlers = attr.get_regex_handlers()
                plugin_info.update({'handlers': len(handlers) if handlers else 0, 'handlers_list': list(handlers.keys()) if handlers else [],
                    'priority': getattr(attr, 'priority', 10), 
                    'handlers_owner_only': {p: (h.get('owner_only', False) if isinstance(h, dict) else False) for p, h in handlers.items()},
                    'handlers_group_only': {p: (h.get('group_only', False) if isinstance(h, dict) else False) for p, h in handlers.items()}})
            except Exception as e:
                plugin_info.update({'status': 'error', 'error': f"获取处理器失败: {str(e)}", 'traceback': traceback.format_exc()})
            plugin_info_list.append(plugin_info)
    if not plugin_classes_found:
        plugin_info_list.append({'name': f"{'system/' if is_system else ''}{dir_name}/{module_name}", 'class_name': 'unknown',
            'status': 'error', 'error': '未在模块中找到有效的插件类', 'path': plugin_path, 'directory': dir_name, 'last_modified': last_modified_str})
    return plugin_info_list

@catch_error
def load_plugin_module(plugin_file, module_name, is_system=False):
    try:
        dir_name = os.path.basename(os.path.dirname(plugin_file))
        if not (spec := importlib.util.spec_from_file_location(f"plugins.{dir_name}.{module_name}", plugin_file)) or not spec.loader:
            return [{'name': f"{dir_name}/{module_name}", 'class_name': 'unknown', 'status': 'error', 
                'error': '无法加载插件文件', 'path': plugin_file, 'directory': dir_name}]
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return process_plugin_module(module, plugin_file, module_name, is_system=is_system, dir_name=dir_name)
    except Exception as e:
        return [{'name': f"{os.path.basename(os.path.dirname(plugin_file))}/{module_name}", 'class_name': 'unknown',
            'status': 'error', 'error': str(e), 'path': plugin_file, 
            'directory': os.path.basename(os.path.dirname(plugin_file)), 'traceback': traceback.format_exc()}]

@catch_error
def scan_plugins():
    """扫描所有插件并获取其状态（包括已禁用的 .py.ban 文件）"""
    global plugins_info
    plugins_info = []
    
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    plugins_dir = os.path.join(script_dir, 'plugins')
    
    for dir_name in os.listdir(plugins_dir):
        plugin_dir = os.path.join(plugins_dir, dir_name)
        if os.path.isdir(plugin_dir):
            # 扫描 .py 文件（已启用的插件）
            py_files = [f for f in os.listdir(plugin_dir) if f.endswith('.py') and f != '__init__.py']
            
            for py_file in py_files:
                plugin_file = os.path.join(plugin_dir, py_file)
                plugin_name = os.path.splitext(py_file)[0]
                
                plugin_info_list = load_plugin_module(
                    plugin_file, 
                    plugin_name,
                    is_system=(dir_name == 'system')
                )
                
                # 标记为已启用
                for plugin_info in plugin_info_list:
                    plugin_info['enabled'] = True
                
                plugins_info.extend(plugin_info_list)
            
            # 扫描 .py.ban 文件（已禁用的插件）
            ban_files = [f for f in os.listdir(plugin_dir) if f.endswith('.py.ban')]
            
            for ban_file in ban_files:
                plugin_file = os.path.join(plugin_dir, ban_file)
                plugin_name = os.path.splitext(os.path.splitext(ban_file)[0])[0]  # 移除 .py.ban
                last_modified_str = datetime.fromtimestamp(os.path.getmtime(plugin_file)).strftime('%Y-%m-%d %H:%M:%S')
                
                # 为禁用的插件创建信息条目
                plugins_info.append({
                    'name': f"{dir_name}/{plugin_name}",
                    'class_name': 'unknown',
                    'status': 'disabled',
                    'error': '插件已禁用',
                    'path': plugin_file,
                    'is_system': (dir_name == 'system'),
                    'directory': dir_name,
                    'last_modified': last_modified_str,
                    'enabled': False,
                    'handlers': 0,
                    'handlers_list': []
                })
    
    # 按状态排序：loaded -> disabled -> error
    plugins_info.sort(key=lambda x: (0 if x['status'] == 'loaded' else (1 if x['status'] == 'disabled' else 2)))
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

    @sio.on('get_plugins_info', namespace=PREFIX)
    @require_socketio_token
    def handle_get_plugins_info():
        plugins = scan_plugins()
        sio.emit('plugins_update', plugins, room=request.sid, namespace=PREFIX)

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
    global historical_data_cache, historical_cache_loaded, today_data_cache, today_cache_time
    current_time, current_date = time.time(), datetime.now().date()
    today_cache_valid = not force_refresh and today_data_cache is not None and current_time - today_cache_time < TODAY_CACHE_DURATION
    try:
        if not historical_cache_loaded or historical_data_cache is None:
            historical_data_cache = load_historical_dau_data_optimized()
            historical_cache_loaded = True
        if not today_cache_valid:
            today_data_cache = get_today_dau_data(force_refresh)
            today_cache_time = current_time
        return {'historical': historical_data_cache, 'today': today_data_cache, 'cache_time': current_time,
            'cache_date': current_date.strftime('%Y-%m-%d'), 'cache_info': {'historical_permanently_cached': historical_cache_loaded,
            'today_cached': today_cache_valid, 'today_cache_age': current_time - today_cache_time if today_data_cache else 0,
            'historical_count': len(historical_data_cache) if historical_data_cache else 0}}
    except Exception as e:
        add_error_log(f"获取统计数据失败: {str(e)}")
        return {'historical': [], 'today': {}, 'cache_time': current_time, 'error': str(e)}

def load_historical_dau_data():
    """原始的历史数据加载函数（从数据库加载）"""
    return load_historical_dau_data_from_database()

def load_historical_dau_data_from_database():
    """从数据库加载历史DAU数据"""
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
        
        # 从数据库加载历史数据
        for i in range(1, 31):
            target_date = today - timedelta(days=i)
            
            try:
                # 从数据库读取DAU数据
                dau_data = dau_analytics.load_dau_data(target_date)
                
                if dau_data:
                    display_date = target_date.strftime('%m-%d')
                    dau_data['display_date'] = display_date
                    
                    # 确保事件统计数据存在
                    if 'event_stats' not in dau_data:
                        dau_data['event_stats'] = {
                            'group_join_count': 0,
                            'group_leave_count': 0,
                            'friend_add_count': 0,
                            'friend_remove_count': 0
                        }
                    
                    historical_data.append(dau_data)
            except Exception as e:
                add_error_log(f"加载历史DAU数据失败 {target_date.strftime('%Y-%m-%d')}: {str(e)}")
                continue
        
        # 按日期排序
        historical_data.sort(key=lambda x: x.get('date', ''))
        
        return historical_data
        
    except Exception as e:
        add_error_log(f"从数据库加载历史DAU数据失败: {str(e)}")
        # 如果数据库加载失败，回退到文件加载
        return load_historical_dau_data_fallback()

def load_historical_dau_data_optimized():
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        import concurrent.futures
        dau_analytics, today = get_dau_analytics(), datetime.now()
        def load_single_day_data(days_ago):
            try:
                if dau_data := dau_analytics.load_dau_data(target_date := today - timedelta(days=days_ago)):
                    dau_data['display_date'] = target_date.strftime('%m-%d')
                    return dau_data
            except:
                pass
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            historical_data = [r for f in concurrent.futures.as_completed({executor.submit(load_single_day_data, i): i for i in range(1, 31)}) if (r := f.result(timeout=5))]
        historical_data.sort(key=lambda x: x.get('date', ''))
        return historical_data
    except Exception as e:
        add_error_log(f"加载优化历史DAU数据失败: {str(e)}")
        return load_historical_dau_data_fallback()

def load_historical_dau_data_fallback():
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        dau_analytics, today, historical_data = get_dau_analytics(), datetime.now(), []
        for i in range(1, 31):
            try:
                if dau_data := dau_analytics.load_dau_data(target_date := today - timedelta(days=i)):
                    dau_data['display_date'] = target_date.strftime('%m-%d')
                    historical_data.append(dau_data)
            except:
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
        
        # 今日数据混合模式：基础统计实时计算，事件统计从DAU表读取
        today_data = None
        
        if not force_refresh:
            # 先尝试实时收集今日基础数据
            try:
                today_data = dau_analytics.collect_dau_data(today)
                if today_data:
                    today_data['is_realtime'] = True
                    today_data['cache_time'] = time.time()
                    
                    # 从DAU表读取事件统计数据并合并
                    try:
                        dau_data = dau_analytics.load_dau_data(today)
                        if dau_data and 'event_stats' in dau_data:
                            today_data['event_stats'] = dau_data['event_stats']
                        elif 'event_stats' not in today_data:
                            today_data['event_stats'] = {
                                'group_join_count': 0,
                                'group_leave_count': 0,
                                'friend_add_count': 0,
                                'friend_remove_count': 0
                            }
                    except Exception as e:
                        # 如果DAU表读取失败，使用默认事件统计
                        if 'event_stats' not in today_data:
                            today_data['event_stats'] = {
                                'group_join_count': 0,
                                'group_leave_count': 0,
                                'friend_add_count': 0,
                                'friend_remove_count': 0
                            }
                        
            except Exception as e:
                # 如果实时收集失败，尝试完全从数据库读取
                try:
                    today_data = dau_analytics.load_dau_data(today)
                    if today_data:
                        today_data['from_database'] = True
                except Exception:
                    pass
        
        if not today_data:
            # 最终回退到空数据结构
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
                'event_stats': {
                    'group_join_count': 0,
                    'group_leave_count': 0,
                    'friend_add_count': 0,
                    'friend_remove_count': 0
                },
                'error': 'No data available'
            }
        
        # 确保事件统计数据存在
        if today_data and 'event_stats' not in today_data:
            today_data['event_stats'] = {
                'group_join_count': 0,
                'group_leave_count': 0,
                'friend_add_count': 0,
                'friend_remove_count': 0
            }
        
        return today_data or {}
        
    except Exception as e:
        add_error_log(f"获取今日DAU数据失败: {str(e)}")
        return {
            'message_stats': {},
            'user_stats': {},
            'command_stats': [],
            'event_stats': {
                'group_join_count': 0,
                'group_leave_count': 0,
                'friend_add_count': 0,
                'friend_remove_count': 0
            },
            'error': str(e)
        }

def start_web(main_app=None, is_subprocess=False):
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
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        dau_analytics, today = get_dau_analytics(), datetime.now()
        missing_dates = [target_date for i in range(1, 31) if not dau_analytics.load_dau_data(target_date := today - timedelta(days=i))]
        if not missing_dates:
            return {'generated_count': 0, 'failed_count': 0, 'total_missing': 0, 'generated_dates': [], 
                'failed_dates': [], 'message': '近30天DAU数据完整，无需补全'}
        generated_dates, failed_dates = [], []
        for target_date in missing_dates:
            try:
                (generated_dates if dau_analytics.manual_generate_dau(target_date) else failed_dates).append(target_date.strftime('%Y-%m-%d'))
            except:
                    failed_dates.append(target_date.strftime('%Y-%m-%d'))
        return {'generated_count': len(generated_dates), 'failed_count': len(failed_dates), 'total_missing': len(missing_dates),
            'generated_dates': generated_dates, 'failed_dates': failed_dates,
            'message': f'检测到{len(missing_dates)}天的DAU数据缺失，成功生成{len(generated_dates)}天，失败{len(failed_dates)}天'}
    except Exception as e:
        raise Exception(f"补全DAU数据失败: {str(e)}")

def fetch_user_nickname(user_id):
    try:
        if not user_id or len(user_id) < 3:
            return None
        from config import appid
        if (response := requests.get(f"https://i.elaina.vin/api/bot/xx.php?openid={user_id}&appid={appid}", timeout=3)).status_code == 200:
            if (nickname := response.json().get('名字', '').strip()) and nickname != user_id and 0 < len(nickname) <= 20:
                return nickname
    except:
        pass
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
        event = MessageEvent(mock_data, skip_recording=True)  # 沙盒测试不记录到数据库
        
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
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        dau_analytics, today = get_dau_analytics(), datetime.now().date()
        available_dates = []
        for i in range(31):
            if (check_date := today - timedelta(days=i)) >= today - timedelta(days=30):
                try:
                    if dau_analytics.load_dau_data(datetime.combine(check_date, datetime.min.time())):
                        is_today, date_str = check_date == today, check_date.strftime('%Y-%m-%d')
                        available_dates.append({'value': 'today' if is_today else date_str, 'date': date_str,
                            'display': "今日数据" if is_today else f"{check_date.strftime('%m-%d')} ({date_str})", 'is_today': is_today})
                except:
                    continue
        if not any(item['is_today'] for item in available_dates):
            available_dates.append({'value': 'today', 'date': today.strftime('%Y-%m-%d'), 'display': '今日数据', 'is_today': True})
        available_dates.sort(key=lambda x: (not x['is_today'], -int(x['date'].replace('-', ''))))
        return available_dates
    except Exception as e:
        add_error_log(f"获取可用DAU日期失败: {str(e)}")
        return [{'value': 'today', 'date': datetime.now().strftime('%Y-%m-%d'), 'display': '今日数据', 'is_today': True}]

def get_specific_date_data(date_str):
    try:
        try:
            from function.dau_analytics import get_dau_analytics
        except ImportError:
            if (project_root := os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
                sys.path.insert(0, project_root)
            from function.dau_analytics import get_dau_analytics
        if not (dau_data := get_dau_analytics().load_dau_data(datetime.strptime(date_str, '%Y-%m-%d'))):
            return None
        return {'message_stats': dau_data.get('message_stats', {}), 'user_stats': dau_data.get('user_stats', {}),
            'command_stats': dau_data.get('command_stats', []), 'event_stats': dau_data.get('event_stats', {}),
            'date': date_str, 'generated_at': dau_data.get('generated_at', ''), 'is_historical': True}
    except Exception as e:
        add_error_log(f"获取特定日期DAU数据失败 {date_str}: {str(e)}")
        return None

# 在文件末尾添加开放平台相关的代码

openapi_user_data = {}
openapi_login_tasks = {}

OPENAPI_DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'openapi.json')

# 导入本地bot_api模块
try:
    from function.bot_api import get_bot_api
    _bot_api = get_bot_api()
except ImportError:
    import sys
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from function.bot_api import get_bot_api
    _bot_api = get_bot_api()

# 注意：所有API调用现在都是同步的，无需异步处理

def save_openapi_data():
    try:
        os.makedirs(os.path.dirname(OPENAPI_DATA_FILE), exist_ok=True)
        with open(OPENAPI_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(openapi_user_data, f, indent=2, ensure_ascii=False)
    except:
        pass

def load_openapi_data():
    global openapi_user_data
    try:
        openapi_user_data = json.load(open(OPENAPI_DATA_FILE, 'r', encoding='utf-8')) if os.path.exists(OPENAPI_DATA_FILE) else {}
    except:
        openapi_user_data = {}

def verify_openapi_login(user_data):
    try:
        return user_data and user_data.get('type') == 'ok' and _bot_api.get_bot_list(
            uin=user_data.get('uin'), quid=user_data.get('developerId'), ticket=user_data.get('ticket')).get('code') == 0
    except:
            return False
        


@web.route('/openapi/start_login', methods=['POST'])
@require_auth
def openapi_start_login():
    try:
        user_id = request.get_json().get('user_id', 'web_user')
        if (login_data := _bot_api.create_login_qr()).get('status') != 'success' or not (url := login_data.get('url')) or not (qr := login_data.get('qr')):
            return jsonify({'success': False, 'message': '获取登录二维码失败，请稍后重试'})
        openapi_login_tasks[user_id] = (time.time(), {'qr': qr})
        return jsonify({'success': True, 'login_url': url, 'qr_code': qr, 'message': '请扫描二维码登录'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'启动登录失败: {str(e)}'})

@web.route('/openapi/check_login', methods=['POST'])
@require_auth
def openapi_check_login():
    try:
        user_id = request.get_json().get('user_id', 'web_user')
        if user_id not in openapi_login_tasks:
            return jsonify({'success': False, 'status': 'not_started', 'message': '未找到登录任务'})
        if (res := _bot_api.get_qr_login_info(qrcode=openapi_login_tasks[user_id][1]['qr'])).get('code') == 0:
            login_data = res.get('data', {}).get('data', {})
            openapi_user_data[user_id] = {'type': 'ok', **login_data}
            openapi_login_tasks.pop(user_id, None)
            save_openapi_data()
            return jsonify({'success': True, 'status': 'logged_in', 'data': {'uin': login_data.get('uin'), 
                'appId': login_data.get('appId'), 'developerId': login_data.get('developerId')}, 'message': '登录成功'})
        return jsonify({'success': True, 'status': 'waiting', 'message': '等待扫码登录'})
    except Exception as e:
        return jsonify({'success': False, 'status': 'error', 'message': f'检查登录状态失败: {str(e)}'})

@web.route('/openapi/get_botlist', methods=['POST'])
@require_auth
def openapi_get_botlist():
    try:
        user_id = request.get_json().get('user_id', 'web_user')
        if user_id not in openapi_user_data:
            return jsonify({'success': False, 'message': '未登录，请先登录开放平台'})
        user_data = openapi_user_data[user_id]
        if (res := _bot_api.get_bot_list(uin=user_data.get('uin'), quid=user_data.get('developerId'), ticket=user_data.get('ticket'))).get('code') != 0:
            return jsonify({'success': False, 'message': '登录状态失效，请重新登录'})
        return jsonify({'success': True, 'data': {'uin': user_data.get('uin'), 'apps': res.get('data', {}).get('apps', [])}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取机器人列表失败: {str(e)}'})

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
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    # 使用本地bot_api模块获取三种类型的数据
    try:
        data1_json = _bot_api.get_bot_data(
            uin=user_data.get('uin'),
            quid=user_data.get('developerId'),
            ticket=user_data.get('ticket'),
            appid=appid_to_use,
            data_type=1
        )
        
        data2_json = _bot_api.get_bot_data(
            uin=user_data.get('uin'),
            quid=user_data.get('developerId'),
            ticket=user_data.get('ticket'),
            appid=appid_to_use,
            data_type=2
        )
        
        data3_json = _bot_api.get_bot_data(
            uin=user_data.get('uin'),
            quid=user_data.get('developerId'),
            ticket=user_data.get('ticket'),
            appid=appid_to_use,
            data_type=3
        )
        
        # 检查API返回状态，兼容不同的错误字段
        def is_api_error(result):
            # 检查多种可能的错误状态
            return (result.get('retcode', 0) != 0 or 
                   result.get('code', 0) not in [0, 200] or
                   result.get('error') is not None)
        
        if any(is_api_error(x) for x in [data1_json, data2_json, data3_json]):
            # 提取具体的错误信息
            error_msgs = []
            for result in [data1_json, data2_json, data3_json]:
                if is_api_error(result):
                    error_msg = (result.get('msg') or 
                               result.get('message') or 
                               result.get('error') or 
                               f"API错误，code: {result.get('code', 'unknown')}")
                    error_msgs.append(error_msg)
            
            # 如果错误信息包含请求失败或者是认证相关错误，提示重新登录
            combined_error = ', '.join(set(error_msgs[:3]))  # 只显示前3个不同的错误
            if any(keyword in combined_error.lower() for keyword in ['登录', 'login', 'auth', '认证', '权限']):
                return openapi_error_response('登录状态失效，请重新登录')
            else:
                return openapi_error_response(f'获取数据失败: {combined_error}')
        
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
        
    except Exception as e:
        return openapi_error_response(f'获取机器人数据失败: {str(e)}')

@web.route('/openapi/get_notifications', methods=['POST'])
@require_auth
@catch_error
def openapi_get_notifications():
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    # 使用本地bot_api模块获取私信消息
    res = _bot_api.get_private_messages(
        uin=user_data.get('uin'),
        quid=user_data.get('developerId'),
        ticket=user_data.get('ticket')
    )
    
    # 检查API返回的错误状态，兼容不同的错误字段
    if res.get('code', 0) != 0 or res.get('error'):
        error_msg = res.get('error', '获取通知消息失败，请检查登录状态')
        return openapi_error_response(error_msg)
    
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
    user_id = request.get_json().get('user_id', 'web_user')
    openapi_user_data.pop(user_id, None)
    save_openapi_data()
    return openapi_success_response(message='登出成功')

@web.route('/openapi/get_login_status', methods=['POST'])
@require_auth
@catch_error
def openapi_get_login_status():
    user_data = check_openapi_login(request.get_json().get('user_id', 'web_user'))
    return openapi_success_response(logged_in=True, uin=user_data.get('uin'), appid=user_data.get('appId')) if user_data else openapi_success_response(logged_in=False)

def cleanup_openapi_tasks():
    current_time = time.time()
    for user_id in [uid for uid, (start_time, _) in openapi_login_tasks.items() if current_time - start_time > 300]:
        openapi_login_tasks.pop(user_id, None)

def start_openapi_cleanup_thread():
    def cleanup_loop():
        while True:
            try:
                cleanup_openapi_tasks()
            except:
                pass
            time.sleep(60)
    threading.Thread(target=cleanup_loop, daemon=True).start()

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
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    # 使用本地bot_api模块获取消息模板
    res = _bot_api.get_message_templates(
        uin=user_data.get('uin'),
        quid=user_data.get('developerId'),
        ticket=user_data.get('ticket'),
        appid=appid_to_use
    )

    # 修正判断条件，retcode和code只要有一个不为0就提示登录失效
    if res.get('retcode', 0) != 0 or res.get('code', 0) != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    else:
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
    seen = set()
    return [p for p in re.findall(r'\{\{\.(\w+)\}\}', template_content) if p not in seen and not seen.add(p)]

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
            
            
            return jsonify({
                'success': True,
                'valid': True,
                'data': {
                    'uin': user_data.get('uin'),
                    'appId': user_data.get('appId'),
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
    if not (user_data := check_openapi_login(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use = data.get('appid') or user_data.get('appId')
    res = _bot_api.get_message_templates(uin=user_data.get('uin'), quid=user_data.get('developerId'), 
        ticket=user_data.get('ticket'), appid=appid_to_use)
    if res.get('retcode', 0) != 0 or res.get('code', 0) not in [0, 200]:
        return openapi_error_response(res.get('msg') or res.get('message') or '获取模板失败，请重新登录')
    processed_templates = [{'id': t.get('模板id', ''), 'name': t.get('模板名称', '未命名'), 'type': t.get('模板类型', '未知类型'),
        'status': t.get('模板状态', '未知状态'), 'content': t.get('模板内容', ''), 'create_time': t.get('创建时间', ''),
        'update_time': t.get('更新时间', ''), 'raw_data': t} for t in res.get('data', {}).get('list', [])]
    return jsonify({'success': True, 'data': {'uin': user_data.get('uin'), 'appid': appid_to_use, 
        'templates': processed_templates, 'total': len(processed_templates)}})

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
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    # 使用本地bot_api模块获取消息模板列表，然后过滤指定的模板ID
    res = _bot_api.get_message_templates(
        uin=user_data.get('uin'),
        quid=user_data.get('developerId'),
        ticket=user_data.get('ticket'),
        appid=appid_to_use
    )
    
    if res.get('retcode') != 0 and res.get('code') != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    
    templates = res.get('data', {}).get('list', [])
    template_detail = None
    
    # 找到指定ID的模板
    for template in templates:
        if template.get('模板id') == template_id:
            template_detail = template
            break
    
    if not template_detail:
        return openapi_error_response('未找到指定的模板')
    
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

@web.route('/openapi/get_whitelist', methods=['POST'])
@require_auth
@catch_error
def openapi_get_whitelist():
    data = request.get_json()
    if not (user_data := check_openapi_login(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    if not (appid_to_use := data.get('appid') or user_data.get('appId')):
        return openapi_error_response('缺少AppID参数')
    res = _bot_api.get_white_list(appid=appid_to_use, uin=user_data.get('uin'),
        uid=user_data.get('developerId'), ticket=user_data.get('ticket'))
    if res.get('code', 0) != 0:
        return openapi_error_response(res.get('msg') or '获取白名单失败，请检查登录状态')
    formatted_ips = [{'ip': ip.get('ip', '') if isinstance(ip, dict) else ip, 'description': ip.get('desc', '') if isinstance(ip, dict) else '',
        'create_time': ip.get('create_time', '') if isinstance(ip, dict) else '', 'status': ip.get('status', 'active') if isinstance(ip, dict) else 'active'}
        for ip in res.get('data', [])]
    return jsonify({'success': True, 'data': {'uin': user_data.get('uin'), 'appid': appid_to_use,
        'ip_list': formatted_ips, 'total': len(formatted_ips)}})

@web.route('/openapi/update_whitelist', methods=['POST'])
@require_auth
@catch_error
def openapi_update_whitelist():
    """更新IP白名单（添加或删除IP）"""
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    target_appid = data.get('appid')
    ip_address = data.get('ip', '').strip()
    action = data.get('action', '').lower()  # 'add' 或 'del'
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    if not appid_to_use:
        return openapi_error_response('缺少AppID参数')
    
    if not ip_address:
        return openapi_error_response('缺少IP地址参数')
    
    if action not in ['add', 'del']:
        return openapi_error_response('无效的操作类型，只支持add或del')
    
    # IP格式验证
    import re
    ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
    if not re.match(ip_pattern, ip_address):
        return openapi_error_response('IP地址格式无效')
    
    # 检查IP地址范围
    try:
        parts = ip_address.split('.')
        for part in parts:
            if not (0 <= int(part) <= 255):
                return openapi_error_response('IP地址范围无效')
    except ValueError:
        return openapi_error_response('IP地址格式错误')
    
    # 这里需要先创建白名单登录二维码
    try:
        # 创建白名单登录二维码
        qr_result = _bot_api.create_white_login_qr(
            appid=appid_to_use,
            uin=user_data.get('uin'),
            uid=user_data.get('developerId'),
            ticket=user_data.get('ticket')
        )
        
        if qr_result.get('code', 0) != 0:
            return openapi_error_response('创建白名单授权失败，请检查登录状态')
        
        qrcode = qr_result.get('qrcode', '')
        if not qrcode:
            return openapi_error_response('获取白名单授权码失败')
        
        # 使用本地bot_api模块更新白名单
        res = _bot_api.update_white_list(
            appid=appid_to_use,
            uin=user_data.get('uin'),
            uid=user_data.get('developerId'),
            ticket=user_data.get('ticket'),
            qrcode=qrcode,
            ip=ip_address,
            action=action
        )
        
        # 检查API返回状态
        if res.get('code', 0) != 0:
            error_msg = res.get('msg') or f'{"添加" if action == "add" else "删除"}IP失败'
            return openapi_error_response(error_msg)
        
        return jsonify({
            'success': True,
            'message': f'IP{"添加" if action == "add" else "删除"}成功',
            'data': {
                'ip': ip_address,
                'action': action,
                'appid': appid_to_use
            }
        })
        
    except Exception as e:
        return openapi_error_response(f'操作失败: {str(e)}')

@web.route('/openapi/get_delete_qr', methods=['POST'])
@require_auth
@catch_error
def openapi_get_delete_qr():
    data = request.get_json()
    if not (user_data := check_openapi_login(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    if not (appid_to_use := data.get('appid') or user_data.get('appId')):
        return openapi_error_response('缺少AppID参数')
    try:
        qr_result = _bot_api.create_white_login_qr(appid=appid_to_use, uin=user_data.get('uin'),
            uid=user_data.get('developerId'), ticket=user_data.get('ticket'))
        if qr_result.get('code', 0) != 0:
            return openapi_error_response('创建授权二维码失败，请检查登录状态')
        if not (qrcode := qr_result.get('qrcode', '')) or not (qr_url := qr_result.get('url', '')):
            return openapi_error_response('获取授权二维码失败')
        return jsonify({'success': True, 'qrcode': qrcode, 'url': qr_url, 'message': '获取授权二维码成功'})
    except Exception as e:
        return openapi_error_response(f'获取授权二维码失败: {str(e)}')

@web.route('/openapi/check_delete_auth', methods=['POST'])
@require_auth
@catch_error
def openapi_check_delete_auth():
    data = request.get_json()
    if not (user_data := check_openapi_login(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    if not (appid_to_use := data.get('appid') or user_data.get('appId')) or not (qrcode := data.get('qrcode', '')):
        return openapi_error_response('缺少必要参数')
    try:
        auth_result = _bot_api.verify_qr_auth(appid=appid_to_use, uin=user_data.get('uin'),
            uid=user_data.get('developerId'), ticket=user_data.get('ticket'), qrcode=qrcode)
        return jsonify({'success': True, 'authorized': auth_result.get('code', 0) == 0,
            'message': '授权成功' if auth_result.get('code', 0) == 0 else '等待授权中'})
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': True,
            'message': f'检查授权状态失败: {str(e)}'
        })

@web.route('/openapi/execute_delete_ip', methods=['POST'])
@require_auth
@catch_error
def openapi_execute_delete_ip():
    """执行删除IP操作（已授权）"""
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    target_appid = data.get('appid')
    ip_address = data.get('ip', '').strip()
    qrcode = data.get('qrcode', '')
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    if not all([appid_to_use, ip_address, qrcode]):
        return openapi_error_response('缺少必要参数')
    
    try:
        # 使用已授权的二维码执行删除操作
        res = _bot_api.update_white_list(
            appid=appid_to_use,
            uin=user_data.get('uin'),
            uid=user_data.get('developerId'),
            ticket=user_data.get('ticket'),
            qrcode=qrcode,
            ip=ip_address,
            action='del'
        )
        
        # 检查API返回状态
        if res.get('code', 0) != 0:
            error_msg = res.get('msg') or '删除IP失败'
            return openapi_error_response(error_msg)
        
        return jsonify({
            'success': True,
            'message': 'IP删除成功',
            'data': {
                'ip': ip_address,
                'appid': appid_to_use
            }
        })
        
    except Exception as e:
        return openapi_error_response(f'删除IP失败: {str(e)}')

@web.route('/openapi/batch_add_whitelist', methods=['POST'])
@require_auth
@catch_error
def openapi_batch_add_whitelist():
    """批量添加IP到白名单（包含现有IP）"""
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    target_appid = data.get('appid')
    ip_list = data.get('ip_list', [])
    qrcode = data.get('qrcode', '')
    
    user_data = check_openapi_login(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    if not all([appid_to_use, ip_list, qrcode]):
        return openapi_error_response('缺少必要参数')
    
    if not isinstance(ip_list, list) or len(ip_list) == 0:
        return openapi_error_response('IP列表不能为空')
    
    try:
        
        # 将IP列表转换为逗号分隔的字符串（QQ开放平台的格式要求）
        ip_string = ','.join(ip_list)
        
        # 调用更新白名单的API，使用add操作但包含所有IP
        res = _bot_api.update_white_list(
            appid=appid_to_use,
            uin=user_data.get('uin'),
            uid=user_data.get('developerId'),
            ticket=user_data.get('ticket'),
            qrcode=qrcode,
            ip=ip_string,  # 包含所有IP的字符串
            action='add'
        )
        
        # 检查API返回状态
        if res.get('code', 0) != 0:
            error_msg = res.get('msg') or '批量添加IP失败'
            return openapi_error_response(error_msg)
        
        return jsonify({
            'success': True,
            'message': f'成功批量添加 {len(ip_list)} 个IP地址',
            'data': {
                'total_ips': len(ip_list),
                'ip_list': ip_list,
                'appid': appid_to_use
            }
        })
        
    except Exception as e:
        return openapi_error_response(f'批量添加IP失败: {str(e)}')

# ===== 系统状态相关API =====

@web.route('/api/system/status', methods=['GET'])
def get_system_status():
    try:
        if (project_root := os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) not in sys.path:
            sys.path.insert(0, project_root)
        try:
            import config
            web_dual_process = getattr(config, 'SERVER_CONFIG', {}).get('web_dual_process', False)
            websocket_enabled = getattr(config, 'WEBSOCKET_CONFIG', {}).get('enabled', False)
        except:
            web_dual_process, websocket_enabled = False, False
        current_pid = os.getpid()
        websocket_available = False
        if not web_dual_process:
            if websocket_enabled:
                websocket_available = True
            else:
                try:
                    import psutil
                    websocket_available = any('main.py' in (cmdline := ' '.join(proc.info['cmdline'])) and 'ElainaBot' in cmdline
                        for proc in psutil.process_iter(['pid', 'name', 'cmdline']) if proc.info['pid'] != current_pid and proc.info['cmdline'])
                except:
                    websocket_available = False
        return jsonify({'success': True, 'standalone_web': web_dual_process, 'websocket_available': websocket_available,
            'websocket_enabled': websocket_enabled, 'process_id': current_pid, 'config_source': 'config.py'})
    except Exception as e:
        return jsonify({'success': False, 'standalone_web': True, 'websocket_available': False,
            'error': str(e), 'config_source': 'fallback'})

@web.route('/api/restart', methods=['POST'])
@require_auth
def restart_bot():
    """重启机器人 - 使用与用户统计.py一致的重启逻辑"""
    try:
        import os
        import sys
        import json
        import platform
        import subprocess
        import psutil
        import importlib.util
        
        current_pid = os.getpid()
        current_dir = os.getcwd()
        main_py_path = os.path.join(current_dir, 'main.py')
        
        # 检查main.py文件是否存在
        if not os.path.exists(main_py_path):
            return jsonify({
                'success': False,
                'error': 'main.py文件不存在！'
            })
        
        # 读取配置文件检查是否为独立进程模式
        config_path = os.path.join(current_dir, 'config.py')
        config = None
        if os.path.exists(config_path):
            try:
                spec = importlib.util.spec_from_file_location("config", config_path)
                config = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(config)
            except Exception as e:
                pass
        
        # 检查是否为独立进程模式
        is_dual_process = False
        main_port = 5001
        web_port = 5002
        
        if config and hasattr(config, 'SERVER_CONFIG'):
            server_config = config.SERVER_CONFIG
            is_dual_process = server_config.get('web_dual_process', False)
            main_port = server_config.get('port', 5001)
            web_port = server_config.get('web_port', 5002)
        
        restart_mode = "独立进程模式" if is_dual_process else "单进程模式"
        
        def _get_restart_status_file():
            """获取重启状态文件路径"""
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            data_dir = os.path.join(os.path.dirname(plugin_dir), 'plugins', 'system', 'data')
            if not os.path.exists(data_dir):
                os.makedirs(data_dir)
            return os.path.join(data_dir, 'restart_status.json')
        
        # 保存重启状态（模拟事件对象的信息）
        restart_status = {
            'restart_time': datetime.now().isoformat(),
            'completed': False,
            'message_id': None,  # Web重启没有message_id
            'user_id': 'web_admin',  # Web管理员标识
            'group_id': 'web_panel'  # Web面板标识
        }
        
        restart_status_file = _get_restart_status_file()
        with open(restart_status_file, 'w', encoding='utf-8') as f:
            json.dump(restart_status, f, ensure_ascii=False)
        
        def _find_processes_by_port(port):
            """通过端口号查找进程ID"""
            import psutil
            pids = []
            try:
                for conn in psutil.net_connections():
                    if conn.laddr.port == port and conn.status == 'LISTEN':
                        try:
                            proc = psutil.Process(conn.pid)
                            pids.append(conn.pid)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
            except Exception as e:
                pass
            return pids
        
        def _create_restart_python_script(main_py_path, is_dual_process=False, main_port=5001, web_port=5002):
            """创建重启脚本，支持独立进程模式"""
            # 获取当前Python进程的PID，传递给重启脚本
            current_python_pid = current_pid
            
            # 构建要杀死的进程列表
            if is_dual_process:
                kill_ports_code = f"""
        # 独立进程模式：查找并杀死主程序和web面板进程
        ports_to_kill = [{main_port}, {web_port}]
        pids_to_kill = []
        
        for port in ports_to_kill:
            for conn in psutil.net_connections():
                if conn.laddr.port == port and conn.status == 'LISTEN':
                    try:
                        proc = psutil.Process(conn.pid)
                        pids_to_kill.append(conn.pid)
                        print(f"找到端口{{port}}的进程: PID {{conn.pid}}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
        
        # 去重
        pids_to_kill = list(set(pids_to_kill))
        
        # 杀死所有相关进程
        for pid in pids_to_kill:
            try:
                if platform.system().lower() == 'windows':
                    result = subprocess.run(['taskkill', '/PID', str(pid), '/F'], 
                                         check=False, capture_output=True)
                    print(f"Windows: 杀死进程 PID {{pid}}, 返回码: {{result.returncode}}")
                else:
                    proc = psutil.Process(pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                        print(f"Linux: 进程 PID {{pid}} 已正常终止")
                    except psutil.TimeoutExpired:
                        proc.kill()
                        print(f"Linux: 强制杀死进程 PID {{pid}}")
            except Exception as e:
                print(f"杀死进程{{pid}}失败: {{e}}")
        
        # 等待进程完全终止
        time.sleep(1)
        
        # 验证进程是否真的被杀死
        for pid in pids_to_kill:
            try:
                proc = psutil.Process(pid)
                if proc.is_running():
                    print(f"警告: 进程 {{pid}} 仍在运行，尝试强制杀死")
                    if platform.system().lower() == 'windows':
                        subprocess.run(['taskkill', '/PID', str(pid), '/F', '/T'], check=False)
                    else:
                        proc.kill()
            except psutil.NoSuchProcess:
                print(f"确认: 进程 {{pid}} 已成功终止")
            except Exception as e:
                print(f"验证进程{{pid}}状态失败: {{e}}")
                """
            else:
                kill_ports_code = f"""
        # 单进程模式：杀死指定的Python进程
        target_pid = {current_python_pid}
        try:
            proc = psutil.Process(target_pid)
            print(f"准备杀死Python进程: PID {{target_pid}}")
            
            if platform.system().lower() == 'windows':
                # Windows下先尝试正常终止，再强制杀死
                result = subprocess.run(['taskkill', '/PID', str(target_pid), '/T'], 
                                     check=False, capture_output=True)
                time.sleep(0.1)
                subprocess.run(['taskkill', '/PID', str(target_pid), '/F', '/T'], 
                             check=False, capture_output=True)
                print(f"Windows: 已杀死进程 PID {{target_pid}}")
            else:
                # Linux下先发送SIGTERM，等待一段时间后发送SIGKILL
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                    print(f"Linux: 进程 PID {{target_pid}} 已正常终止")
                except psutil.TimeoutExpired:
                    proc.kill()
                    print(f"Linux: 强制杀死进程 PID {{target_pid}}")
        except psutil.NoSuchProcess:
            print(f"进程 {{target_pid}} 不存在或已终止")
        except Exception as e:
            print(f"杀死进程{{target_pid}}失败: {{e}}")
        
        # 等待进程完全终止
        time.sleep(1)
                """
            
            script_content = f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import signal
import platform
import subprocess
import psutil

def main():
    main_py_path = r"{main_py_path}"
    
    try:{kill_ports_code}
    except Exception as e:
        print(f"杀死进程过程中出错: {{e}}")
    
    # 等待进程完全终止
    time.sleep(1)
    
    # 最终验证：确保端口已经释放
    ports_to_check = [{main_port}, {web_port}] if {str(is_dual_process)} else [5001]
    max_wait = 5  # 最多等待5秒
    wait_count = 0
    while wait_count < max_wait:
        ports_still_occupied = False
        try:
            for conn in psutil.net_connections():
                if conn.laddr.port in ports_to_check and conn.status == 'LISTEN':
                    ports_still_occupied = True
                    print(f"端口{{conn.laddr.port}}仍被PID{{conn.pid}}占用")
                    break
        except:
            pass
            
        if not ports_still_occupied:
            print("确认端口已释放，可以启动新进程")
            break
        else:
            print(f"端口仍被占用，继续等待... ({{wait_count + 1}}/{{max_wait}})")
            time.sleep(1)
            wait_count += 1
    
    try:
        os.chdir(os.path.dirname(main_py_path))
        
        print(f"正在重新启动主程序: {{main_py_path}}")
        
        if platform.system().lower() == 'windows':
            subprocess.Popen(
                [sys.executable, main_py_path],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=os.path.dirname(main_py_path)
            )
        else:
            # 清理重启脚本
            try:
                script_path = __file__
                if os.path.exists(script_path):
                    os.remove(script_path)
            except:
                pass
            os.execv(sys.executable, [sys.executable, main_py_path])
        
        print("重启命令已执行")
        
    except Exception as e:
        print(f"重启失败: {{e}}")
        sys.exit(1)
    
    if platform.system().lower() == 'windows':
        time.sleep(1)
        try:
            # 清理重启脚本
            script_path = __file__
            if os.path.exists(script_path):
                os.remove(script_path)
        except:
            pass
        sys.exit(0)

if __name__ == "__main__":
    main()
'''
            return script_content
        
        # 创建重启脚本时传递独立进程模式信息
        restart_script_content = _create_restart_python_script(
            main_py_path, is_dual_process, main_port, web_port
        )
        restart_script_path = os.path.join(current_dir, 'bot_restarter.py')
        
        with open(restart_script_path, 'w', encoding='utf-8') as f:
            f.write(restart_script_content)
        
        # 输出调试信息
        if is_dual_process:
            # 显示当前监听的端口进程
            try:
                main_pids = _find_processes_by_port(main_port)
                web_pids = _find_processes_by_port(web_port)
            except Exception as e:
                pass
        
        is_windows = platform.system().lower() == 'windows'
        
        if is_windows:
            subprocess.Popen(['python', restart_script_path], cwd=current_dir,
                           creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen([sys.executable, restart_script_path], cwd=current_dir,
                           start_new_session=True)
        
        return jsonify({
            'success': True,
            'message': f'🔄 正在重启机器人... ({restart_mode})\n⏱️ 预计重启时间: 1秒'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@web.route('/api/status', methods=['GET'])
def get_simple_status():
    """获取简单状态信息，用于重启后的状态检测"""
    try:
        import datetime
        return jsonify({
            'status': 'ok',
            'timestamp': datetime.datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

# ===== 消息发送界面相关API =====

@web.route('/api/message/get_chats', methods=['POST'])
@require_auth
def get_chats():
    """获取聊天列表（用户或群聊）"""
    try:
        data = request.get_json()
        chat_type = data.get('type', 'user')  # 'user' 或 'group'
        search = data.get('search', '').strip()
        limit = 30  # 直接加载30个聊天记录
        
        # 导入数据库连接
        from function.log_db import LogDatabasePool
        from pymysql.cursors import DictCursor
        
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            return jsonify({'success': False, 'message': '数据库连接失败'})
        
        try:
            cursor = connection.cursor(DictCursor)
            
            # 获取表前缀
            table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            id_table_name = f'{table_prefix}id'
            
            # 检查ID表是否存在
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """, (id_table_name,))
            if cursor.fetchone()['count'] == 0:
                return jsonify({'success': False, 'message': 'ID表不存在'})
            
            # 构建查询
            if search:
                # 搜索功能 - 使用参数化查询避免SQL注入
                search_condition = "AND chat_id LIKE %s"
                search_param = f"%{search}%"
            else:
                search_condition = ""
                search_param = None
            
            # 获取聊天数据（最多30个）
            data_sql = f"""
                SELECT chat_id, last_message_id, MAX(timestamp) as last_time
                FROM {id_table_name} 
                WHERE chat_type = %s {search_condition}
                GROUP BY chat_id, last_message_id
                ORDER BY last_time DESC
                LIMIT %s
            """
            if search_param:
                cursor.execute(data_sql, (chat_type, search_param, limit))
            else:
                cursor.execute(data_sql, (chat_type, limit))
            chats = cursor.fetchall()
            
            # 处理数据
            chat_list = []
            for chat in chats:
                chat_info = {
                    'chat_id': chat['chat_id'],
                    'last_message_id': chat['last_message_id'],
                    'last_time': chat['last_time'].strftime('%Y-%m-%d %H:%M:%S') if chat['last_time'] else '',
                    'avatar': get_chat_avatar(chat['chat_id'], chat_type),
                    'nickname': 'Loading...'  # 异步加载
                }
                chat_list.append(chat_info)
            
            return jsonify({
                'success': True,
                'data': {
                    'chats': chat_list
                }
            })
            
        finally:
            cursor.close()
            log_db_pool.release_connection(connection)
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取聊天列表失败: {str(e)}'})

@web.route('/api/message/get_chat_history', methods=['POST'])
@require_auth
def get_chat_history():
    """获取聊天记录（仅今日）"""
    try:
        data = request.get_json()
        chat_type = data.get('chat_type')
        chat_id = data.get('chat_id')
        
        if not chat_type or not chat_id:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        
        from function.log_db import LogDatabasePool
        from pymysql.cursors import DictCursor
        import datetime
        
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            return jsonify({'success': False, 'message': '数据库连接失败'})
        
        try:
            cursor = connection.cursor(DictCursor)
            
            # 获取表前缀和今日消息表名
            table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            today = datetime.datetime.now().strftime('%Y%m%d')
            table_name = f'{table_prefix}{today}_message'
            
            # 检查表是否存在
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """, (table_name,))
            
            if cursor.fetchone()['count'] == 0:
                return jsonify({'success': True, 'data': {'messages': [],
                    'chat_info': {'chat_id': chat_id, 'chat_type': chat_type, 'avatar': get_chat_avatar(chat_id, chat_type)}}})
            
            where_condition, params = (("(group_id = %s AND group_id != 'c2c') OR (user_id = 'ZFC2G' AND group_id = %s)", (chat_id, chat_id))
                if chat_type == 'group' else ("(user_id = %s AND group_id = 'c2c') OR (user_id = %s AND group_id = 'ZFC2C')", (chat_id, chat_id)))
            
            # 获取消息记录
            sql = f"""
                SELECT user_id, group_id, content, timestamp
                FROM {table_name}
                WHERE {where_condition}
                ORDER BY timestamp ASC
                LIMIT 100
            """
            cursor.execute(sql, params)
            messages = cursor.fetchall()
            
            message_list = []
            for msg in messages:
                is_self_message = (chat_type == 'group' and msg['user_id'] == 'ZFC2G') or (chat_type == 'user' and msg['group_id'] == 'ZFC2C')
                display_user_id = '机器人' if is_self_message else msg['user_id']
                message_list.append({'user_id': display_user_id, 'content': msg['content'],
                    'timestamp': msg['timestamp'].strftime('%H:%M:%S') if msg['timestamp'] else '',
                    'avatar': get_chat_avatar('robot' if is_self_message else msg['user_id'], 'user'), 'is_self': is_self_message})
            
            return jsonify({'success': True, 'data': {'messages': message_list,
                'chat_info': {'chat_id': chat_id, 'chat_type': chat_type, 'avatar': get_chat_avatar(chat_id, chat_type)}}})
            
        finally:
            cursor.close()
            log_db_pool.release_connection(connection)
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取聊天记录失败: {str(e)}'})

@web.route('/api/message/send', methods=['POST'])
@require_auth
def send_message():
    """发送消息"""
    try:
        data = request.get_json()
        chat_type = data.get('chat_type')
        chat_id = data.get('chat_id')
        send_method = data.get('send_method', 'text')
        
        if not chat_type or not chat_id:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        
        # 检查ID是否过期
        from function.log_db import LogDatabasePool
        from pymysql.cursors import DictCursor
        import datetime
        
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            return jsonify({'success': False, 'message': '数据库连接失败'})
        
        try:
            cursor = connection.cursor(DictCursor)
            
            # 获取表前缀和ID表名
            table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            id_table_name = f'{table_prefix}id'
            
            # 获取最后的消息ID和时间
            cursor.execute(f"""
                SELECT last_message_id, timestamp 
                FROM {id_table_name} 
                WHERE chat_type = %s AND chat_id = %s
            """, (chat_type, chat_id))
            
            result = cursor.fetchone()
            if not result:
                return jsonify({'success': False, 'message': 'ID记录不存在'})
            
            last_message_id = result['last_message_id']
            last_time = result['timestamp']
            
            # 获取当前时间用于显示
            now = datetime.datetime.now()
            
            # 创建模拟消息事件来发送消息
            mock_raw_data = {
                'd': {
                    'id': last_message_id,
                    'author': {'id': '2218872014'},
                    'content': '',
                    'timestamp': last_time.isoformat()
                },
                'id': last_message_id,
                't': 'C2C_MESSAGE_CREATE' if chat_type == 'user' else 'GROUP_AT_MESSAGE_CREATE'
            }
            
            if chat_type == 'group':
                mock_raw_data['d']['group_id'] = chat_id
            else:
                mock_raw_data['d']['author']['id'] = chat_id
                
            from core.event.MessageEvent import MessageEvent
            event = MessageEvent(mock_raw_data, skip_recording=True)
            
            # 根据发送方法调用相应的发送函数
            message_id = None
            display_content = ''
            
            if send_method == 'text':
                if not (content := data.get('content', '').strip()):
                    return jsonify({'success': False, 'message': '请输入消息内容'})
                # 发送普通消息：强制使用纯文本模式 (use_markdown=False)
                message_id = event.reply(content, use_markdown=False)
                display_content = content
            elif send_method == 'markdown':
                if not (content := data.get('content', '').strip()):
                    return jsonify({'success': False, 'message': '请输入Markdown内容'})
                # 发送 Markdown 消息：强制使用 markdown 模式 (use_markdown=True)
                message_id = event.reply(content, use_markdown=True)
                display_content = content
                
            elif send_method == 'template_markdown':
                if not (template := data.get('template')):
                    return jsonify({'success': False, 'message': '请选择模板'})
                if not (params := data.get('params', [])):
                    return jsonify({'success': False, 'message': '请输入模板参数'})
                message_id, display_content = event.reply_markdown(template, tuple(params), data.get('keyboard_id')), f'[模板消息: {template}]'
            elif send_method == 'image':
                if not (image_url := data.get('image_url', '').strip()):
                    return jsonify({'success': False, 'message': '请输入图片URL'})
                message_id, display_content = event.reply_image(image_url, data.get('image_text', '').strip()), f'[图片消息: {data.get("image_text", "") or "图片"}]'
            elif send_method == 'voice':
                if not (voice_url := data.get('voice_url', '').strip()):
                    return jsonify({'success': False, 'message': '请输入语音文件URL'})
                message_id, display_content = event.reply_voice(voice_url), '[语音消息]'
            elif send_method == 'video':
                if not (video_url := data.get('video_url', '').strip()):
                    return jsonify({'success': False, 'message': '请输入视频文件URL'})
                message_id, display_content = event.reply_video(video_url), '[视频消息]'
            elif send_method == 'ark':
                if not (ark_type := data.get('ark_type')):
                    return jsonify({'success': False, 'message': '请选择ARK卡片类型'})
                if not (ark_params := data.get('ark_params', [])):
                    return jsonify({'success': False, 'message': '请输入卡片参数'})
                message_id, display_content = event.reply_ark(ark_type, tuple(ark_params)), f'[ARK卡片: 类型{ark_type}]'
            else:
                return jsonify({'success': False, 'message': '不支持的发送方法'})
            
            # 根据MessageEvent的返回值判断发送是否成功
            if message_id is not None:
                # 检查message_id是否包含官方API错误信息
                message_id_str = str(message_id)
                
                # 检查是否为JSON格式的错误信息（MessageEvent返回的错误）
                if message_id_str.startswith('{') and message_id_str.endswith('}'):
                    try:
                        import json
                        error_obj = json.loads(message_id_str)
                        
                        # 检查是否为错误信息
                        if error_obj.get('error') is True:
                            api_error = ''
                            if error_obj.get('message'):
                                api_error = error_obj['message']
                            if error_obj.get('code'):
                                api_error += f", code:{error_obj['code']}" if api_error else f"code:{error_obj['code']}"
                            
                            return jsonify({'success': False, 'message': api_error or "发送失败: 未知错误"})
                    except Exception:
                        pass
                
                # 如果不是API错误格式，则表示发送成功
                
                # 记录发送的消息到数据库
                try:
                    add_sent_message_to_db(
                        chat_type=chat_type,
                        chat_id=chat_id,
                        content=display_content,
                        timestamp=now.strftime('%Y-%m-%d %H:%M:%S')
                    )
                except Exception as e:
                    # 记录失败不影响发送成功的响应
                    print(f"记录发送消息失败: {str(e)}")
                
                return jsonify({
                    'success': True,
                    'message': '消息发送成功',
                    'data': {
                        'message_id': message_id,
                        'content': display_content,
                        'timestamp': now.strftime('%H:%M:%S'),
                        'send_method': send_method
                    }
                })
            else:
                # MessageEvent返回None，表示发送失败（如被忽略的错误代码）
                return jsonify({'success': False, 'message': '消息发送失败，可能是权限不足或其他限制'})
            
        finally:
            cursor.close()
            log_db_pool.release_connection(connection)
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'发送消息失败: {str(e)}'})

@web.route('/api/message/get_nickname', methods=['POST'])
@require_auth
def get_nickname():
    try:
        if not (user_id := request.get_json().get('user_id')):
            return jsonify({'success': False, 'message': '缺少用户ID'})
        
        # 从数据库获取昵称
        try:
            from function.database import Database
            db = Database()
            nickname = db.get_user_name(user_id)
            
            if not nickname:
                nickname = f"用户{user_id[-6:]}"
        except:
            nickname = f"用户{user_id[-6:]}"
            
        return jsonify({'success': True, 'data': {'user_id': user_id, 'nickname': nickname}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取昵称失败: {str(e)}'})

@web.route('/api/message/get_nicknames_batch', methods=['POST'])
@require_auth
def get_nicknames_batch():
    """批量获取用户昵称"""
    try:
        data = request.get_json()
        user_ids = data.get('user_ids', [])
        
        if not user_ids or not isinstance(user_ids, list):
            return jsonify({'success': False, 'message': '缺少用户ID列表'})
        
        # 从数据库批量获取昵称
        try:
            from function.database import Database, get_table_name
            from function.db_pool import ConnectionManager
            
            db = Database()
            nicknames = {}
            
            # 使用 IN 查询批量获取
            if user_ids:
                with ConnectionManager() as manager:
                    if manager.connection:
                        cursor = manager.connection.cursor()
                        try:
                            placeholders = ','.join(['%s'] * len(user_ids))
                            sql = f"SELECT user_id, name FROM {get_table_name('users')} WHERE user_id IN ({placeholders})"
                            cursor.execute(sql, tuple(user_ids))
                            results = cursor.fetchall()
                            
                            # 处理结果
                            for row in results:
                                if isinstance(row, dict):
                                    user_id = row.get('user_id')
                                    name = row.get('name')
                                else:
                                    user_id = row[0]
                                    name = row[1]
                                
                                if user_id and name:
                                    nicknames[user_id] = name
                        finally:
                            cursor.close()
            
            # 对于没有找到昵称的用户，使用默认昵称
            for user_id in user_ids:
                if user_id not in nicknames:
                    nicknames[user_id] = f"用户{user_id[-6:]}"
            
            return jsonify({'success': True, 'data': {'nicknames': nicknames}})
            
        except Exception as e:
            # 如果数据库查询失败，返回默认昵称
            nicknames = {user_id: f"用户{user_id[-6:]}" for user_id in user_ids}
            return jsonify({'success': True, 'data': {'nicknames': nicknames}})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'批量获取昵称失败: {str(e)}'})

@web.route('/api/message/get_templates', methods=['GET'])
@require_auth
def get_markdown_templates():
    """获取所有可用的 Markdown 模板"""
    try:
        from core.event.markdown_templates import get_all_templates
        templates = get_all_templates()
        
        template_list = []
        for template_id, template_info in templates.items():
            template_list.append({
                'id': template_id,
                'name': f'模板{template_id}',
                'params': template_info.get('params', []),
                'param_count': len(template_info.get('params', []))
            })
        
        return jsonify({'success': True, 'data': {'templates': template_list}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取模板列表失败: {str(e)}'})

def get_chat_avatar(chat_id, chat_type):
    """获取聊天头像URL"""
    if chat_type == 'user':
        return f"https://q.qlogo.cn/qqapp/{appid}/{chat_id}/100"
    else:  # group
        # 群聊显示第一个字母作为头像
        return chat_id[0].upper() if chat_id else 'G'

def get_user_nickname(user_id):
    try:
        try:
            response = requests.get("https://i.elaina.vin/api/bot/xx.php", params={'openid': user_id, 'appid': appid}, timeout=3)
            if response.status_code == 200:
                try:
                    data = response.json()
                    return data.get('名字', f"用户{user_id[-6:]}")
                except:
                    # JSON解析失败时的fallback处理
                    nickname = response.text.strip()
                    return nickname if nickname else f"用户{user_id[-6:]}"
            else:
                return f"用户{user_id[-6:]}"
        except:
            return f"用户{user_id[-6:]}"
            
    except Exception:
        return f"用户{user_id[-6:]}"

# 昵称缓存 - 全局变量，避免重复请求
_nickname_cache = {}
_cache_timeout = 86400  # 1天缓存过期

def get_user_nicknames_batch(user_ids):
    """批量获取用户昵称（带缓存优化）"""
    import time
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    current_time = time.time()
    result = {}
    users_to_fetch = []
    
    # 检查缓存，过滤出需要获取的用户
    for user_id in user_ids:
        if user_id in _nickname_cache:
            cached_data = _nickname_cache[user_id]
            if current_time - cached_data['timestamp'] < _cache_timeout:
                result[user_id] = cached_data['nickname']
                continue
        users_to_fetch.append(user_id)
    
    # 如果没有需要获取的用户，直接返回缓存结果
    if not users_to_fetch:
        return result
    
    def fetch_single_nickname(user_id):
        try:
            nickname = get_user_nickname(user_id)
        except:
            nickname = f"用户{user_id[-6:]}"
        _nickname_cache[user_id] = {'nickname': nickname, 'timestamp': current_time}
        return user_id, nickname
    
    with ThreadPoolExecutor(max_workers=min(5, len(users_to_fetch))) as executor:
        future_to_user = {executor.submit(fetch_single_nickname, uid): uid for uid in users_to_fetch}
        for future in as_completed(future_to_user, timeout=10):
            try:
                user_id, nickname = future.result()
                result[user_id] = nickname
            except:
                result[future_to_user[future]] = f"用户{future_to_user[future][-6:]}"
    return result