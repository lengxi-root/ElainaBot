import os, sys, threading, traceback, functools, logging
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, Blueprint, make_response
from flask_socketio import SocketIO
from flask_cors import CORS
from config import LOG_DB_CONFIG, WEB_CONFIG, ROBOT_QQ, appid, WEBSOCKET_CONFIG
from function.log_db import add_log_to_db, add_sent_message_to_db
from core.event.MessageEvent import MessageEvent

logger = logging.getLogger('ElainaBot.web')

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tools')
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from web.tools import session_manager, log_handler, system_info, log_query, update_handler, robot_info, status_routes

PREFIX = '/web'
_COOKIE_SECRET = 'elaina_cookie_secret_key_2024_v1'
_SESSION_DAYS = 7
_SESSION_MAX_AGE = 604800

# 基础CSP配置
_BASE_CSP = {
    'default-src': ["'self'"],
    'script-src': ["'self'", "'unsafe-inline'", "cdn.jsdelivr.net", "cdnjs.cloudflare.com"],
    'style-src': ["'self'", "'unsafe-inline'", "cdn.jsdelivr.net", "cdnjs.cloudflare.com"],
    'font-src': ["'self'", "cdn.jsdelivr.net", "cdnjs.cloudflare.com"],
    'img-src': ["'self'", "data:", "*.myqcloud.com", "thirdqq.qlogo.cn", "*.qlogo.cn", "http://*.qlogo.cn", "*.nt.qq.com.cn", "api.2dcode.biz"],
    'connect-src': ["'self'", "i.elaina.vin"]
}

_SECURITY_HEADERS_BASE = (
    ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'), ('X-XSS-Protection', '1; mode=block'),
    ('Referrer-Policy', 'strict-origin-when-cross-origin'), ('Permissions-Policy', 'geolocation=(), microphone=(), camera=()'),
    ('Strict-Transport-Security', 'max-age=0'), ('Cache-Control', 'no-cache, no-store, must-revalidate'), ('Pragma', 'no-cache'), ('Expires', '0')
)

def _build_csp_header():
    """构建动态CSP头，包含插件注册的域名"""
    try:
        from core.plugin.PluginManager import PluginManager
        plugin_csp = PluginManager.get_csp_domains()
        
        # 合并基础CSP和插件CSP
        merged_csp = {}
        for directive, domains in _BASE_CSP.items():
            merged_csp[directive] = set(domains)
        
        for directive, domains in plugin_csp.items():
            if directive not in merged_csp:
                merged_csp[directive] = set()
            merged_csp[directive].update(domains)
        
        # 构建CSP字符串
        csp_parts = []
        for directive, domains in merged_csp.items():
            csp_parts.append(f"{directive} {' '.join(sorted(domains))}")
        
        return '; '.join(csp_parts)
    except Exception as e:
        logger.error(f"构建CSP头失败: {e}")
        # 降级到基础CSP
        csp_parts = [f"{directive} {' '.join(domains)}" for directive, domains in _BASE_CSP.items()]
        return '; '.join(csp_parts)

def _get_security_headers():
    """获取包含动态CSP的安全头"""
    csp_header = _build_csp_header()
    return _SECURITY_HEADERS_BASE + (('Content-Security-Policy', csp_header),)

web = Blueprint('web', __name__, template_folder='templates', static_folder='static')
socketio = None

valid_sessions = session_manager.valid_sessions
ip_access_data = session_manager.ip_access_data
message_logs = log_handler.message_logs
framework_logs = log_handler.framework_logs
error_logs = log_handler.error_logs

extract_device_info = session_manager.extract_device_info
record_ip_access = session_manager.record_ip_access
cleanup_expired_sessions = session_manager.cleanup_expired_sessions
limit_session_count = session_manager.limit_session_count
generate_session_token = session_manager.generate_session_token
sign_cookie_value = session_manager.sign_cookie_value
verify_cookie_value = session_manager.verify_cookie_value
save_session_data = session_manager.save_session_data
get_real_ip = session_manager.get_real_ip

add_display_message = log_handler.add_display_message
add_plugin_log = log_handler.add_plugin_log
add_framework_log = log_handler.add_framework_log
add_error_log = log_handler.add_error_log
get_system_info = system_info.get_system_info
get_websocket_status = system_info.get_websocket_status

