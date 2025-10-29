import os, sys, time
from datetime import datetime
from flask import request, jsonify
from concurrent.futures import ThreadPoolExecutor, as_completed

_nickname_cache = {}
_cache_timeout = 86400

def get_chat_avatar(chat_id, chat_type, appid):
    if chat_type == 'user':
        return f"https://q.qlogo.cn/qqapp/{appid}/{chat_id}/100"
    else:
        return chat_id[0].upper() if chat_id else 'G'

def get_user_nickname(user_id):
    try:
        if not user_id:
            return "用户Unknown"
        
        try:
            from function.database import Database
        except ImportError:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            from function.database import Database
        
        db = Database()
        nickname = db.get_user_name(user_id)
        
        if nickname:
            return nickname
        return f"用户{user_id[-6:]}"
    except Exception:
        return f"用户{user_id[-6:]}"

def get_user_nicknames_batch(user_ids):
    current_time = time.time()
    result = {}
    users_to_fetch = []
    
    for user_id in user_ids:
        if user_id in _nickname_cache:
            cached_data = _nickname_cache[user_id]
            if current_time - cached_data['timestamp'] < _cache_timeout:
                result[user_id] = cached_data['nickname']
                continue
        users_to_fetch.append(user_id)
    
    if not users_to_fetch:
        return result
    
    try:
        from function.database import Database
    except ImportError:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from function.database import Database
    
    db = Database()
    
    def fetch_single_nickname(user_id):
        try:
            nickname = db.get_user_name(user_id)
            if not nickname:
                nickname = f"用户{user_id[-6:]}"
        except:
            nickname = f"用户{user_id[-6:]}"
        _nickname_cache[user_id] = {'nickname': nickname, 'timestamp': current_time}
        return user_id, nickname
    
    with ThreadPoolExecutor(max_workers=min(5, len(users_to_fetch))) as executor:
        future_to_user = {executor.submit(fetch_single_nickname, uid): uid for uid in users_to_fetch}
        for future in as_completed(future_to_user, timeout=10):
            try:
                user_id, nickname = future.result()
                result[user_id] = nickname
            except:
                result[future_to_user[future]] = f"用户{future_to_user[future][-6:]}"
    return result

