import os, sys, time
from datetime import datetime
from flask import request, jsonify

MessageEvent = None

def set_message_event_class(event_class):
    global MessageEvent
    MessageEvent = event_class

api_success_response = None
api_error_response = None

def set_response_functions(success_func, error_func):
    global api_success_response, api_error_response
    api_success_response = success_func
    api_error_response = error_func

def handle_sandbox_test():
    data = request.get_json()
    
    if not data:
        return api_error_response('缺少请求数据', 400)
    
    message_content = data.get('message', '').strip()
    group_id = data.get('group_id', '').strip()
    user_id = data.get('user_id', '').strip()
    
    if not message_content:
        return api_error_response('消息内容不能为空', 400)
    if not user_id:
        return api_error_response('用户ID不能为空', 400)
    
    is_private = not group_id
    message_type = "C2C_MESSAGE_CREATE" if is_private else "GROUP_AT_MESSAGE_CREATE"
    
    mock_data = {
        "s": 1,
        "op": 0,
        "t": message_type,
        "d": {
            "id": f"sandbox_test_{int(time.time())}",
            "content": message_content,
            "timestamp": datetime.now().isoformat(),
            "author": {
                "id": user_id,
                "username": f"测试用户{user_id}",
                "avatar": "",
                "bot": False
            },
            "attachments": [],
            "embeds": [],
            "mentions": [],
            "mention_roles": [],
            "pinned": False,
            "mention_everyone": False,
            "tts": False,
            "edited_timestamp": None,
            "flags": 0,
            "referenced_message": None,
            "interaction": None,
            "thread": None,
            "components": [],
            "sticker_items": [],
            "position": None
        }
    }
    
    if not is_private:
        mock_data["d"]["channel_id"] = group_id
        mock_data["d"]["guild_id"] = group_id
        mock_data["d"]["group_id"] = group_id
        mock_data["d"]["member"] = {
            "user": {
                "id": user_id,
                "username": f"测试用户{user_id}",
                "avatar": "",
                "bot": False
            },
            "nick": f"测试用户{user_id}",
            "roles": [],
            "joined_at": datetime.now().isoformat()
        }
    
    try:
        event = MessageEvent(mock_data, skip_recording=True)
        
        replies = []
        original_reply = event.reply
        
        def mock_reply(content, buttons=None, media=None, *args, **kwargs):
            reply_data = {
                'type': 'reply',
                'content': str(content) if content else '',
                'buttons': buttons,
                'media': media
            }
            replies.append(reply_data)
            return reply_data
        
        event.reply = mock_reply
        
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if project_root not in sys.path:
                sys.path.insert(0, project_root)
            
            from core.plugin.PluginManager import PluginManager
            
            PluginManager.dispatch_message(event)
            
            event.reply = original_reply
            
            return api_success_response({
                'replies': replies,
                'message_info': {
                    'content': message_content,
                    'group_id': group_id or '(私聊)',
                    'user_id': user_id,
                    'message_type': '私聊消息' if is_private else '群聊消息',
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            })
            
        except Exception as plugin_error:
            return api_error_response(f'插件处理错误: {str(plugin_error)}')
            
    except Exception as event_error:
        return api_error_response(f'MessageEvent创建错误: {str(event_error)}')

