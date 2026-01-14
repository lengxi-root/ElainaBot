import functools
from collections import deque
from datetime import datetime

_MAX_LOGS = 1000
_PREFIX = '/web'

message_logs = deque(maxlen=_MAX_LOGS)
framework_logs = deque(maxlen=_MAX_LOGS)
error_logs = deque(maxlen=_MAX_LOGS)
_GLOBAL_LOGS = {'message': message_logs, 'framework': framework_logs, 'error': error_logs}

socketio = None

def set_socketio(sio):
    global socketio
    socketio = sio

def catch_error(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except:
            pass
    return wrapper

class LogHandler:
    __slots__ = ('log_type', 'logs', 'global_logs')
    
    def __init__(self, log_type):
        self.log_type = log_type
        self.logs = deque(maxlen=_MAX_LOGS)
        self.global_logs = _GLOBAL_LOGS.get(log_type, message_logs)
    
    def add(self, content, traceback_info=None):
        entry = content.copy() if isinstance(content, dict) else {'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'content': content}
        if 'timestamp' not in entry:
            entry['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if traceback_info:
            entry['traceback'] = traceback_info
        
        self.logs.append(entry)
        self.global_logs.append(entry)
        
        if socketio:
            try:
                actual_type = self.log_type
                if self.log_type == 'message':
                    entry_type = entry.get('type')
                    if entry_type in ('plugin', 'received'):
                        actual_type = entry_type
                
                fields = ['timestamp', 'content']
                if 'traceback' in entry:
                    fields.append('traceback')
                if actual_type == 'plugin':
                    fields.extend(('user_id', 'group_id', 'plugin_name'))
                elif actual_type == 'received':
                    fields.extend(('user_id', 'group_id'))
                
                socketio.emit('new_message', {'type': actual_type, 'data': {k: entry[k] for k in fields if k in entry}}, namespace=_PREFIX)
            except:
                pass
        
        return entry

message_handler = LogHandler('message')
framework_handler = LogHandler('framework')
error_handler = LogHandler('error')
received_handler = message_handler
plugin_handler = message_handler

_HANDLERS = {'message': message_handler, 'framework': framework_handler, 'error': error_handler}

@catch_error
def add_display_message(formatted_message, timestamp=None, user_id=None, group_id=None, message_content=None):
    ts = timestamp or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if user_id is not None and message_content is not None:
        entry = {'timestamp': ts, 'type': 'received', 'content': formatted_message,
                 'user_id': user_id, 'group_id': group_id or '-', 'message': message_content, 'raw_message': message_content}
    else:
        entry = {'timestamp': ts, 'type': 'received', 'content': formatted_message}
    return message_handler.add(entry)

@catch_error
def add_plugin_log(log, user_id=None, group_id=None, plugin_name=None, raw_message=None):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(log, str):
        log_data = {'timestamp': ts, 'type': 'plugin', 'content': log,
                    'user_id': user_id or '', 'group_id': group_id or 'c2c', 'plugin_name': plugin_name or ''}
    else:
        log_data = log.copy() if isinstance(log, dict) else {'content': str(log)}
        log_data.update({'type': 'plugin', 'user_id': user_id or '', 'group_id': group_id or 'c2c', 'plugin_name': plugin_name or ''})
    if raw_message:
        log_data['raw_message'] = raw_message
    return message_handler.add(log_data)

@catch_error
def add_framework_log(log):
    return framework_handler.add(log)

@catch_error
def add_error_log(log, traceback_info=None):
    return error_handler.add(log, traceback_info)

def get_logs_data(log_type):
    handler = _HANDLERS.get(log_type)
    return list(handler.logs) if handler else []