def handle_get_chats(LOG_DB_CONFIG, appid):
    try:
        data = request.get_json()
        chat_type = data.get('type', 'user')
        search = data.get('search', '').strip()
        limit = 30
        
        from function.log_db import LogDatabasePool
        from pymysql.cursors import DictCursor
        
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            return jsonify({'success': False, 'message': '数据库连接失败'})
        
        try:
            cursor = connection.cursor(DictCursor)
            
            # 获取表前缀
            table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            id_table_name = f'{table_prefix}id'
            
            # 检查ID表是否存在
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """, (id_table_name,))
            if cursor.fetchone()['count'] == 0:
                return jsonify({'success': False, 'message': 'ID表不存在'})
            
            # 构建查询
            if search:
                # 搜索功能 - 使用参数化查询避免SQL注入
                search_condition = "AND chat_id LIKE %s"
                search_param = f"%{search}%"
            else:
                search_condition = ""
                search_param = None
            
            # 获取聊天数据（最多30个）
            data_sql = f"""
                SELECT chat_id, last_message_id, MAX(timestamp) as last_time
                FROM {id_table_name} 
                WHERE chat_type = %s {search_condition}
                GROUP BY chat_id, last_message_id
                ORDER BY last_time DESC
                LIMIT %s
            """
            if search_param:
                cursor.execute(data_sql, (chat_type, search_param, limit))
            else:
                cursor.execute(data_sql, (chat_type, limit))
            chats = cursor.fetchall()
            
            # 处理数据
            chat_list = []
            for chat in chats:
                chat_info = {
                    'chat_id': chat['chat_id'],
                    'last_message_id': chat['last_message_id'],
                    'last_time': chat['last_time'].strftime('%Y-%m-%d %H:%M:%S') if chat['last_time'] else '',
                    'avatar': get_chat_avatar(chat['chat_id'], chat_type, appid),
                    'nickname': 'Loading...'  # 异步加载
                }
                chat_list.append(chat_info)
            
            return jsonify({
                'success': True,
                'data': {
                    'chats': chat_list
                }
            })
            
        finally:
            cursor.close()
            log_db_pool.release_connection(connection)
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取聊天列表失败: {str(e)}'})

def handle_get_chat_history(LOG_DB_CONFIG, appid):
    try:
        data = request.get_json()
        chat_type = data.get('chat_type')
        chat_id = data.get('chat_id')
        
        if not chat_type or not chat_id:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        
        from function.log_db import LogDatabasePool
        from pymysql.cursors import DictCursor
        
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            return jsonify({'success': False, 'message': '数据库连接失败'})
        
        try:
            cursor = connection.cursor(DictCursor)
            
            # 获取表前缀和今日消息表名
            table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            today = datetime.now().strftime('%Y%m%d')
            table_name = f'{table_prefix}{today}_message'
            
            # 检查表是否存在
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """, (table_name,))
            
            if cursor.fetchone()['count'] == 0:
                return jsonify({'success': True, 'data': {'messages': [],
                    'chat_info': {'chat_id': chat_id, 'chat_type': chat_type, 'avatar': get_chat_avatar(chat_id, chat_type, appid)}}})
            
            where_condition, params = (("(group_id = %s AND group_id != 'c2c') OR (user_id = 'ZFC2G' AND group_id = %s)", (chat_id, chat_id))
                if chat_type == 'group' else ("(user_id = %s AND group_id = 'c2c') OR (user_id = %s AND group_id = 'ZFC2C')", (chat_id, chat_id)))
            
            # 获取消息记录
            sql = f"""
                SELECT user_id, group_id, content, timestamp
                FROM {table_name}
                WHERE {where_condition}
                ORDER BY timestamp ASC
                LIMIT 100
            """
            cursor.execute(sql, params)
            messages = cursor.fetchall()
            
            message_list = []
            for msg in messages:
                is_self_message = (chat_type == 'group' and msg['user_id'] == 'ZFC2G') or (chat_type == 'user' and msg['group_id'] == 'ZFC2C')
                display_user_id = '机器人' if is_self_message else msg['user_id']
                message_list.append({'user_id': display_user_id, 'content': msg['content'],
                    'timestamp': msg['timestamp'].strftime('%H:%M:%S') if msg['timestamp'] else '',
                    'avatar': get_chat_avatar('robot' if is_self_message else msg['user_id'], 'user', appid), 'is_self': is_self_message})
            
            return jsonify({'success': True, 'data': {'messages': message_list,
                'chat_info': {'chat_id': chat_id, 'chat_type': chat_type, 'avatar': get_chat_avatar(chat_id, chat_type, appid)}}})
            
        finally:
            cursor.close()
            log_db_pool.release_connection(connection)
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取聊天记录失败: {str(e)}'})

