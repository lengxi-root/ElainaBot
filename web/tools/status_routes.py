import os, sys, traceback
from datetime import datetime
from flask import request, jsonify

received_messages = None
plugin_logs = None
framework_logs = None
execute_bot_restart = None

def set_log_queues(received, plugin, framework):
    global received_messages, plugin_logs, framework_logs
    received_messages = received
    plugin_logs = plugin
    framework_logs = framework

def set_restart_function(restart_func):
    global execute_bot_restart
    execute_bot_restart = restart_func

def handle_status():
    return jsonify({
        'status': 'ok',
        'version': '1.0',
        'logs_count': {
            'received': len(received_messages) if received_messages else 0,
            'plugin': len(plugin_logs) if plugin_logs else 0,
            'framework': len(framework_logs) if framework_logs else 0
        }
    })

def handle_get_system_status():
    try:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        
        try:
            import config
            web_dual_process = getattr(config, 'SERVER_CONFIG', {}).get('web_dual_process', False)
            websocket_enabled = getattr(config, 'WEBSOCKET_CONFIG', {}).get('enabled', False)
        except:
            web_dual_process = False
            websocket_enabled = False
        
        current_pid = os.getpid()
        websocket_available = False
        
        if not web_dual_process:
            if websocket_enabled:
                websocket_available = True
            else:
                try:
                    import psutil
                    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                        if proc.info['pid'] == current_pid:
                            continue
                        if not proc.info['cmdline']:
                            continue
                        
                        cmdline = ' '.join(proc.info['cmdline'])
                        if 'main.py' in cmdline and 'ElainaBot' in cmdline:
                            websocket_available = True
                            break
                except:
                    websocket_available = False
        
        return jsonify({
            'success': True,
            'standalone_web': web_dual_process,
            'websocket_available': websocket_available,
            'websocket_enabled': websocket_enabled,
            'process_id': current_pid,
            'config_source': 'config.py'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'standalone_web': True,
            'websocket_available': False,
            'error': str(e),
            'config_source': 'fallback'
        })

def handle_restart_bot():
    try:
        request_data = request.get_json(silent=True) or {}
        
        restart_status = {
            'restart_time': request_data.get('restart_time') or datetime.now().isoformat(),
            'completed': False,
            'message_id': request_data.get('message_id'),
            'user_id': request_data.get('user_id', 'web_admin'),
            'group_id': request_data.get('group_id', 'web_panel')
        }
        
        result = execute_bot_restart(restart_status)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500

def handle_get_simple_status():
    try:
        return jsonify({
            'status': 'ok',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

