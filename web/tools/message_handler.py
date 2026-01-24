import os, sys, time
from datetime import datetime, timedelta
from flask import request, jsonify

_nickname_cache = {}
_CACHE_TIMEOUT = 86400
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def _ensure_path():
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

def get_chat_avatar(chat_id, chat_type, appid):
    return f"https://q.qlogo.cn/qqapp/{appid}/{chat_id}/100" if chat_type == 'user' else (chat_id[0].upper() if chat_id else 'G')

def get_user_nickname(user_id):
    if not user_id:
        return "用户Unknown"
    try:
        _ensure_path()
        from function.database import Database
        return Database().get_user_name(user_id) or f"用户{user_id[-6:]}"
    except:
        return f"用户{user_id[-6:]}"

def get_user_nicknames_batch(user_ids):
    current_time, result, users_to_fetch = time.time(), {}, []
    
    for uid in user_ids:
        if uid in _nickname_cache and current_time - _nickname_cache[uid]['timestamp'] < _CACHE_TIMEOUT:
            result[uid] = _nickname_cache[uid]['nickname']
        else:
            users_to_fetch.append(uid)
    
    if not users_to_fetch:
        return result
    
    try:
        _ensure_path()
        from function.database import Database
        from function.log_db import LogDatabasePool
        
        db, pool = Database(), LogDatabasePool()
        if conn := pool.get_connection():
            try:
                cursor = conn.cursor()
                cursor.execute(f"SELECT user_id, name FROM {db.get_table_name('users')} WHERE user_id IN ({','.join(['%s'] * len(users_to_fetch))})", tuple(users_to_fetch))
                for row in cursor.fetchall():
                    uid, name = (row.get('user_id'), row.get('name')) if isinstance(row, dict) else (row[0], row[1])
                    if uid and name:
                        result[uid] = name
                        _nickname_cache[uid] = {'nickname': name, 'timestamp': current_time}
            finally:
                cursor.close()
                pool.release_connection(conn)
    except:
        pass
    
    for uid in users_to_fetch:
        if uid not in result:
            nickname = f"用户{uid[-6:]}"
            result[uid] = nickname
            _nickname_cache[uid] = {'nickname': nickname, 'timestamp': current_time}
    return result

def handle_get_chats(LOG_DB_CONFIG, appid):
    try:
        data = request.get_json()
        chat_type, search, days, limit = data.get('type', 'user'), data.get('search', '').strip(), data.get('days', 3), 200
        
        _ensure_path()
        from function.log_db import LogDatabasePool
        from pymysql.cursors import DictCursor
        
        pool = LogDatabasePool()
        if not (conn := pool.get_connection()):
            return jsonify({'success': False, 'message': '数据库连接失败'})
        
        try:
            cursor = conn.cursor(DictCursor)
            prefix, id_table = LOG_DB_CONFIG.get('table_prefix', 'Mlog_'), f"{LOG_DB_CONFIG.get('table_prefix', 'Mlog_')}id"
            
            cursor.execute("SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (id_table,))
            if cursor.fetchone()['count'] == 0:
                return jsonify({'success': False, 'message': 'ID表不存在'})
            
            days_ago = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) if days == 1 else datetime.now() - timedelta(days=days)
            conditions, params = ["chat_type = %s", "timestamp >= %s"], [chat_type, days_ago]
            if search:
                conditions.append("chat_id LIKE %s")
                params.append(f"%{search}%")
            
            cursor.execute(f"SELECT chat_id, last_message_id, MAX(timestamp) as last_time FROM {id_table} WHERE {' AND '.join(conditions)} GROUP BY chat_id, last_message_id ORDER BY last_time DESC LIMIT %s", (*params, limit))
            chats = cursor.fetchall()
            
            if not chats:
                return jsonify({'success': True, 'data': {'chats': []}})
            
            nicknames = get_user_nicknames_batch([c['chat_id'] for c in chats]) if chat_type == 'user' else {}
            chat_list = [{
                'chat_id': c['chat_id'], 'last_message_id': c['last_message_id'],
                'last_time': c['last_time'].strftime('%Y-%m-%d %H:%M:%S') if c['last_time'] else '',
                'avatar': get_chat_avatar(c['chat_id'], chat_type, appid),
                'nickname': nicknames.get(c['chat_id'], f"用户{c['chat_id'][-6:]}") if chat_type == 'user' else f"群{c['chat_id'][-6:]}"
            } for c in chats]
            
            return jsonify({'success': True, 'data': {'chats': chat_list}})
        finally:
            cursor.close()
            pool.release_connection(conn)
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取聊天列表失败: {e}'})