def handle_send_message(LOG_DB_CONFIG, add_sent_message_to_db):
    try:
        data = request.get_json()
        chat_type = data.get('chat_type')
        chat_id = data.get('chat_id')
        send_method = data.get('send_method', 'text')
        
        if not chat_type or not chat_id:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        
        # 检查ID是否过期
        from function.log_db import LogDatabasePool
        from pymysql.cursors import DictCursor
        
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            return jsonify({'success': False, 'message': '数据库连接失败'})
        
        try:
            cursor = connection.cursor(DictCursor)
            
            # 获取表前缀和ID表名
            table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            id_table_name = f'{table_prefix}id'
            
            # 获取最后的消息ID和时间
            cursor.execute(f"""
                SELECT last_message_id, timestamp 
                FROM {id_table_name} 
                WHERE chat_type = %s AND chat_id = %s
            """, (chat_type, chat_id))
            
            result = cursor.fetchone()
            if not result:
                return jsonify({'success': False, 'message': 'ID记录不存在'})
            
            last_message_id = result['last_message_id']
            last_time = result['timestamp']
            
            # 获取当前时间用于显示
            now = datetime.now()
            
            # 创建模拟消息事件来发送消息
            mock_raw_data = {
                'd': {
                    'id': last_message_id,
                    'author': {'id': '2218872014'},
                    'content': '',
                    'timestamp': last_time.isoformat()
                },
                'id': last_message_id,
                't': 'C2C_MESSAGE_CREATE' if chat_type == 'user' else 'GROUP_AT_MESSAGE_CREATE'
            }
            
            if chat_type == 'group':
                mock_raw_data['d']['group_id'] = chat_id
            else:
                mock_raw_data['d']['author']['id'] = chat_id
                
            from core.event.MessageEvent import MessageEvent
            event = MessageEvent(mock_raw_data, skip_recording=True)
            
            # 根据发送方法调用相应的发送函数
            message_id = None
            display_content = ''
            
            if send_method == 'text':
                if not (content := data.get('content', '').strip()):
                    return jsonify({'success': False, 'message': '请输入消息内容'})
                # 发送普通消息：强制使用纯文本模式 (use_markdown=False)
                message_id = event.reply(content, use_markdown=False)
                display_content = content
            elif send_method == 'markdown':
                if not (content := data.get('content', '').strip()):
                    return jsonify({'success': False, 'message': '请输入Markdown内容'})
                # 发送 Markdown 消息：强制使用 markdown 模式 (use_markdown=True)
                message_id = event.reply(content, use_markdown=True)
                display_content = content
                
            elif send_method == 'template_markdown':
                if not (template := data.get('template')):
                    return jsonify({'success': False, 'message': '请选择模板'})
                if not (params := data.get('params', [])):
                    return jsonify({'success': False, 'message': '请输入模板参数'})
                message_id, display_content = event.reply_markdown(template, tuple(params), data.get('keyboard_id')), f'[模板消息: {template}]'
            elif send_method == 'image':
                if not (image_url := data.get('image_url', '').strip()):
                    return jsonify({'success': False, 'message': '请输入图片URL'})
                message_id, display_content = event.reply_image(image_url, data.get('image_text', '').strip()), f'[图片消息: {data.get("image_text", "") or "图片"}]'
            elif send_method == 'voice':
                if not (voice_url := data.get('voice_url', '').strip()):
                    return jsonify({'success': False, 'message': '请输入语音文件URL'})
                message_id, display_content = event.reply_voice(voice_url), '[语音消息]'
            elif send_method == 'video':
                if not (video_url := data.get('video_url', '').strip()):
                    return jsonify({'success': False, 'message': '请输入视频文件URL'})
                message_id, display_content = event.reply_video(video_url), '[视频消息]'
            elif send_method == 'ark':
                if not (ark_type := data.get('ark_type')):
                    return jsonify({'success': False, 'message': '请选择ARK卡片类型'})
                if not (ark_params := data.get('ark_params', [])):
                    return jsonify({'success': False, 'message': '请输入卡片参数'})
                
                # 处理ark参数：保持列表结构（用于ark23的嵌套列表）
                processed_params = []
                for param in ark_params:
                    if isinstance(param, list):
                        # 保持列表格式（用于ark23的列表项）
                        processed_params.append(param)
                    else:
                        # 普通参数
                        processed_params.append(param)
                
                message_id, display_content = event.reply_ark(ark_type, tuple(processed_params)), f'[ARK卡片: 类型{ark_type}]'
            else:
                return jsonify({'success': False, 'message': '不支持的发送方法'})
            
            # 根据MessageEvent的返回值判断发送是否成功
            if message_id is not None:
                # 检查message_id是否包含官方API错误信息
                message_id_str = str(message_id)
                
                # 检查是否为JSON格式的错误信息（MessageEvent返回的错误）
                if message_id_str.startswith('{') and message_id_str.endswith('}'):
                    try:
                        import json
                        error_obj = json.loads(message_id_str)
                        
                        # 检查是否为错误信息
                        if error_obj.get('error') is True:
                            api_error = ''
                            if error_obj.get('message'):
                                api_error = error_obj['message']
                            if error_obj.get('code'):
                                api_error += f", code:{error_obj['code']}" if api_error else f"code:{error_obj['code']}"
                            
                            return jsonify({'success': False, 'message': api_error or "发送失败: 未知错误"})
                    except Exception:
                        pass
                
                # 如果不是API错误格式，则表示发送成功
                
                # 记录发送的消息到数据库
                try:
                    add_sent_message_to_db(
                        chat_type=chat_type,
                        chat_id=chat_id,
                        content=display_content,
                        timestamp=now.strftime('%Y-%m-%d %H:%M:%S')
                    )
                except Exception as e:
                    # 记录失败不影响发送成功的响应
                    print(f"记录发送消息失败: {str(e)}")
                
                return jsonify({
                    'success': True,
                    'message': '消息发送成功',
                    'data': {
                        'message_id': message_id,
                        'content': display_content,
                        'timestamp': now.strftime('%H:%M:%S'),
                        'send_method': send_method
                    }
                })
            else:
                # MessageEvent返回None，表示发送失败（如被忽略的错误代码）
                return jsonify({'success': False, 'message': '消息发送失败，可能是权限不足或其他限制'})
            
        finally:
            cursor.close()
            log_db_pool.release_connection(connection)
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'发送消息失败: {str(e)}'})

