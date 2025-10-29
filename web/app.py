import os, sys, threading, traceback, functools, logging
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, Blueprint, make_response
from flask_socketio import SocketIO
from flask_cors import CORS
from config import LOG_DB_CONFIG, WEB_SECURITY, WEB_INTERFACE, ROBOT_QQ, appid, WEBSOCKET_CONFIG
from function.log_db import add_log_to_db, add_sent_message_to_db
from core.event.MessageEvent import MessageEvent

# 创建 Web 模块 logger
logger = logging.getLogger('ElainaBot.web')

try:
    from web.tools import (
        session_manager,
        log_handler,
        system_info,
        log_query,
        update_handler,
        robot_info,
        sandbox_handler,
        status_routes
    )
except ImportError:
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import session_manager
    import log_handler
    import system_info
    import log_query
    import update_handler
    import robot_info
    import sandbox_handler
    import status_routes

PREFIX = '/web'
web = Blueprint('web', __name__, 
                     template_folder='templates',
                     static_folder='static')
socketio = None
plugins_info = []

valid_sessions = session_manager.valid_sessions
ip_access_data = session_manager.ip_access_data

received_messages = log_handler.received_messages
plugin_logs = log_handler.plugin_logs
framework_logs = log_handler.framework_logs
error_logs = log_handler.error_logs

START_TIME = system_info.START_TIME

log_handler.set_log_db_config(LOG_DB_CONFIG, add_log_to_db)

system_info.set_start_time(datetime.now())

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

extract_device_info = session_manager.extract_device_info
record_ip_access = session_manager.record_ip_access
is_ip_banned = session_manager.is_ip_banned
cleanup_expired_ip_bans = session_manager.cleanup_expired_ip_bans
cleanup_expired_sessions = session_manager.cleanup_expired_sessions
limit_session_count = session_manager.limit_session_count
generate_session_token = session_manager.generate_session_token
sign_cookie_value = session_manager.sign_cookie_value
verify_cookie_value = session_manager.verify_cookie_value
save_session_data = session_manager.save_session_data

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

sandbox_handler.set_response_functions(api_success_response, api_error_response)

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

require_token = session_manager.require_token(WEB_SECURITY)
require_auth = session_manager.require_auth(WEB_SECURITY, WEB_INTERFACE)
require_socketio_token = session_manager.require_socketio_token(WEB_SECURITY)
check_ip_ban = session_manager.check_ip_ban

def full_auth(func):
    @functools.wraps(func)
    @check_ip_ban
    @require_token
    @require_auth
    @catch_error
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

def simple_auth(func):
    @functools.wraps(func)
    @require_auth
    @catch_error
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

def safe_route(func):
    @functools.wraps(func)
    @catch_error
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

def token_auth(func):
    @functools.wraps(func)
    @require_token
    @require_auth
    @catch_error
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

add_display_message = log_handler.add_display_message
add_plugin_log = log_handler.add_plugin_log
add_framework_log = log_handler.add_framework_log
add_error_log = log_handler.add_error_log

system_info.set_error_log_func(add_error_log)

log_query.set_log_queues(received_messages, plugin_logs, framework_logs, error_logs)
log_query.set_config(LOG_DB_CONFIG, add_error_log)

status_routes.set_log_queues(received_messages, plugin_logs, framework_logs)

sandbox_handler.set_message_event_class(MessageEvent)

