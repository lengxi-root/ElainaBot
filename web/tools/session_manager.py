import os, json, time, uuid, base64, hashlib, hmac, functools
from datetime import datetime
from flask import request, render_template

valid_sessions = {}
ip_access_data = {}
_last_session_cleanup = 0
_last_ip_cleanup = 0

_WEB_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'web')
_IP_DATA_FILE = os.path.join(_WEB_DATA_DIR, 'ip.json')
_SESSION_DATA_FILE = os.path.join(_WEB_DATA_DIR, 'sessions.json')
_COOKIE_SECRET = 'elaina_cookie_secret_key_2024_v1'
_BAN_DURATION = 86400
_SESSION_CLEANUP_INTERVAL = 300
_IP_CLEANUP_INTERVAL = 3600
_PASSWORD_FAIL_WINDOW = 86400
_PASSWORD_SUCCESS_WINDOW = 2592000
_MAX_SESSIONS = 10
_MAX_FAIL_COUNT = 5

os.makedirs(_WEB_DATA_DIR, exist_ok=True)

def safe_file_operation(operation, file_path, data=None, default_return=None):
    try:
        if operation == 'read':
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return default_return or {}
        elif operation == 'write' and data is not None:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
    except:
        return default_return

def get_real_ip(req):
    forwarded = req.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    real_ip = req.headers.get('X-Real-IP')
    return real_ip.strip() if real_ip else req.remote_addr

_MOBILE_KEYWORDS = frozenset({'android', 'iphone', 'ipad', 'mobile', 'phone'})
_BROWSER_KEYWORDS = (('edge', 'edge'), ('chrome', 'chrome'), ('firefox', 'firefox'), ('safari', 'safari'))

def extract_device_info(req):
    ua = req.headers.get('User-Agent', '')
    ua_lower = ua.lower()
    
    if 'tablet' in ua_lower:
        device_type = 'tablet'
    elif any(k in ua_lower for k in _MOBILE_KEYWORDS):
        device_type = 'mobile'
    else:
        device_type = 'desktop'
    
    browser = 'unknown'
    for keyword, name in _BROWSER_KEYWORDS:
        if keyword in ua_lower and (keyword != 'safari' or 'chrome' not in ua_lower):
            browser = name
            break
    
    return {
        'user_agent': ua[:500], 'accept_language': req.headers.get('Accept-Language', '')[:100],
        'accept_encoding': req.headers.get('Accept-Encoding', '')[:100],
        'last_update': datetime.now().isoformat(), 'device_type': device_type, 'browser': browser
    }

def load_ip_data():
    global ip_access_data
    ip_access_data = safe_file_operation('read', _IP_DATA_FILE, default_return={})

def save_ip_data():
    safe_file_operation('write', _IP_DATA_FILE, ip_access_data)

def _cleanup_old_times(ip, field, window):
    if ip in ip_access_data:
        now = datetime.now()
        ip_access_data[ip][field] = [t for t in ip_access_data[ip][field] if (now - datetime.fromisoformat(t)).total_seconds() < window]

def record_ip_access(ip_address, access_type='token_success', device_info=None):
    now = datetime.now()
    now_iso = now.isoformat()
    
    if ip_address not in ip_access_data:
        ip_access_data[ip_address] = {
            'first_access': now_iso, 'last_access': now_iso, 'token_success_count': 0,
            'password_fail_count': 0, 'password_fail_times': [], 'password_success_count': 0,
            'password_success_times': [], 'token_fail_count': 0, 'token_fail_times': [],
            'device_info': {}, 'is_banned': False, 'ban_time': None
        }
    
    ip_data = ip_access_data[ip_address]
    ip_data['last_access'] = now_iso
    
    if access_type == 'token_success':
        ip_data['token_success_count'] += 1
        if device_info:
            ip_data['device_info'] = device_info
    elif access_type == 'password_fail':
        ip_data['password_fail_count'] += 1
        ip_data['password_fail_times'].append(now_iso)
        _cleanup_old_times(ip_address, 'password_fail_times', _PASSWORD_FAIL_WINDOW)
        recent_fails = sum(1 for t in ip_data['password_fail_times'] if (now - datetime.fromisoformat(t)).total_seconds() < _PASSWORD_FAIL_WINDOW)
        if recent_fails >= _MAX_FAIL_COUNT:
            ip_data['is_banned'] = True
            ip_data['ban_time'] = now_iso
    elif access_type == 'password_success':
        ip_data['password_success_count'] += 1
        ip_data['password_success_times'].append(now_iso)
        _cleanup_old_times(ip_address, 'password_success_times', _PASSWORD_SUCCESS_WINDOW)
        if device_info:
            ip_data['device_info'] = device_info
    elif access_type == 'token_fail':
        if 'token_fail_count' not in ip_data:
            ip_data['token_fail_count'] = 0
            ip_data['token_fail_times'] = []
        ip_data['token_fail_count'] += 1
        ip_data['token_fail_times'].append(now_iso)
        _cleanup_old_times(ip_address, 'token_fail_times', _PASSWORD_FAIL_WINDOW)
        recent_fails = sum(1 for t in ip_data['token_fail_times'] if (now - datetime.fromisoformat(t)).total_seconds() < _PASSWORD_FAIL_WINDOW)
        if recent_fails >= _MAX_FAIL_COUNT:
            ip_data['is_banned'] = True
            ip_data['ban_time'] = now_iso
    
    save_ip_data()