def handle_get_chat_history(LOG_DB_CONFIG, appid):
    try:
        data = request.get_json()
        chat_type, chat_id = data.get('chat_type'), data.get('chat_id')
        show_all, since_id = data.get('show_all', False), data.get('since_id')
        days_range = 365 if show_all else min(data.get('days_range', 1), 30)
        
        if not chat_type or not chat_id:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        
        _ensure_path()
        from function.log_db import LogDatabasePool
        from pymysql.cursors import DictCursor
        
        pool = LogDatabasePool()
        if not (conn := pool.get_connection()):
            return jsonify({'success': False, 'message': '数据库连接失败'})
        
        try:
            cursor = conn.cursor(DictCursor)
            prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            
            existing_tables = []
            for i in range(days_range):
                table_name = f"{prefix}{(datetime.now() - timedelta(days=i)).strftime('%Y%m%d')}_message"
                cursor.execute("SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (table_name,))
                if cursor.fetchone()['count'] > 0:
                    existing_tables.append(table_name)
            
            if not existing_tables:
                return jsonify({'success': True, 'data': {'messages': [], 'chat_info': {'chat_id': chat_id, 'chat_type': chat_type, 'avatar': get_chat_avatar(chat_id, chat_type, appid)}, 'no_history': True}})
            
            if chat_type == 'group':
                base_cond, params = "(group_id = %s AND group_id != 'c2c') OR (user_id = 'ZFC2G' AND group_id = %s)", [chat_id, chat_id]
            else:
                base_cond, params = "(user_id = %s AND group_id = 'c2c') OR (user_id = %s AND group_id = 'ZFC2C')", [chat_id, chat_id]
            
            where_cond = f"({base_cond}) AND id > %s" if since_id else base_cond
            if since_id:
                params.append(since_id)
            
            union_parts, all_params = [], []
            for t in existing_tables:
                union_parts.append(f"SELECT user_id, group_id, content, timestamp, type, id FROM {t} WHERE {where_cond}")
                all_params.extend(params)
            
            msg_limit = 50 if since_id else (500 if show_all else 200)
            order = 'ASC' if since_id else 'DESC'
            cursor.execute(f"SELECT * FROM ({' UNION ALL '.join(union_parts)} ORDER BY id {order} LIMIT {msg_limit}) AS m ORDER BY id ASC", all_params)
            messages = cursor.fetchall()
            
            user_ids = {m['user_id'] for m in messages if m.get('type') != 'plugin' and not ((chat_type == 'group' and m['user_id'] == 'ZFC2G') or (chat_type == 'user' and m['group_id'] == 'ZFC2C'))}
            nicknames = get_user_nicknames_batch(list(user_ids)) if user_ids else {}
            
            ts_fmt = '%Y-%m-%d %H:%M:%S' if show_all else '%H:%M:%S'
            msg_list = []
            for m in messages:
                is_self = m.get('type') == 'plugin' or (chat_type == 'group' and m['user_id'] == 'ZFC2G') or (chat_type == 'user' and m['group_id'] == 'ZFC2C')
                msg_list.append({
                    'user_id': '机器人' if is_self else m['user_id'],
                    'nickname': '机器人' if is_self else nicknames.get(m['user_id'], f"用户{m['user_id'][-6:]}"),
                    'content': m['content'], 'timestamp': m['timestamp'].strftime(ts_fmt) if m['timestamp'] else '',
                    'avatar': get_chat_avatar('robot' if is_self else m['user_id'], 'user', appid),
                    'is_self': is_self, 'id': m.get('id')
                })
            
            return jsonify({'success': True, 'data': {'messages': msg_list, 'chat_info': {'chat_id': chat_id, 'chat_type': chat_type, 'avatar': get_chat_avatar(chat_id, chat_type, appid)},
                'is_incremental': bool(since_id), 'has_more': len(msg_list) == msg_limit if since_id else False}})
        finally:
            cursor.close()
            pool.release_connection(conn)
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取聊天记录失败: {e}'})


def handle_send_message(LOG_DB_CONFIG, add_sent_message_to_db):
    try:
        data = request.get_json()
        chat_type, chat_id, send_method = data.get('chat_type'), data.get('chat_id'), data.get('send_method', 'text')
        
        if not chat_type or not chat_id:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        
        _ensure_path()
        from function.log_db import LogDatabasePool
        from pymysql.cursors import DictCursor
        
        pool = LogDatabasePool()
        if not (conn := pool.get_connection()):
            return jsonify({'success': False, 'message': '数据库连接失败'})
        
        try:
            cursor = conn.cursor(DictCursor)
            prefix, id_table = LOG_DB_CONFIG.get('table_prefix', 'Mlog_'), f"{LOG_DB_CONFIG.get('table_prefix', 'Mlog_')}id"
            
            cursor.execute(f"SELECT last_message_id, id_type, timestamp FROM {id_table} WHERE chat_type = %s AND chat_id = %s", (chat_type, chat_id))
            if not (result := cursor.fetchone()):
                return jsonify({'success': False, 'message': 'ID记录不存在'})
            
            msg_id, ts = result['last_message_id'], result['timestamp'].isoformat()
            if result['id_type'] == 'event':
                d = {'group_openid': chat_id, 'group_member_openid': '2218872014', 'chat_type': 1} if chat_type == 'group' else {'user_openid': chat_id, 'chat_type': 2}
                mock_raw = {'d': {**d, 'content': '', 'timestamp': ts}, 'id': msg_id, 't': 'INTERACTION_CREATE'}
            else:
                d = {'id': msg_id, 'author': {'id': chat_id if chat_type == 'user' else '2218872014'}, 'content': '', 'timestamp': ts}
                if chat_type == 'group': d['group_id'] = chat_id
                mock_raw = {'d': d, 'id': msg_id, 't': 'C2C_MESSAGE_CREATE' if chat_type == 'user' else 'GROUP_AT_MESSAGE_CREATE'}
            
            from core.event.MessageEvent import MessageEvent
            event = MessageEvent(mock_raw, skip_recording=True)
            
            # 发送消息
            if send_method in ('text', 'markdown'):
                if not (content := data.get('content', '').strip()):
                    return jsonify({'success': False, 'message': '请输入消息内容'})
                message_id, display_content = event.reply(content, use_markdown=(send_method == 'markdown')), content
            elif send_method == 'template_markdown':
                if not (template := data.get('template')) or not (params := data.get('params', [])):
                    return jsonify({'success': False, 'message': '请选择模板并输入参数'})
                message_id, display_content = event.reply_markdown(template, tuple(params), data.get('keyboard_id')), f'[模板消息: {template}]'
            elif send_method in ('image', 'voice', 'video'):
                url_key, err_msg = {'image': ('image_url', '图片'), 'voice': ('voice_url', '语音文件'), 'video': ('video_url', '视频文件')}[send_method]
                if not (url := data.get(url_key, '').strip()):
                    return jsonify({'success': False, 'message': f'请输入{err_msg}URL'})
                func = {'image': lambda: event.reply_image(url, data.get('image_text', '').strip()), 'voice': lambda: event.reply_voice(url), 'video': lambda: event.reply_video(url)}[send_method]
                display = f'[图片消息: {data.get("image_text", "") or "图片"}]' if send_method == 'image' else f'[{err_msg}消息]'
                message_id, display_content = func(), display
            elif send_method == 'ark':
                if not (ark_type := data.get('ark_type')) or not (ark_params := data.get('ark_params', [])):
                    return jsonify({'success': False, 'message': '请选择ARK卡片类型并输入参数'})
                message_id, display_content = event.reply_ark(ark_type, tuple(ark_params)), f'[ARK卡片: 类型{ark_type}]'
            else:
                return jsonify({'success': False, 'message': '不支持的发送方法'})
            
            if message_id is None:
                return jsonify({'success': False, 'message': '消息发送失败，可能是权限不足或其他限制'})
            
            msg_str = str(message_id)
            if msg_str.startswith('{') and msg_str.endswith('}'):
                try:
                    import json
                    if (err := json.loads(msg_str)).get('error'):
                        return jsonify({'success': False, 'message': err.get('message', '') + (f", code:{err['code']}" if err.get('code') else '') or "发送失败"})
                except: pass
            
            try:
                from web.app import add_plugin_log
                from function.log_db import add_sent_message_to_db
                add_plugin_log(display_content, user_id=chat_id if chat_type == 'user' else '', group_id=chat_id if chat_type == 'group' else 'c2c', plugin_name='WebPanel')
                add_sent_message_to_db(chat_type, chat_id, display_content, raw_message=display_content, timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            except: pass
            
            return jsonify({'success': True, 'message': '消息发送成功', 'data': {'message_id': message_id, 'content': display_content, 'timestamp': datetime.now().strftime('%H:%M:%S'), 'send_method': send_method}})
        finally:
            cursor.close()
            pool.release_connection(conn)
    except Exception as e:
        return jsonify({'success': False, 'message': f'发送消息失败: {e}'})

def handle_get_nickname():
    try:
        if not (user_id := request.get_json().get('user_id')):
            return jsonify({'success': False, 'message': '缺少用户ID'})
        return jsonify({'success': True, 'data': {'user_id': user_id, 'nickname': get_user_nickname(user_id)}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取昵称失败: {e}'})

def handle_get_nicknames_batch():
    try:
        user_ids = request.get_json().get('user_ids', [])
        if not user_ids or not isinstance(user_ids, list):
            return jsonify({'success': False, 'message': '缺少用户ID列表'})
        return jsonify({'success': True, 'data': {'nicknames': get_user_nicknames_batch(user_ids)}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'批量获取昵称失败: {e}'})

def handle_get_markdown_templates():
    try:
        from core.event.markdown_templates import get_all_templates
        return jsonify({'success': True, 'data': {'templates': [{'id': tid, 'name': f'模板{tid}', 'params': info.get('params', []), 'param_count': len(info.get('params', []))} for tid, info in get_all_templates().items()]}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取模板列表失败: {e}'})

def handle_get_markdown_templates_detail():
    try:
        from core.event.markdown_templates import get_all_templates
        import re
        raw_contents = {}
        try:
            with open(os.path.join(_PROJECT_ROOT, 'core', 'event', 'markdown_templates.py'), 'r', encoding='utf-8') as f:
                for tid, raw in re.findall(r'"(\d+)":\s*\{[^}]+\},?\s*#\s*原始模板内容:\s*(.+?)(?=\n|$)', f.read()):
                    raw_contents[tid] = raw.strip()
        except: pass
        return jsonify({'success': True, 'data': {'templates': [{'id': tid, 'name': f'模板{tid}', 'template_id': info.get('id', ''), 'params': info.get('params', []), 'param_count': len(info.get('params', [])), 'raw_content': raw_contents.get(tid, '未提供')} for tid, info in get_all_templates().items()]}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取模板详情失败: {e}'})