@web.route('/login', methods=['POST'])
@check_ip_ban
@require_token
@safe_route
def login():
    password, token = request.form.get('password'), request.form.get('token')
    if password == WEB_SECURITY['admin_password']:
        cleanup_expired_sessions()
        limit_session_count()
        record_ip_access(request.remote_addr, 'password_success', extract_device_info(request))
        
        current_ip = request.remote_addr
        current_ua = request.headers.get('User-Agent', '')[:200]
        existing_session_token = None
        
        for session_token, session_info in valid_sessions.items():
            if (session_info.get('ip') == current_ip and 
                datetime.now() < session_info['expires']):
                existing_session_token = session_token
                break
        
        if existing_session_token:
            session_token = existing_session_token
            expires = datetime.now() + timedelta(days=7)
            valid_sessions[session_token]['expires'] = expires
            valid_sessions[session_token]['user_agent'] = current_ua
        else:
            session_token = generate_session_token()
            expires = datetime.now() + timedelta(days=7)
            valid_sessions[session_token] = {
                'created': datetime.now(), 
                'expires': expires, 
                'ip': current_ip, 
                'user_agent': current_ua
            }
        
        save_session_data()
        
        response = make_response(f'<script>window.location.href = "/web/?token={token}";</script>')
        cookie_expires = datetime.now() + timedelta(days=7)
        response.set_cookie(
            'elaina_admin_session', 
            sign_cookie_value(session_token, 'elaina_cookie_secret_key_2024_v1'), 
            max_age=604800,
            expires=cookie_expires,
            httponly=True, 
            secure=False, 
            samesite='Lax', 
            path='/'
        )
        return response
    record_ip_access(request.remote_addr, 'password_fail')
    return render_template('login.html', token=token, error='密码错误，请重试', web_interface=WEB_INTERFACE)

