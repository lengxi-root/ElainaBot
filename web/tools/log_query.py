from datetime import datetime
from flask import request, jsonify

message_logs = None
framework_logs = None
error_logs = None
LOG_DB_CONFIG = None
add_error_log = None

_LOGS_MAP = {}
_DEFAULT_LIMIT = 100
_TABLE_CHECK_SQL = "SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s"
_LOG_TYPES = frozenset(('plugin', 'framework', 'error'))

def set_log_queues(message, framework, error):
    global message_logs, framework_logs, error_logs, _LOGS_MAP
    message_logs, framework_logs, error_logs = message, framework, error
    _LOGS_MAP = {'message': message_logs, 'framework': framework_logs, 'error': error_logs}

def set_config(log_db_config, error_log_func):
    global LOG_DB_CONFIG, add_error_log
    LOG_DB_CONFIG, add_error_log = log_db_config, error_log_func

def handle_get_logs(log_type):
    if log_type not in _LOGS_MAP:
        return jsonify({'error': '无效的日志类型'}), 400
    
    page, page_size = request.args.get('page', 1, type=int), request.args.get('size', 50, type=int)
    logs = list(_LOGS_MAP[log_type])
    logs.reverse()
    start = (page - 1) * page_size
    
    return jsonify({
        'logs': logs[start:start + page_size], 'total': len(logs),
        'page': page, 'page_size': page_size, 'total_pages': (len(logs) + page_size - 1) // page_size
    })

def _get_db_connection():
    if not LOG_DB_CONFIG:
        return None, None
    try:
        from function.log_db import LogDatabasePool
        from pymysql.cursors import DictCursor
        pool = LogDatabasePool()
        return pool, pool.get_connection()
    except ImportError:
        return None, None

def _format_timestamp(ts):
    return ts.strftime('%Y-%m-%d %H:%M:%S') if ts else ''

def get_today_logs_from_db(log_type, limit=None):
    try:
        pool, conn = _get_db_connection()
        if not conn:
            return []
        
        limit = limit or LOG_DB_CONFIG.get('initial_load_count', _DEFAULT_LIMIT)
        today = datetime.now().strftime('%Y%m%d')
        prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
        
        try:
            from pymysql.cursors import DictCursor
            cursor = conn.cursor(DictCursor)
            
            if log_type == 'plugin':
                table_name = f'{prefix}{today}_message'
                cursor.execute(_TABLE_CHECK_SQL, (table_name,))
                if cursor.fetchone()['count'] == 0:
                    return []
                cursor.execute(f"SELECT timestamp, content, user_id, group_id, plugin_name FROM {table_name} WHERE type = 'plugin' ORDER BY timestamp DESC LIMIT %s", (limit,))
            else:
                table_name = f'{prefix}{today}_{log_type}'
                cursor.execute(_TABLE_CHECK_SQL, (table_name,))
                if cursor.fetchone()['count'] == 0:
                    return []
                cols = 'timestamp, content, traceback, resp_obj, send_payload, raw_message' if log_type == 'error' else 'timestamp, content'
                cursor.execute(f"SELECT {cols} FROM {table_name} ORDER BY timestamp DESC LIMIT %s", (limit,))
            
            result = []
            for log in cursor.fetchall():
                entry = {'timestamp': _format_timestamp(log['timestamp']), 'content': log['content'] or ''}
                if log_type == 'plugin':
                    entry.update({'user_id': log.get('user_id', ''), 'group_id': log.get('group_id', 'c2c'), 'plugin_name': log.get('plugin_name', '未知插件')})
                elif log_type == 'error':
                    if log.get('traceback'):
                        entry['traceback'] = log['traceback']
                    if log.get('resp_obj'):
                        entry['resp_obj'] = log['resp_obj']
                    if log.get('send_payload'):
                        entry['send_payload'] = log['send_payload']
                    if log.get('raw_message'):
                        entry['raw_message'] = log['raw_message']
                result.append(entry)
            return result
        finally:
            cursor.close()
            pool.release_connection(conn)
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取今日日志失败 {log_type}: {e}")
        return []

def get_today_message_logs_from_db(limit=None):
    try:
        pool, conn = _get_db_connection()
        if not conn:
            return []
        
        limit = limit or LOG_DB_CONFIG.get('initial_load_count', _DEFAULT_LIMIT)
        today = datetime.now().strftime('%Y%m%d')
        prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
        table_name = f'{prefix}{today}_message'
        
        try:
            from pymysql.cursors import DictCursor
            cursor = conn.cursor(DictCursor)
            cursor.execute(_TABLE_CHECK_SQL, (table_name,))
            if cursor.fetchone()['count'] == 0:
                return []
            
            cursor.execute(f"SELECT timestamp, user_id, group_id, content FROM {table_name} WHERE type = 'received' AND user_id != 'ZFC2G' AND user_id != 'ZFC2C' ORDER BY timestamp DESC LIMIT %s", (limit,))
            return [{'timestamp': _format_timestamp(log['timestamp']), 'content': f"收到消息: {log['content']}" if log['content'] else '',
                     'user_id': log['user_id'] or '', 'group_id': log['group_id'] or 'c2c', 'message': log['content'] or ''} for log in cursor.fetchall()]
        finally:
            cursor.close()
            pool.release_connection(conn)
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取今日消息日志失败: {e}")
        return []

def handle_get_today_logs():
    try:
        limit = request.args.get('limit', type=int) or (LOG_DB_CONFIG.get('initial_load_count', _DEFAULT_LIMIT) if LOG_DB_CONFIG else _DEFAULT_LIMIT)
        
        result = {'received': {'logs': (logs := get_today_message_logs_from_db(limit)), 'total': len(logs), 'type': 'received'}}
        for log_type in _LOG_TYPES:
            logs = get_today_logs_from_db(log_type, limit)
            result[log_type] = {'logs': logs, 'total': len(logs), 'type': log_type}
        
        return jsonify({'success': True, 'data': result, 'limit': limit, 'date': datetime.now().strftime('%Y-%m-%d'),
                        'message': f'成功获取今日日志，每种类型最多{limit}条'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'获取今日日志失败: {e}'}), 500
