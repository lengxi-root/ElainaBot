import os, sys, traceback
from datetime import datetime
from flask import request, jsonify

message_logs = None
framework_logs = None
execute_bot_restart = None

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def set_log_queues(message, framework):
    global message_logs, framework_logs
    message_logs, framework_logs = message, framework

def set_restart_function(restart_func):
    global execute_bot_restart
    execute_bot_restart = restart_func

def handle_status():
    return jsonify({
        'status': 'ok', 'version': '1.0',
        'logs_count': {'message': len(message_logs) if message_logs else 0, 'framework': len(framework_logs) if framework_logs else 0}
    })

def handle_get_system_status():
    try:
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)
        
        try:
            import config
            ws_enabled = getattr(config, 'WEBSOCKET_CONFIG', {}).get('enabled', False)
        except:
            ws_enabled = False
        
        pid = os.getpid()
        ws_available = ws_enabled
        
        if not ws_enabled:
            try:
                import psutil
                for proc in psutil.process_iter(['pid', 'cmdline']):
                    if proc.info['pid'] != pid and proc.info['cmdline'] and 'main.py' in ' '.join(proc.info['cmdline']) and 'ElainaBot' in ' '.join(proc.info['cmdline']):
                        ws_available = True
                        break
            except:
                pass
        
        return jsonify({'success': True, 'websocket_available': ws_available, 'websocket_enabled': ws_enabled, 'process_id': pid, 'config_source': 'config.py'})
    except Exception as e:
        return jsonify({'success': False, 'websocket_available': False, 'error': str(e), 'config_source': 'fallback'})

def handle_restart_bot():
    try:
        data = request.get_json(silent=True) or {}
        result = execute_bot_restart({
            'restart_time': data.get('restart_time') or datetime.now().isoformat(),
            'completed': False, 'message_id': data.get('message_id'),
            'user_id': data.get('user_id', 'web_admin'), 'group_id': data.get('group_id', 'web_panel')
        })
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500

def handle_get_simple_status():
    try:
        return jsonify({'status': 'ok', 'timestamp': datetime.now().isoformat()})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500