def is_ip_banned(ip_address):
    ip_data = ip_access_data.get(ip_address)
    if not ip_data or not ip_data.get('is_banned'):
        return False
    ban_time_str = ip_data.get('ban_time')
    if not ban_time_str:
        return True
    try:
        if (datetime.now() - datetime.fromisoformat(ban_time_str)).total_seconds() >= _BAN_DURATION:
            ip_data['is_banned'] = False
            ip_data['ban_time'] = None
            ip_data['password_fail_times'] = []
            save_ip_data()
            return False
        return True
    except:
        return True

def cleanup_expired_ip_bans():
    global _last_ip_cleanup
    now = time.time()
    if now - _last_ip_cleanup < _IP_CLEANUP_INTERVAL:
        return
    _last_ip_cleanup = now
    now_dt = datetime.now()
    cleaned = 0
    for ip, data in list(ip_access_data.items()):
        _cleanup_old_times(ip, 'password_fail_times', _PASSWORD_FAIL_WINDOW)
        if data.get('is_banned') and (ban_time := data.get('ban_time')):
            try:
                if (now_dt - datetime.fromisoformat(ban_time)).total_seconds() >= _BAN_DURATION:
                    data['is_banned'] = False
                    data['ban_time'] = None
                    data['password_fail_times'] = []
                    cleaned += 1
            except:
                pass
    if cleaned:
        save_ip_data()

def load_session_data():
    global valid_sessions
    if os.path.exists(_SESSION_DATA_FILE):
        with open(_SESSION_DATA_FILE, 'r', encoding='utf-8') as f:
            sessions = json.load(f)
            now = datetime.now()
            for token, info in sessions.items():
                info['created'] = datetime.fromisoformat(info['created'])
                info['expires'] = datetime.fromisoformat(info['expires'])
                if now < info['expires']:
                    valid_sessions[token] = info

