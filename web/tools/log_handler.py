import functools
from collections import deque
from datetime import datetime

MAX_LOGS = 1000
received_messages = deque(maxlen=MAX_LOGS)
plugin_logs = deque(maxlen=MAX_LOGS)
framework_logs = deque(maxlen=MAX_LOGS)
error_logs = deque(maxlen=MAX_LOGS)

socketio = None
PREFIX = '/web'

LOG_DB_CONFIG = None
add_log_to_db = None

def set_socketio(sio):
    global socketio
    socketio = sio

def set_log_db_config(config, add_log_func):
    global LOG_DB_CONFIG, add_log_to_db
    LOG_DB_CONFIG = config
    add_log_to_db = add_log_func

def catch_error(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            pass
    return wrapper

class LogHandler:
    def __init__(self, log_type, max_logs=MAX_LOGS):
        self.log_type = log_type
        self.logs = deque(maxlen=max_logs)
        self.global_logs = {
            'received': received_messages,
            'plugin': plugin_logs,
            'framework': framework_logs,
            'error': error_logs
        }[log_type]
    
    def add(self, content, traceback_info=None, skip_db=False):
        if isinstance(content, dict):
            entry = content.copy()
        else:
            entry = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'content': content
            }
        
        if 'timestamp' not in entry:
            entry['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if traceback_info:
            entry['traceback'] = traceback_info
        
        self.logs.append(entry)
        self.global_logs.append(entry)
        
        if not skip_db and LOG_DB_CONFIG and LOG_DB_CONFIG.get('enabled'):
            try:
                if add_log_to_db:
                    add_log_to_db(self.log_type, entry)
            except:
                pass
        
        if socketio:
            try:
                emit_data = {
                    'type': self.log_type,
                    'data': {
                        k: entry[k] for k in ['timestamp', 'content'] + 
                        (['traceback'] if 'traceback' in entry else [])
                    }
                }
                socketio.emit('new_message', emit_data, namespace=PREFIX)
            except:
                pass
        
        return entry

received_handler = LogHandler('received')
plugin_handler = LogHandler('plugin')
framework_handler = LogHandler('framework')
error_handler = LogHandler('error')

@catch_error
def add_display_message(formatted_message, timestamp=None, user_id=None, group_id=None, message_content=None):
    if user_id is not None and message_content is not None:
        entry = {
            'timestamp': timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'content': formatted_message,
            'user_id': user_id,
            'group_id': group_id or '-',
            'message': message_content
        }
    else:
        entry = {
            'timestamp': timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'content': formatted_message
        }
    
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
    if isinstance(log, str):
        log_data = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'content': log,
            'user_id': user_id or '',
            'group_id': group_id or 'c2c',
            'plugin_name': plugin_name or ''
        }
    else:
        if isinstance(log, dict):
            log_data = log.copy()
        else:
            log_data = {'content': str(log)}
        
        log_data['user_id'] = user_id or ''
        log_data['group_id'] = group_id or 'c2c'
        log_data['plugin_name'] = plugin_name or ''
    
    return plugin_handler.add(log_data)

@catch_error
def add_framework_log(log):
    return framework_handler.add(log)

@catch_error
def add_error_log(log, traceback_info=None):
    return error_handler.add(log, traceback_info)

def get_logs_data(log_type):
    handlers = {
        'received': received_handler,
        'plugin': plugin_handler,
        'framework': framework_handler,
        'error': error_handler
    }
    
    handler = handlers.get(log_type)
    if handler:
        return list(handler.logs)
    return []
