import os, json, time, uuid, base64, hashlib, hmac, functools
from datetime import datetime
from flask import request, render_template

valid_sessions = {}
_last_session_cleanup = 0
ip_access_data = {}
_last_ip_cleanup = 0

WEB_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'web')
IP_DATA_FILE = os.path.join(WEB_DATA_DIR, 'ip.json')
SESSION_DATA_FILE = os.path.join(WEB_DATA_DIR, 'sessions.json')

os.makedirs(WEB_DATA_DIR, exist_ok=True)

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

def cleanup_old_password_fails(ip):
    if ip in ip_access_data:
        ip_access_data[ip]['password_fail_times'] = [
            t for t in ip_access_data[ip]['password_fail_times']
            if (datetime.now() - datetime.fromisoformat(t)).total_seconds() < 86400
        ]

def cleanup_old_password_success(ip):
    if ip in ip_access_data:
        ip_access_data[ip]['password_success_times'] = [
            t for t in ip_access_data[ip]['password_success_times']
            if (datetime.now() - datetime.fromisoformat(t)).total_seconds() < 2592000
        ]

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

def is_ip_banned(ip_address):
    if ip_address not in ip_access_data:
        return False
    
    ip_data = ip_access_data[ip_address]
    if not ip_data.get('is_banned'):
        return False
    
    ban_time_str = ip_data.get('ban_time')
    if not ban_time_str:
        return True
    
    try:
        if (datetime.now() - datetime.fromisoformat(ban_time_str)).total_seconds() >= 86400:
            ip_data['is_banned'] = False
            ip_data['ban_time'] = None
            ip_data['password_fail_times'] = []
            save_ip_data()
            return False
        return True
    except:
        return True

def cleanup_expired_ip_bans():
    global ip_access_data, _last_ip_cleanup
    current_time = time.time()
    
    if current_time - _last_ip_cleanup < 3600:
        return
    
    _last_ip_cleanup = current_time
    current_datetime = datetime.now()
    cleaned = 0
    
    for ip_address, ip_data in list(ip_access_data.items()):
        cleanup_old_password_fails(ip_address)
        if ip_data.get('is_banned') and (ban_time_str := ip_data.get('ban_time')):
            try:
                if (current_datetime - datetime.fromisoformat(ban_time_str)).total_seconds() >= 86400:
                    ip_data['is_banned'] = False
                    ip_data['ban_time'] = None
                    ip_data['password_fail_times'] = []
                    cleaned += 1
            except:
                pass
    
    if cleaned:
        save_ip_data()

def load_session_data():
    global valid_sessions
    if os.path.exists(SESSION_DATA_FILE):
        with open(SESSION_DATA_FILE, 'r', encoding='utf-8') as f:
            sessions = json.load(f)
            for session_token, session_info in sessions.items():
                session_info['created'] = datetime.fromisoformat(session_info['created'])
                session_info['expires'] = datetime.fromisoformat(session_info['expires'])
                if datetime.now() < session_info['expires']:
                    valid_sessions[session_token] = session_info

def save_session_data():
    sessions_to_save = {}
    for session_token, session_info in valid_sessions.items():
        sessions_to_save[session_token] = {
            'created': session_info['created'].isoformat(),
            'expires': session_info['expires'].isoformat(),
            'ip': session_info.get('ip', ''),
            'user_agent': session_info.get('user_agent', '')
        }
    with open(SESSION_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(sessions_to_save, f, ensure_ascii=False, indent=2)

def cleanup_expired_sessions():
    global valid_sessions, _last_session_cleanup
    current_time = time.time()
    
    if current_time - _last_session_cleanup < 300:
        return
    
    _last_session_cleanup = current_time
    current_datetime = datetime.now()
    expired_count = 0
    
    for session_token in list(valid_sessions.keys()):
        if current_datetime >= valid_sessions[session_token]['expires']:
            del valid_sessions[session_token]
            expired_count += 1
    
    if expired_count > 0:
        save_session_data()

def limit_session_count():
    if len(valid_sessions) > 10:
        sorted_sessions = sorted(valid_sessions.items(), key=lambda x: x[1]['created'])
        for i in range(len(valid_sessions) - 10):
            valid_sessions.pop(sorted_sessions[i][0])
        save_session_data()

def generate_session_token():
    return base64.urlsafe_b64encode(uuid.uuid4().bytes).decode('utf-8').rstrip('=')

def sign_cookie_value(value, secret):
    signature = hmac.new(secret.encode('utf-8'), value.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{value}.{signature}"

def verify_cookie_value(signed_value, secret):
    try:
        value, signature = signed_value.rsplit('.', 1)
        expected_signature = hmac.new(secret.encode('utf-8'), value.encode('utf-8'), hashlib.sha256).hexdigest()
        is_valid = hmac.compare_digest(signature, expected_signature)
        return is_valid, value
    except:
        return False, None

def require_token(WEB_SECURITY):
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            token = request.args.get('token') or request.form.get('token')
            if not token or token != WEB_SECURITY['access_token']:
                return '', 403
            record_ip_access(request.remote_addr, 'token_success', extract_device_info(request))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def require_auth(WEB_SECURITY, WEB_INTERFACE):
    def decorator(f):
        @functools.wraps(f)
        def decorated_function(*args, **kwargs):
            cleanup_expired_sessions()
            cookie_value = request.cookies.get('elaina_admin_session')
            
            if cookie_value:
                is_valid, session_token = verify_cookie_value(cookie_value, 'elaina_cookie_secret_key_2024_v1')
                if is_valid and session_token in valid_sessions:
                    session_info = valid_sessions[session_token]
                    if datetime.now() < session_info['expires']:
                        if not WEB_SECURITY.get('production_mode', False) or \
                           (session_info.get('ip') == request.remote_addr and \
                            session_info.get('user_agent', '')[:200] == request.headers.get('User-Agent', '')[:200]):
                            return f(*args, **kwargs)
                        del valid_sessions[session_token]
                        save_session_data()
                    else:
                        del valid_sessions[session_token]
                        save_session_data()
            
            from flask import render_template
            return render_template('login.html', token=request.args.get('token', ''), web_interface=WEB_INTERFACE)
        return decorated_function
    return decorator

def require_socketio_token(WEB_SECURITY):
    def decorator(f):
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
                save_session_data()
                return False
            
            if WEB_SECURITY.get('production_mode', False):
                if (session_info.get('ip') != client_ip or 
                    session_info.get('user_agent', '')[:200] != request.headers.get('User-Agent', '')[:200]):
                    del valid_sessions[session_token]
                    save_session_data()
                    return False
            
            device_info = extract_device_info(request)
            record_ip_access(client_ip, access_type='token_success', device_info=device_info)
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def check_ip_ban(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        cleanup_expired_ip_bans()
        if is_ip_banned(request.remote_addr):
            return '', 403
        return f(*args, **kwargs)
    return wrapper

load_ip_data()
load_session_data()
