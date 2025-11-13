from datetime import datetime
from flask import request, jsonify

message_logs = None
framework_logs = None
error_logs = None
LOG_DB_CONFIG = None
add_error_log = None

def set_log_queues(message, framework, error):
    global message_logs, framework_logs, error_logs
    message_logs = message
    framework_logs = framework
    error_logs = error

def set_config(log_db_config, error_log_func):
    global LOG_DB_CONFIG, add_error_log
    LOG_DB_CONFIG = log_db_config
    add_error_log = error_log_func

def handle_get_logs(log_type):
    page = request.args.get('page', 1, type=int)
    page_size = request.args.get('size', 50, type=int)
    
    logs_map = {
        'message': message_logs,
        'framework': framework_logs,
        'error': error_logs
    }
    
    if log_type not in logs_map:
        return jsonify({'error': '无效的日志类型'}), 400
    
    logs = list(logs_map[log_type])
    logs.reverse()
    
    start = (page - 1) * page_size
    page_logs = logs[start:start + page_size]
    
    return jsonify({
        'logs': page_logs,
        'total': len(logs),
        'page': page,
        'page_size': page_size,
        'total_pages': (len(logs) + page_size - 1) // page_size
    })

def get_today_logs_from_db(log_type, limit=None):
    try:
        if not LOG_DB_CONFIG:
            return []
        
        if limit is None:
            limit = LOG_DB_CONFIG.get('initial_load_count', 100)
        
        try:
            from function.log_db import LogDatabasePool
            from pymysql.cursors import DictCursor
        except ImportError:
            return []
        
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            return []
        
        try:
            cursor = connection.cursor(DictCursor)
            
            today = datetime.now().strftime('%Y%m%d')
            table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            
            if log_type == 'plugin':
                # 插件日志现在存储在 message 表中，type 字段为 'plugin'
                table_name = f'{table_prefix}{today}_message'
                
                cursor.execute("""
                    SELECT COUNT(*) as count 
                    FROM information_schema.tables 
                    WHERE table_schema = DATABASE() 
                    AND table_name = %s
                """, (table_name,))
                
                if cursor.fetchone()['count'] == 0:
                    return []
                
                sql = f"""
                    SELECT timestamp, content, user_id, group_id, plugin_name
                    FROM {table_name}
                    WHERE type = 'plugin'
                    ORDER BY timestamp DESC
                    LIMIT %s
                """
            else:
                # framework 和 error 日志仍在各自的表中
                table_suffix_map = {
                    'framework': 'framework', 
                    'error': 'error'
                }
                
                table_suffix = table_suffix_map.get(log_type, log_type)
                table_name = f'{table_prefix}{today}_{table_suffix}'
                
                cursor.execute("""
                    SELECT COUNT(*) as count 
                    FROM information_schema.tables 
                    WHERE table_schema = DATABASE() 
                    AND table_name = %s
                """, (table_name,))
                
                if cursor.fetchone()['count'] == 0:
                    return []
                
                if log_type == 'error':
                    sql = f"""
                        SELECT timestamp, content, traceback
                        FROM {table_name}
                        ORDER BY timestamp DESC
                        LIMIT %s
                    """
                else:
                    sql = f"""
                        SELECT timestamp, content
                        FROM {table_name}
                        ORDER BY timestamp DESC
                        LIMIT %s
                    """
            
            cursor.execute(sql, (limit,))
            logs = cursor.fetchall()
            
            result = []
            for log in logs:
                base_content = log['content'] or ''
                
                if log_type == 'plugin':
                    plugin_name = log.get('plugin_name', '未知插件')
                    user_id = log.get('user_id', '')
                    group_id = log.get('group_id', 'c2c')
                    
                    location_info = f"{user_id}@{group_id}" if user_id else group_id
                    formatted_content = f"[{plugin_name}] {base_content}"
                    if user_id or group_id != 'c2c':
                        formatted_content += f" ({location_info})"
                    
                    log_entry = {
                        'timestamp': log['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if log['timestamp'] else '',
                        'content': formatted_content,
                        'user_id': user_id,
                        'group_id': group_id,
                        'plugin_name': plugin_name
                    }
                elif log_type == 'error':
                    log_entry = {
                        'timestamp': log['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if log['timestamp'] else '',
                        'content': base_content
                    }
                    if log.get('traceback'):
                        log_entry['traceback'] = log['traceback']
                else:
                    log_entry = {
                        'timestamp': log['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if log['timestamp'] else '',
                        'content': base_content
                    }
                
                result.append(log_entry)
            
            return result
            
        finally:
            cursor.close()
            log_db_pool.release_connection(connection)
            
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取今日日志失败 {log_type}: {str(e)}")
        return []

def get_today_message_logs_from_db(limit=None):
    try:
        if not LOG_DB_CONFIG:
            return []
        
        if limit is None:
            limit = LOG_DB_CONFIG.get('initial_load_count', 100)
        
        try:
            from function.log_db import LogDatabasePool
            from pymysql.cursors import DictCursor
        except ImportError:
            return []
        
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            return []
        
        try:
            cursor = connection.cursor(DictCursor)
            
            today = datetime.now().strftime('%Y%m%d')
            table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            table_name = f'{table_prefix}{today}_message'
            
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """, (table_name,))
            
            if cursor.fetchone()['count'] == 0:
                return []
            
            sql = f"""
                SELECT timestamp, user_id, group_id, content
                FROM {table_name}
                WHERE type = 'received' AND user_id != 'ZFC2G' AND user_id != 'ZFC2C'
                ORDER BY timestamp DESC
                LIMIT %s
            """
            
            cursor.execute(sql, (limit,))
            logs = cursor.fetchall()
            
            result = []
            for log in logs:
                log_entry = {
                    'timestamp': log['timestamp'].strftime('%Y-%m-%d %H:%M:%S') if log['timestamp'] else '',
                    'content': f"收到消息: {log['content']}" if log['content'] else '',
                    'user_id': log['user_id'] or '',
                    'group_id': log['group_id'] or 'c2c',
                    'message': log['content'] or ''
                }
                result.append(log_entry)
            
            return result
            
        finally:
            cursor.close()
            log_db_pool.release_connection(connection)
            
    except Exception as e:
        if add_error_log:
            add_error_log(f"获取今日消息日志失败: {str(e)}")
        return []

def handle_get_today_logs():
    try:
        limit = request.args.get('limit', type=int)
        if limit is None:
            limit = LOG_DB_CONFIG.get('initial_load_count', 100) if LOG_DB_CONFIG else 100
        
        result = {}
        
        received_logs = get_today_message_logs_from_db(limit)
        result['received'] = {
            'logs': received_logs,
            'total': len(received_logs),
            'type': 'received'
        }
        
        for log_type in ['plugin', 'framework', 'error']:
            logs = get_today_logs_from_db(log_type, limit)
            result[log_type] = {
                'logs': logs,
                'total': len(logs),
                'type': log_type
            }
        
        return jsonify({
            'success': True,
            'data': result,
            'limit': limit,
            'date': datetime.now().strftime('%Y-%m-%d'),
            'message': f'成功获取今日日志，每种类型最多{limit}条'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'获取今日日志失败: {str(e)}'
        }), 500