def save_session_data():
    data = {token: {'created': info['created'].isoformat(), 'expires': info['expires'].isoformat(),
                    'ip': info.get('ip', ''), 'user_agent': info.get('user_agent', '')}
            for token, info in valid_sessions.items()}
    with open(_SESSION_DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def cleanup_expired_sessions():
    global _last_session_cleanup
    now = time.time()
    if now - _last_session_cleanup < _SESSION_CLEANUP_INTERVAL:
        return
    _last_session_cleanup = now
    now_dt = datetime.now()
    expired = [t for t, info in valid_sessions.items() if now_dt >= info['expires']]
    for t in expired:
        del valid_sessions[t]
    if expired:
        save_session_data()

def limit_session_count():
    if len(valid_sessions) > _MAX_SESSIONS:
        sorted_sessions = sorted(valid_sessions.items(), key=lambda x: x[1]['created'])
        for i in range(len(valid_sessions) - _MAX_SESSIONS):
            valid_sessions.pop(sorted_sessions[i][0])
        save_session_data()

def generate_session_token():
    return base64.urlsafe_b64encode(uuid.uuid4().bytes).decode('utf-8').rstrip('=')

def sign_cookie_value(value, secret=_COOKIE_SECRET):
    sig = hmac.new(secret.encode('utf-8'), value.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{value}.{sig}"

def verify_cookie_value(signed_value, secret=_COOKIE_SECRET):
    try:
        value, sig = signed_value.rsplit('.', 1)
        expected = hmac.new(secret.encode('utf-8'), value.encode('utf-8'), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected), value
    except:
        return False, None

def get_token_fail_count(ip_address):
    """获取IP的token错误次数"""
    ip_data = ip_access_data.get(ip_address, {})
    if 'token_fail_times' not in ip_data:
        return 0
    now = datetime.now()
    recent_fails = sum(1 for t in ip_data.get('token_fail_times', []) 
                      if (now - datetime.fromisoformat(t)).total_seconds() < _PASSWORD_FAIL_WINDOW)
    return recent_fails

def render_token_error_page(fail_count, WEB_CONFIG):
    """渲染token错误页面"""
    from flask import render_template
    remaining = _MAX_FAIL_COUNT - fail_count
    is_banned = remaining <= 0
    
    return render_template('token_error.html',
        framework_name=WEB_CONFIG.get('framework_name', 'ElainaBot'),
        favicon_url=WEB_CONFIG.get('favicon_url', ''),
        theme_gradient=WEB_CONFIG.get('theme_gradient', 'linear-gradient(135deg, #5865F2, #7289DA)'),
        fail_count=fail_count,
        remaining=max(0, remaining),
        is_banned=is_banned,
        max_fail_count=_MAX_FAIL_COUNT
    )

def require_token(WEB_CONFIG):
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            token = request.args.get('token') or request.form.get('token')
            if not token or token != WEB_CONFIG['access_token']:
                client_ip = get_real_ip(request)
                record_ip_access(client_ip, 'token_fail')
                fail_count = get_token_fail_count(client_ip)
                return render_token_error_page(fail_count, WEB_CONFIG), 403
            record_ip_access(request.remote_addr, 'token_success', extract_device_info(request))
            return f(*args, **kwargs)
        return decorated
    return decorator

def require_auth(WEB_CONFIG):
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            cleanup_expired_sessions()
            cookie = request.cookies.get('elaina_admin_session')
            if cookie:
                is_valid, token = verify_cookie_value(cookie)
                if is_valid and token in valid_sessions:
                    info = valid_sessions[token]
                    if datetime.now() < info['expires']:
                        real_ip = get_real_ip(request)
                        if info.get('ip') == real_ip and info.get('user_agent', '')[:200] == request.headers.get('User-Agent', '')[:200]:
                            return f(*args, **kwargs)
                    else:
                        del valid_sessions[token]
                        save_session_data()
            return render_template('login.html', token=request.args.get('token', ''), web_interface=WEB_CONFIG)
        return decorated
    return decorator

def require_socketio_token(WEB_CONFIG):
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            cleanup_expired_ip_bans()
            cleanup_expired_sessions()
            client_ip = get_real_ip(request)
            if is_ip_banned(client_ip):
                return False
            token = request.args.get('token')
            if not token or token != WEB_CONFIG['access_token']:
                return False
            cookie = request.cookies.get('elaina_admin_session')
            if not cookie:
                return False
            is_valid, session_token = verify_cookie_value(cookie)
            if not is_valid or session_token not in valid_sessions:
                return False
            info = valid_sessions[session_token]
            if datetime.now() >= info['expires']:
                del valid_sessions[session_token]
                save_session_data()
                return False
            if info.get('ip') != client_ip or info.get('user_agent', '')[:200] != request.headers.get('User-Agent', '')[:200]:
                del valid_sessions[session_token]
                save_session_data()
                return False
            record_ip_access(client_ip, 'token_success', extract_device_info(request))
            return f(*args, **kwargs)
        return decorated
    return decorator

def check_ip_ban(WEB_CONFIG):
    def decorator(f):
        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            cleanup_expired_ip_bans()
            client_ip = get_real_ip(request)
            if is_ip_banned(client_ip):
                fail_count = get_token_fail_count(client_ip)
                return render_token_error_page(max(fail_count, _MAX_FAIL_COUNT), WEB_CONFIG), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator

load_ip_data()
load_session_data()