system_info.set_start_time(datetime.now())
system_info.set_error_log_func(add_error_log)
log_query.set_log_queues(message_logs, framework_logs, error_logs)
log_query.set_config(LOG_DB_CONFIG, add_error_log)
status_routes.set_log_queues(message_logs, framework_logs)
robot_info.set_config(ROBOT_QQ, appid, WEBSOCKET_CONFIG, get_websocket_status)

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

api_error_response = lambda msg, code=500, **e: create_response(False, error=msg, status_code=code, **e)
api_success_response = lambda data=None, **e: create_response(True, data=data, **e)
openapi_error_response = lambda msg, code=200: create_response(False, error=msg, status_code=code, response_type='openapi')
openapi_success_response = lambda data=None, **e: create_response(True, data=data, response_type='openapi', **e)

def catch_error(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            tb = traceback.format_exc()
            try:
                add_log_to_db('error', {'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'content': f"{func.__name__} 错误: {str(e)}", 'traceback': tb})
            except:
                pass
            logger.error(f"catch_error捕获异常 in {func.__name__}: {str(e)}\n{tb}")
            return api_error_response(str(e))
    return wrapper

require_token = session_manager.require_token(WEB_CONFIG)
require_auth = session_manager.require_auth(WEB_CONFIG)
require_socketio_token = session_manager.require_socketio_token(WEB_CONFIG)
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

from web.tools.bot_restart import execute_bot_restart
from web.tools.openapi_handler import (openapi_user_data, handle_start_login, handle_check_login, handle_get_botlist,
    handle_get_botdata, handle_get_notifications, handle_logout, handle_get_login_status, handle_import_templates,
    handle_verify_saved_login, handle_get_templates, handle_get_template_detail, handle_render_button_template,
    handle_get_whitelist, handle_update_whitelist, handle_get_delete_qr, handle_check_delete_auth, handle_execute_delete_ip,
    handle_batch_add_whitelist, handle_create_template_qr, handle_check_template_qr, handle_preview_template,
    handle_submit_template, handle_audit_templates, handle_delete_templates)
from web.tools.message_handler import (handle_get_chats, handle_get_chat_history, handle_send_message,
    handle_get_nickname, handle_get_nicknames_batch, handle_get_markdown_templates, handle_get_markdown_templates_detail)
from web.tools.statistics_handler import (handle_get_statistics, handle_get_statistics_task_status,
    handle_get_all_statistics_tasks, handle_complete_dau, handle_get_user_nickname, handle_get_available_dates)
from web.tools.config_handler import (handle_get_config, handle_parse_config, handle_update_config_items,
    handle_save_config, handle_check_pending_config, handle_cancel_pending_config,
    handle_get_message_templates, handle_save_message_templates, handle_parse_message_templates)
from web.tools.plugin_manager import (handle_toggle_plugin, handle_read_plugin, handle_save_plugin,
    handle_create_plugin, handle_create_plugin_folder, handle_get_plugin_folders, handle_upload_plugin, scan_plugins_internal)
from web.tools.plugin_market_handler import (handle_market_submit, handle_market_list, handle_market_pending,
    handle_market_review, handle_market_update_status, handle_market_delete, handle_market_categories,
    handle_market_export, handle_market_download, handle_market_preview, handle_market_install, handle_market_local_plugins,
    handle_market_upload_local, handle_market_register, handle_market_login, handle_market_user_info,
    handle_market_plugin_detail, handle_market_author_update, handle_market_author_delete, handle_market_update_local,
    handle_market_get_source, handle_market_save_source, handle_local_plugin_read, handle_local_plugin_save,
    handle_market_upload_direct, handle_market_update_plugin_code)

status_routes.set_restart_function(execute_bot_restart)
check_openapi_login = lambda uid: openapi_user_data.get(uid)
scan_plugins = scan_plugins_internal

@web.route('/login', methods=['POST'])
@check_ip_ban
@require_token
@safe_route
def login():
    password, token = request.form.get('password'), request.form.get('token')
    if password == WEB_CONFIG['admin_password']:
        cleanup_expired_sessions()
        limit_session_count()
        current_ip = get_real_ip(request)
        record_ip_access(current_ip, 'password_success', extract_device_info(request))
        current_ua = request.headers.get('User-Agent', '')[:200]
        now = datetime.now()
        expires = now + timedelta(days=_SESSION_DAYS)
        
        existing = next((t for t, info in valid_sessions.items() if info.get('ip') == current_ip and now < info['expires']), None)
        if existing:
            valid_sessions[existing].update({'expires': expires, 'user_agent': current_ua})
            session_token = existing
        else:
            session_token = generate_session_token()
            valid_sessions[session_token] = {'created': now, 'expires': expires, 'ip': current_ip, 'user_agent': current_ua}
        save_session_data()
        
        response = make_response(f'<script>window.location.href = "/web/?token={token}";</script>')
        is_https = request.is_secure or request.headers.get('X-Forwarded-Proto') == 'https'
        response.set_cookie('elaina_admin_session', sign_cookie_value(session_token), max_age=_SESSION_MAX_AGE,
            expires=expires, httponly=True, secure=is_https, samesite='None' if is_https else 'Lax', path='/')
        return response
    record_ip_access(request.remote_addr, 'password_fail')
    return render_template('login.html', token=token, error='密码错误，请重试', web_interface=WEB_CONFIG)

@web.route('/')
@full_auth
def index():
    from core.plugin.PluginManager import PluginManager
    response = make_response(render_template('index.html', prefix=PREFIX, ROBOT_QQ=ROBOT_QQ, appid=appid,
        WEBSOCKET_CONFIG=WEBSOCKET_CONFIG, web_interface=WEB_CONFIG, plugin_routes=PluginManager.get_web_routes()))
    for h, v in _get_security_headers():
        response.headers[h] = v
    return response

@web.route('/plugin_market')
@full_auth
def plugin_market_page():
    return render_template('pc/plugin_market.html')

@web.route('/plugin/<plugin_path>')
@full_auth
def plugin_page(plugin_path):
    from core.plugin.PluginManager import PluginManager
    routes = PluginManager.get_web_routes()
    if plugin_path not in routes:
        return jsonify({'error': '插件页面不存在'}), 404
    info = routes[plugin_path]
    try:
        handler = getattr(info['class'], info['handler'], None)
        if not handler:
            return jsonify({'error': f"插件处理函数 {info['handler']} 不存在"}), 500
        result = handler()
        if isinstance(result, dict):
            return jsonify({'success': True, 'html': result.get('html', ''), 'script': result.get('script', ''),
                'css': result.get('css', ''), 'title': info['menu_name']})
        elif isinstance(result, str):
            return jsonify({'success': True, 'html': result, 'script': '', 'css': '', 'title': info['menu_name']})
        return jsonify({'error': '插件返回格式错误'}), 500
    except Exception as e:
        add_error_log(f"插件页面加载失败: {plugin_path}", traceback.format_exc())
        return jsonify({'error': f'插件页面加载失败: {str(e)}'}), 500

@web.route('/api/plugin/<path:api_path>', methods=['GET', 'POST', 'PUT', 'DELETE'])
@check_ip_ban
def handle_plugin_api(api_path):
    from core.plugin.PluginManager import PluginManager
    full_path = f'/api/{api_path}'
    routes = PluginManager.get_api_routes()
    if full_path not in routes:
        return jsonify({'success': False, 'message': 'API路由不存在'}), 404
    info = routes[full_path]
    if request.method not in info.get('methods', ['GET']):
        return jsonify({'success': False, 'message': f'不支持的请求方法: {request.method}'}), 405
    if info.get('require_token', True):
        token = request.args.get('token') or request.form.get('token')
        if not token or token != WEB_CONFIG['access_token']:
            return jsonify({'success': False, 'message': '无效的token'}), 403
    if info.get('require_auth', True):
        cleanup_expired_sessions()
        cookie = request.cookies.get('elaina_admin_session')
        if not cookie:
            return jsonify({'success': False, 'message': '未登录'}), 401
        is_valid, st = verify_cookie_value(cookie)
        if not is_valid or st not in valid_sessions:
            return jsonify({'success': False, 'message': '会话无效'}), 401
        if datetime.now() >= valid_sessions[st]['expires']:
            del valid_sessions[st]
            save_session_data()
            return jsonify({'success': False, 'message': '会话已过期'}), 401
    try:
        handler = getattr(info['class'], info['handler'], None)
        if not handler:
            return jsonify({'success': False, 'message': f"处理函数 {info['handler']} 不存在"}), 500
        data = request.args.to_dict() if request.method == 'GET' else (request.get_json() or {})
        result = handler(data)
        return jsonify(result) if isinstance(result, dict) else jsonify({'success': True, 'data': result})
    except Exception as e:
        add_error_log(f"插件API处理失败: {full_path}", traceback.format_exc())
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

@web.route('/api/update/sources')
@token_auth
def get_download_sources():
    return update_handler.handle_get_download_sources()

@web.route('/api/update/sources', methods=['POST'])
@token_auth
def set_download_source():
    return update_handler.handle_set_download_source()

@web.route('/api/update/test_source', methods=['POST'])
@token_auth
def test_download_source():
    return update_handler.handle_test_download_source()

@web.route('/api/config/apply_diff', methods=['POST'])
@token_auth
def apply_config_diff():
    return update_handler.handle_apply_config_diff()

@web.route('/api/update/upload', methods=['POST'])
@token_auth
def upload_update():
    return update_handler.handle_upload_update()

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

@web.route('/api/config/message_templates', methods=['GET'])
@token_auth
def get_message_templates():
    return handle_get_message_templates()

@web.route('/api/config/message_templates', methods=['POST'])
@token_auth
def save_message_templates():
    return handle_save_message_templates()

@web.route('/api/config/message_templates/parse', methods=['GET'])
@token_auth
def parse_message_templates():
    return handle_parse_message_templates()

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

@web.route('/api/market/submit', methods=['POST'])
@safe_route
def market_submit():
    return handle_market_submit()

@web.route('/api/market/list', methods=['GET'])
@safe_route
def market_list():
    return handle_market_list()

@web.route('/api/market/pending', methods=['GET'])
@safe_route
def market_pending():
    return handle_market_pending()

@web.route('/api/market/review', methods=['POST'])
@safe_route
def market_review():
    return handle_market_review()

@web.route('/api/market/update_status', methods=['POST'])
@safe_route
def market_update_status():
    return handle_market_update_status()

@web.route('/api/market/delete', methods=['POST'])
@safe_route
def market_delete():
    return handle_market_delete()

@web.route('/api/market/categories', methods=['GET'])
@safe_route
def market_categories():
    return handle_market_categories()

@web.route('/api/market/export', methods=['GET'])
@safe_route
def market_export():
    return handle_market_export()

@web.route('/api/market/download', methods=['POST'])
@safe_route
def market_download():
    return handle_market_download()

@web.route('/api/market/preview', methods=['POST'])
@safe_route
def market_preview():
    return handle_market_preview()

@web.route('/api/market/install', methods=['POST'])
@full_auth
def market_install():
    return handle_market_install()

@web.route('/api/market/local_plugins', methods=['GET'])
@full_auth
def market_local_plugins():
    return handle_market_local_plugins()

@web.route('/api/market/upload_local', methods=['POST'])
@full_auth
def market_upload_local():
    return handle_market_upload_local()

@web.route('/api/market/register', methods=['POST'])
@safe_route
def market_register():
    return handle_market_register()

@web.route('/api/market/login', methods=['POST'])
@safe_route
def market_login():
    return handle_market_login()

@web.route('/api/market/user_info', methods=['POST'])
@safe_route
def market_user_info():
    return handle_market_user_info()

@web.route('/api/market/plugin_detail', methods=['POST'])
@safe_route
def market_plugin_detail():
    return handle_market_plugin_detail()

@web.route('/api/market/author_update', methods=['POST'])
@safe_route
def market_author_update():
    return handle_market_author_update()

@web.route('/api/market/author_delete', methods=['POST'])
@safe_route
def market_author_delete():
    return handle_market_author_delete()

@web.route('/api/market/update_local', methods=['POST'])
@safe_route
def market_update_local():
    return handle_market_update_local()

@web.route('/api/market/get_source', methods=['POST'])
@safe_route
def market_get_source():
    return handle_market_get_source()

@web.route('/api/market/save_source', methods=['POST'])
@safe_route
def market_save_source():
    return handle_market_save_source()

@web.route('/api/market/local_read', methods=['POST'])
@safe_route
def market_local_read():
    return handle_local_plugin_read()

@web.route('/api/market/local_save', methods=['POST'])
@safe_route
def market_local_save():
    return handle_local_plugin_save()

@web.route('/api/market/upload_direct', methods=['POST'])
@safe_route
def market_upload_direct():
    return handle_market_upload_direct()

@web.route('/api/market/update_plugin_code', methods=['POST'])
@safe_route
def market_update_plugin_code():
    return handle_market_update_plugin_code()

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

@web.route('/openapi/create_template_qr', methods=['POST'])
@simple_auth
def openapi_create_template_qr():
    return handle_create_template_qr(check_openapi_login, openapi_error_response)

@web.route('/openapi/check_template_qr', methods=['POST'])
@simple_auth
def openapi_check_template_qr():
    return handle_check_template_qr(check_openapi_login, openapi_error_response)

@web.route('/openapi/preview_template', methods=['POST'])
@simple_auth
def openapi_preview_template():
    return handle_preview_template(check_openapi_login, openapi_error_response)

@web.route('/openapi/submit_template', methods=['POST'])
@simple_auth
def openapi_submit_template():
    return handle_submit_template(check_openapi_login, openapi_error_response)

@web.route('/openapi/audit_templates', methods=['POST'])
@simple_auth
def openapi_audit_templates():
    return handle_audit_templates(check_openapi_login, openapi_error_response)

@web.route('/openapi/delete_templates', methods=['POST'])
@simple_auth
def openapi_delete_templates():
    return handle_delete_templates(check_openapi_login, openapi_error_response)

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

@web.route('/api/config/markdown_templates', methods=['GET'])
@token_auth
def get_markdown_templates_detail():
    return handle_get_markdown_templates_detail()

_LOGS_MAP = {
    'received': lambda: log_handler.received_handler.logs,
    'plugin': lambda: log_handler.plugin_handler.logs,
    'framework': lambda: log_handler.framework_handler.logs,
    'error': lambda: log_handler.error_handler.logs
}

@catch_error
def register_socketio_handlers(sio):
    @sio.on('connect', namespace=PREFIX)
    @require_socketio_token
    def handle_connect():
        sid = request.sid
        def load_data():
            try:
                sio.emit('system_info', get_system_info(), room=sid, namespace=PREFIX)
            except:
                pass
            try:
                sio.emit('plugins_update', scan_plugins(), room=sid, namespace=PREFIX)
                logs_data = {}
                for t, getter in _LOGS_MAP.items():
                    logs = list(getter())[-30:]
                    logs.reverse()
                    logs_data[t] = {'logs': logs, 'total': len(getter()), 'page': 1, 'page_size': 30}
                sio.emit('logs_batch', logs_data, room=sid, namespace=PREFIX)
            except:
                pass
        threading.Thread(target=load_data, daemon=True).start()

    @sio.on('disconnect', namespace=PREFIX)
    def handle_disconnect():
        pass

    @sio.on('get_system_info', namespace=PREFIX)
    @require_socketio_token
    def handle_get_system_info():
        sio.emit('system_info', get_system_info(), room=request.sid, namespace=PREFIX)

    @sio.on('get_plugins_info', namespace=PREFIX)
    @require_socketio_token
    def handle_get_plugins_info():
        sio.emit('plugins_update', scan_plugins(), room=request.sid, namespace=PREFIX)

    @sio.on('request_logs', namespace=PREFIX)
    @require_socketio_token
    def handle_request_logs(data):
        log_type = data.get('type', 'received')
        page = data.get('page', 1)
        page_size = data.get('page_size', 50)
        getter = _LOGS_MAP.get(log_type)
        if not getter:
            return
        logs = list(getter())
        logs.reverse()
        start = (page - 1) * page_size
        sio.emit('logs_update', {'type': log_type, 'logs': logs[start:start+page_size] if start < len(logs) else [],
            'total': len(logs), 'page': page, 'page_size': page_size}, room=request.sid, namespace=PREFIX)

def start_web(main_app=None, is_subprocess=False):
    global socketio
    def init_socketio(app):
        global socketio
        try:
            socketio = SocketIO(app, cors_allowed_origins="*", path="/socket.io", async_mode='eventlet', logger=False, engineio_logger=False)
            log_handler.set_socketio(socketio)
            register_socketio_handlers(socketio)
        except Exception as e:
            add_log_to_db('error', {'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'content': f"Socket.IO初始化错误: {str(e)}", 'traceback': traceback.format_exc()})
    
    if main_app is None:
        app = Flask(__name__)
        app.register_blueprint(web, url_prefix=PREFIX)
        CORS(app, resources={r"/*": {"origins": "*"}})
        init_socketio(app)
        return app, socketio
    else:
        if 'web' not in [bp.name for bp in main_app.blueprints.values()]:
            main_app.register_blueprint(web, url_prefix=PREFIX)
        try:
            CORS(main_app, resources={r"/*": {"origins": "*"}})
        except:
            pass
        if hasattr(main_app, 'socketio'):
            socketio = main_app.socketio
            log_handler.set_socketio(socketio)
            register_socketio_handlers(socketio)
        else:
            init_socketio(main_app)
            main_app.socketio = socketio
        return None