def handle_get_nickname():
    try:
        if not (user_id := request.get_json().get('user_id')):
            return jsonify({'success': False, 'message': '缺少用户ID'})
        
        # 从数据库获取昵称
        try:
            from function.database import Database
            db = Database()
            nickname = db.get_user_name(user_id)
            
            if not nickname:
                nickname = f"用户{user_id[-6:]}"
        except:
            nickname = f"用户{user_id[-6:]}"
            
        return jsonify({'success': True, 'data': {'user_id': user_id, 'nickname': nickname}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取昵称失败: {str(e)}'})

def handle_get_nicknames_batch():
    try:
        data = request.get_json()
        user_ids = data.get('user_ids', [])
        
        if not user_ids or not isinstance(user_ids, list):
            return jsonify({'success': False, 'message': '缺少用户ID列表'})
        
        # 从数据库批量获取昵称
        try:
            from function.database import Database, get_table_name
            from function.db_pool import ConnectionManager
            
            db = Database()
            nicknames = {}
            
            # 使用 IN 查询批量获取
            if user_ids:
                with ConnectionManager() as manager:
                    if manager.connection:
                        cursor = manager.connection.cursor()
                        try:
                            placeholders = ','.join(['%s'] * len(user_ids))
                            sql = f"SELECT user_id, name FROM {get_table_name('users')} WHERE user_id IN ({placeholders})"
                            cursor.execute(sql, tuple(user_ids))
                            results = cursor.fetchall()
                            
                            # 处理结果
                            for row in results:
                                if isinstance(row, dict):
                                    user_id = row.get('user_id')
                                    name = row.get('name')
                                else:
                                    user_id = row[0]
                                    name = row[1]
                                
                                if user_id and name:
                                    nicknames[user_id] = name
                        finally:
                            cursor.close()
            
            # 对于没有找到昵称的用户，使用默认昵称
            for user_id in user_ids:
                if user_id not in nicknames:
                    nicknames[user_id] = f"用户{user_id[-6:]}"
            
            return jsonify({'success': True, 'data': {'nicknames': nicknames}})
            
        except Exception as e:
            # 如果数据库查询失败，返回默认昵称
            nicknames = {user_id: f"用户{user_id[-6:]}" for user_id in user_ids}
            return jsonify({'success': True, 'data': {'nicknames': nicknames}})
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'批量获取昵称失败: {str(e)}'})

def handle_get_markdown_templates():
    try:
        from core.event.markdown_templates import get_all_templates
        templates = get_all_templates()
        
        template_list = []
        for template_id, template_info in templates.items():
            template_list.append({
                'id': template_id,
                'name': f'模板{template_id}',
                'params': template_info.get('params', []),
                'param_count': len(template_info.get('params', []))
            })
        
        return jsonify({'success': True, 'data': {'templates': template_list}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取模板列表失败: {str(e)}'})