@web.route('/')
@full_auth
def index():
    from core.plugin.PluginManager import PluginManager
    plugin_routes = PluginManager.get_web_routes()
    
    response = make_response(render_template('index.html', prefix=PREFIX, device_type='pc', ROBOT_QQ=ROBOT_QQ, appid=appid, WEBSOCKET_CONFIG=WEBSOCKET_CONFIG, web_interface=WEB_INTERFACE, plugin_routes=plugin_routes))
    for header, value in [('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'), ('X-XSS-Protection', '1; mode=block'),
        ('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' cdn.jsdelivr.net cdnjs.cloudflare.com; font-src 'self' cdn.jsdelivr.net cdnjs.cloudflare.com; img-src 'self' data: *.myqcloud.com thirdqq.qlogo.cn *.qlogo.cn api.2dcode.biz; connect-src 'self' i.elaina.vin"),
        ('Referrer-Policy', 'strict-origin-when-cross-origin'), ('Permissions-Policy', 'geolocation=(), microphone=(), camera=()'),
        ('Strict-Transport-Security', 'max-age=0'), ('Cache-Control', 'no-cache, no-store, must-revalidate'), ('Pragma', 'no-cache'), ('Expires', '0')]:
        response.headers[header] = value
    return response

@web.route('/plugin/<plugin_path>')
@full_auth
def plugin_page(plugin_path):
    from core.plugin.PluginManager import PluginManager
    
    plugin_routes = PluginManager.get_web_routes()
    
    if plugin_path not in plugin_routes:
        return jsonify({'error': '插件页面不存在'}), 404
    
    route_info = plugin_routes[plugin_path]
    plugin_class = route_info['class']
    handler_name = route_info['handler']
    
    try:
        if hasattr(plugin_class, handler_name):
            handler = getattr(plugin_class, handler_name)
            result = handler()
            
            if isinstance(result, dict):
                html_content = result.get('html', '')
                script_content = result.get('script', '')
                css_content = result.get('css', '')
                
                return jsonify({
                    'success': True,
                    'html': html_content,
                    'script': script_content,
                    'css': css_content,
                    'title': route_info['menu_name']
                })
            elif isinstance(result, str):
                return jsonify({
                    'success': True,
                    'html': result,
                    'script': '',
                    'css': '',
                    'title': route_info['menu_name']
                })
            else:
                return jsonify({'error': '插件返回格式错误'}), 500
        else:
            return jsonify({'error': f'插件处理函数 {handler_name} 不存在'}), 500
            
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        add_error_log(f"插件页面加载失败: {plugin_path}", error_trace)
        return jsonify({'error': f'插件页面加载失败: {str(e)}'}), 500

@web.route('/api/plugin/<path:api_path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@check_ip_ban
def handle_plugin_api(api_path):
    from core.plugin.PluginManager import PluginManager
    
    full_api_path = f'/api/{api_path}'
    api_routes = PluginManager.get_api_routes()
    
    if full_api_path not in api_routes:
        return jsonify({'success': False, 'message': 'API路由不存在'}), 404
    
    route_info = api_routes[full_api_path]
    
    if request.method not in route_info.get('methods', ['GET']):
        return jsonify({'success': False, 'message': f'不支持的请求方法: {request.method}'}), 405
    
    if route_info.get('require_token', True):
        token = request.args.get('token') or request.form.get('token')
        if not token or token != WEB_SECURITY['access_token']:
            return jsonify({'success': False, 'message': '无效的token'}), 403
    
    if route_info.get('require_auth', True):
        cleanup_expired_sessions()
        cookie_value = request.cookies.get('elaina_admin_session')
        if not cookie_value:
            return jsonify({'success': False, 'message': '未登录'}), 401
        
        is_valid, session_token = verify_cookie_value(cookie_value, 'elaina_cookie_secret_key_2024_v1')
        if not is_valid or session_token not in valid_sessions:
            return jsonify({'success': False, 'message': '会话无效'}), 401
        
        session_info = valid_sessions[session_token]
        if datetime.now() >= session_info['expires']:
            del valid_sessions[session_token]
            save_session_data()
            return jsonify({'success': False, 'message': '会话已过期'}), 401
    
    plugin_class = route_info['class']
    handler_name = route_info['handler']
    
    try:
        if hasattr(plugin_class, handler_name):
            handler = getattr(plugin_class, handler_name)
            
            if request.method == 'GET':
                request_data = request.args.to_dict()
            else:
                request_data = request.get_json() or {}
            
            result = handler(request_data)
            
            if isinstance(result, dict):
                return jsonify(result)
            else:
                return jsonify({'success': True, 'data': result})
        else:
            return jsonify({'success': False, 'message': f'处理函数 {handler_name} 不存在'}), 500
    
    except Exception as e:
        error_trace = traceback.format_exc()
        add_error_log(f"插件API处理失败: {full_api_path}", error_trace)
        logger.error(f"插件API错误: {str(e)}\n{error_trace}")
        return jsonify({'success': False, 'message': f'处理请求失败: {str(e)}'}), 500

@web.route('/api/logs/<log_type>')
@full_auth
def get_logs(log_type):
    return log_query.handle_get_logs(log_type)

@web.route('/api/logs/today')
@full_auth
def get_today_logs():
    return log_query.handle_get_today_logs()

@web.route('/status')
@full_auth
def status():
    return status_routes.handle_status()

@web.route('/api/statistics')
@full_auth
def get_statistics():
    return handle_get_statistics(api_success_response, api_error_response, add_error_log)

@web.route('/api/statistics/task/<task_id>')
@full_auth
def get_statistics_task_status(task_id):
    return handle_get_statistics_task_status(task_id, api_success_response, api_error_response)

@web.route('/api/statistics/tasks')
@full_auth
def get_all_statistics_tasks():
    return handle_get_all_statistics_tasks(api_success_response)

@web.route('/api/complete_dau', methods=['POST'])
@full_auth
def complete_dau():
    return handle_complete_dau(api_success_response)

@web.route('/api/get_nickname/<user_id>')
@full_auth
def get_user_nickname(user_id):
    return handle_get_user_nickname(user_id, api_success_response)

@web.route('/api/available_dates')
@full_auth
def get_available_dates():
    return handle_get_available_dates(api_success_response, add_error_log)

@web.route('/api/robot_info')
def get_robot_info():
    return robot_info.handle_get_robot_info()

@web.route('/api/robot_qrcode')
@safe_route
def get_robot_qrcode():
    return robot_info.handle_get_robot_qrcode()

@web.route('/api/changelog')
@safe_route
def get_changelog():
    return update_handler.handle_get_changelog()

@web.route('/api/update/version')
@token_auth
def get_current_version():
    return update_handler.handle_get_current_version()

@web.route('/api/update/check')
@token_auth
def check_update():
    return update_handler.handle_check_update()

@web.route('/api/update/start', methods=['POST'])
@token_auth
def start_update():
    return update_handler.handle_start_update()

@web.route('/api/update/status')
@token_auth
def get_update_status():
    return update_handler.handle_get_update_status()

@web.route('/api/update/progress')
@token_auth
def get_update_progress():
    return update_handler.handle_get_update_progress()

@web.route('/api/config/get')
@token_auth
def get_config():
    return handle_get_config()

@web.route('/api/config/parse')
@token_auth
def parse_config():
    return handle_parse_config()

@web.route('/api/config/update_items', methods=['POST'])
@token_auth
def update_config_items():
    return handle_update_config_items()

@web.route('/api/config/save', methods=['POST'])
@token_auth
def save_config():
    return handle_save_config()

@web.route('/api/config/check_pending')
@token_auth
def check_pending_config():
    return handle_check_pending_config()

@web.route('/api/config/cancel_pending', methods=['POST'])
@token_auth
def cancel_pending_config():
    return handle_cancel_pending_config()

@web.route('/api/plugin/toggle', methods=['POST'])
@full_auth
def toggle_plugin():
    return handle_toggle_plugin(add_framework_log)

@web.route('/api/plugin/read', methods=['POST'])
@full_auth
def read_plugin():
    return handle_read_plugin()

@web.route('/api/plugin/save', methods=['POST'])
@full_auth
def save_plugin():
    return handle_save_plugin(add_framework_log)

@web.route('/api/plugin/create', methods=['POST'])
@full_auth
def create_plugin():
    return handle_create_plugin(add_framework_log)

@web.route('/api/plugin/create_folder', methods=['POST'])
@full_auth
def create_plugin_folder():
    return handle_create_plugin_folder(add_framework_log)

@web.route('/api/plugin/folders', methods=['GET'])
@full_auth
def get_plugin_folders():
    return handle_get_plugin_folders()

@web.route('/api/plugin/upload', methods=['POST'])
@full_auth
def upload_plugin():
    return handle_upload_plugin(add_framework_log)

get_system_info = system_info.get_system_info
get_websocket_status = system_info.get_websocket_status

robot_info.set_config(ROBOT_QQ, appid, WEBSOCKET_CONFIG, get_websocket_status)

try:
    from web.tools.bot_restart import execute_bot_restart
except ImportError:
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from bot_restart import execute_bot_restart

status_routes.set_restart_function(execute_bot_restart)

def scan_plugins():
    return scan_plugins_internal()

@catch_error
def register_socketio_handlers(sio):
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
                        'logs': list(log_handler.received_handler.logs)[-30:],
                        'total': len(log_handler.received_handler.logs),
                        'page': 1,
                        'page_size': 30
                    },
                    'plugin': {
                        'logs': list(log_handler.plugin_handler.logs)[-30:],
                        'total': len(log_handler.plugin_handler.logs),
                        'page': 1,
                        'page_size': 30
                    },
                    'framework': {
                        'logs': list(log_handler.framework_handler.logs)[-30:],
                        'total': len(log_handler.framework_handler.logs),
                        'page': 1,
                        'page_size': 30
                    },
                    'error': {
                        'logs': list(log_handler.error_handler.logs)[-30:],
                        'total': len(log_handler.error_handler.logs),
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
            'received': log_handler.received_handler.logs,
            'plugin': log_handler.plugin_handler.logs,
            'framework': log_handler.framework_handler.logs,
            'error': log_handler.error_handler.logs
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
            log_handler.set_socketio(socketio)
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
            log_handler.set_socketio(socketio)
            register_socketio_handlers(socketio)
        except Exception as e:
            error_tb = traceback.format_exc()
            add_log_to_db('error', {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'content': f"Socket.IO初始化错误: {str(e)}",
                'traceback': error_tb
            })

        return None

@web.route('/api/sandbox/test', methods=['POST'])
@simple_auth
def sandbox_test():
    return sandbox_handler.handle_sandbox_test()

try:
    from web.tools.openapi_handler import (
        openapi_user_data,
        handle_start_login,
        handle_check_login,
        handle_get_botlist,
        handle_get_botdata,
        handle_get_notifications,
        handle_logout,
        handle_get_login_status,
        handle_import_templates,
        handle_verify_saved_login,
        handle_get_templates,
        handle_get_template_detail,
        handle_render_button_template,
        handle_get_whitelist,
        handle_update_whitelist,
        handle_get_delete_qr,
        handle_check_delete_auth,
        handle_execute_delete_ip,
        handle_batch_add_whitelist
    )
except ImportError:
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from openapi_handler import (
        openapi_user_data,
        handle_start_login,
        handle_check_login,
        handle_get_botlist,
        handle_get_botdata,
        handle_get_notifications,
        handle_logout,
        handle_get_login_status,
        handle_import_templates,
        handle_verify_saved_login,
        handle_get_templates,
        handle_get_template_detail,
        handle_render_button_template,
        handle_get_whitelist,
        handle_update_whitelist,
        handle_get_delete_qr,
        handle_check_delete_auth,
        handle_execute_delete_ip,
        handle_batch_add_whitelist
    )

check_openapi_login = lambda user_id: openapi_user_data.get(user_id)

@web.route('/openapi/start_login', methods=['POST'])
@simple_auth
def openapi_start_login():
    return handle_start_login()

@web.route('/openapi/check_login', methods=['POST'])
@simple_auth
def openapi_check_login():
    return handle_check_login()

@web.route('/openapi/get_botlist', methods=['POST'])
@simple_auth
def openapi_get_botlist():
    return handle_get_botlist(check_openapi_login)

@web.route('/openapi/get_botdata', methods=['POST'])
@simple_auth
def openapi_get_botdata():
    return handle_get_botdata(check_openapi_login, openapi_error_response, openapi_success_response)

@web.route('/openapi/get_notifications', methods=['POST'])
@simple_auth
def openapi_get_notifications():
    return handle_get_notifications(check_openapi_login, openapi_error_response, openapi_success_response)

@web.route('/openapi/logout', methods=['POST'])
@simple_auth
def openapi_logout():
    return handle_logout(openapi_success_response)

@web.route('/openapi/get_login_status', methods=['POST'])
@simple_auth
def openapi_get_login_status():
    return handle_get_login_status(check_openapi_login, openapi_success_response)

@web.route('/openapi/import_templates', methods=['POST'])
@simple_auth
def openapi_import_templates():
    return handle_import_templates(check_openapi_login, openapi_error_response)

@web.route('/openapi/verify_saved_login', methods=['POST'])
@simple_auth
def openapi_verify_saved_login():
    return handle_verify_saved_login()

@web.route('/openapi/get_templates', methods=['POST'])
@simple_auth
def openapi_get_templates():
    return handle_get_templates(check_openapi_login, openapi_error_response)

@web.route('/openapi/get_template_detail', methods=['POST'])
@simple_auth
def openapi_get_template_detail():
    return handle_get_template_detail(check_openapi_login, openapi_error_response)

@web.route('/openapi/render_button_template', methods=['POST'])
@simple_auth
def openapi_render_button_template():
    return handle_render_button_template()

@web.route('/openapi/get_whitelist', methods=['POST'])
@simple_auth
def openapi_get_whitelist():
    return handle_get_whitelist(check_openapi_login, openapi_error_response)

@web.route('/openapi/update_whitelist', methods=['POST'])
@simple_auth
def openapi_update_whitelist():
    return handle_update_whitelist(check_openapi_login, openapi_error_response)

@web.route('/openapi/get_delete_qr', methods=['POST'])
@simple_auth
def openapi_get_delete_qr():
    return handle_get_delete_qr(check_openapi_login, openapi_error_response)

@web.route('/openapi/check_delete_auth', methods=['POST'])
@simple_auth
def openapi_check_delete_auth():
    return handle_check_delete_auth(check_openapi_login, openapi_error_response)

@web.route('/openapi/execute_delete_ip', methods=['POST'])
@simple_auth
def openapi_execute_delete_ip():
    return handle_execute_delete_ip(check_openapi_login, openapi_error_response)

@web.route('/openapi/batch_add_whitelist', methods=['POST'])
@simple_auth
def openapi_batch_add_whitelist():
    return handle_batch_add_whitelist(check_openapi_login, openapi_error_response)

@web.route('/api/system/status', methods=['GET'])
def get_system_status():
    return status_routes.handle_get_system_status()

@web.route('/api/restart', methods=['POST'])
@simple_auth
def restart_bot():
    return status_routes.handle_restart_bot()

@web.route('/api/status', methods=['GET'])
def get_simple_status():
    return status_routes.handle_get_simple_status()

try:
    from web.tools.message_handler import (
        handle_get_chats,
        handle_get_chat_history,
        handle_send_message,
        handle_get_nickname,
        handle_get_nicknames_batch,
        handle_get_markdown_templates
    )
except ImportError:
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from message_handler import (
        handle_get_chats,
        handle_get_chat_history,
        handle_send_message,
        handle_get_nickname,
        handle_get_nicknames_batch,
        handle_get_markdown_templates
    )

try:
    from web.tools.statistics_handler import (
        handle_get_statistics,
        handle_get_statistics_task_status,
        handle_get_all_statistics_tasks,
        handle_complete_dau,
        handle_get_user_nickname,
        handle_get_available_dates
    )
except ImportError:
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from statistics_handler import (
        handle_get_statistics,
        handle_get_statistics_task_status,
        handle_get_all_statistics_tasks,
        handle_complete_dau,
        handle_get_user_nickname,
        handle_get_available_dates
    )

try:
    from web.tools.config_handler import (
        handle_get_config,
        handle_parse_config,
        handle_update_config_items,
        handle_save_config,
        handle_check_pending_config,
        handle_cancel_pending_config
    )
except ImportError:
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from config_handler import (
        handle_get_config,
        handle_parse_config,
        handle_update_config_items,
        handle_save_config,
        handle_check_pending_config,
        handle_cancel_pending_config
    )

try:
    from web.tools.plugin_manager import (
        handle_toggle_plugin,
        handle_read_plugin,
        handle_save_plugin,
        handle_create_plugin,
        handle_create_plugin_folder,
        handle_get_plugin_folders,
        handle_upload_plugin,
        scan_plugins_internal
    )
except ImportError:
    tools_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from plugin_manager import (
        handle_toggle_plugin,
        handle_read_plugin,
        handle_save_plugin,
        handle_create_plugin,
        handle_create_plugin_folder,
        handle_get_plugin_folders,
        handle_upload_plugin,
        scan_plugins_internal
    )

@web.route('/api/message/get_chats', methods=['POST'])
@simple_auth
def get_chats():
    return handle_get_chats(LOG_DB_CONFIG, appid)

@web.route('/api/message/get_chat_history', methods=['POST'])
@simple_auth
def get_chat_history():
    return handle_get_chat_history(LOG_DB_CONFIG, appid)

@web.route('/api/message/send', methods=['POST'])
@simple_auth
def send_message():
    return handle_send_message(LOG_DB_CONFIG, add_sent_message_to_db)

@web.route('/api/message/get_nickname', methods=['POST'])
@simple_auth
def get_nickname():
    return handle_get_nickname()

@web.route('/api/message/get_nicknames_batch', methods=['POST'])
@simple_auth
def get_nicknames_batch():
    return handle_get_nicknames_batch()

@web.route('/api/message/get_templates', methods=['GET'])
@simple_auth
def get_markdown_templates():
    return handle_get_markdown_templates()
