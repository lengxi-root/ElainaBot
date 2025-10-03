#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json, random, tempfile, hashlib, datetime, time, re, base64, os 
from function.Access import BOT凭证, BOTAPI, Json, Json取
from function.database import Database
from config import USE_MARKDOWN, IMAGE_BED_CHANNEL_ID, ENABLE_NEW_USER_WELCOME, ENABLE_WELCOME_MESSAGE, ENABLE_FRIEND_ADD_MESSAGE, HIDE_AVATAR_GLOBAL, BILIBILI_IMAGE_BED_CONFIG
from function.log_db import add_log_to_db, record_last_message_id
from core.plugin.message_templates import MessageTemplate, MSG_TYPE_WELCOME, MSG_TYPE_USER_WELCOME, MSG_TYPE_FRIEND_ADD, MSG_TYPE_API_ERROR
from function.httpx_pool import sync_post, get_binary_content

try:
    from web.app import add_error_log
except:
    add_error_log = lambda *a, **k: None

_image_upload_counter = 0
_image_upload_msgid = 0

class MessageEvent:
    GROUP_MESSAGE = 'GROUP_AT_MESSAGE_CREATE'
    DIRECT_MESSAGE = 'C2C_MESSAGE_CREATE'
    INTERACTION = 'INTERACTION_CREATE'
    CHANNEL_MESSAGE = 'AT_MESSAGE_CREATE'
    GROUP_ADD_ROBOT = 'GROUP_ADD_ROBOT'
    GROUP_DEL_ROBOT = 'GROUP_DEL_ROBOT'
    FRIEND_ADD = 'FRIEND_ADD'
    FRIEND_DEL = 'FRIEND_DEL'
    UNKNOWN_MESSAGE = 'UNKNOWN'

    _MESSAGE_TYPE_PARSERS = {
        GROUP_MESSAGE: '_parse_group_message',
        DIRECT_MESSAGE: '_parse_direct_message',
        INTERACTION: '_parse_interaction',
        CHANNEL_MESSAGE: '_parse_channel_message',
        GROUP_ADD_ROBOT: '_parse_group_add_robot',
        GROUP_DEL_ROBOT: '_parse_group_del_robot',
        FRIEND_ADD: '_parse_friend_add',
        FRIEND_DEL: '_parse_friend_del',
    }

    _BASE_ENDPOINTS = {
        'group': {
            'reply': '/v2/groups/{group_id}/messages',
            'recall': '/v2/groups/{group_id}/messages/{message_id}'
        },
        'user': {
            'reply': '/v2/users/{user_id}/messages',
            'recall': '/v2/users/{user_id}/messages/{message_id}'
        },
        'channel': {
            'reply': '/channels/{channel_id}/messages',
            'recall': '/channels/{channel_id}/messages/{message_id}'
        }
    }
    
    _MESSAGE_TYPE_TO_ENDPOINT = {
        GROUP_MESSAGE: 'group',
        DIRECT_MESSAGE: 'user', 
        INTERACTION: 'group',
        CHANNEL_MESSAGE: 'channel',
        GROUP_ADD_ROBOT: 'group',
        GROUP_DEL_ROBOT: 'group',
        FRIEND_ADD: 'user',
        FRIEND_DEL: 'user'
    }

    _IGNORE_ERROR_CODES = [11293, 40054002, 40054003]

    def __init__(self, data, skip_recording=False):
        self.is_private = self.is_group = False
        self.raw_data = data
        self.user_id = self.group_id = None
        self.content = ""
        self.message_type = self.UNKNOWN_MESSAGE
        self.event_type = self.get('t')
        self.message_id = self.get('d/id') or self.get('id') if self.event_type in ('GROUP_AT_MESSAGE_CREATE', 'C2C_MESSAGE_CREATE', 'AT_MESSAGE_CREATE') else self.get('id')
        self.timestamp = self.get('d/timestamp')
        self.matches = None
        self.db = Database()
        self.ignore = False
        self.skip_recording = skip_recording
        self._endpoint_cache = {}
        self._parse_message()
        if not self.ignore and not self.skip_recording:
            self._record_user_and_group()

    def _parse_message(self):
        if self.event_type in self._MESSAGE_TYPE_PARSERS:
            getattr(self, self._MESSAGE_TYPE_PARSERS[self.event_type])()
        else:
            self.message_type = self.UNKNOWN_MESSAGE
            self.content = ""
            self.user_id = self.group_id = self.channel_id = self.guild_id = None
            self.is_group = self.is_private = False

    def _parse_group_message(self):
        self.message_type = self.GROUP_MESSAGE
        self.content = self.sanitize_content(self.get('d/content'))
        self.user_id = self.get('d/author/id')
        self.group_id = self.get('d/group_id')
        self.channel_id = self.guild_id = None
        self.is_group = True
        self.is_private = False

    def _parse_direct_message(self):
        self.message_type = self.DIRECT_MESSAGE
        self.content = self.sanitize_content(self.get('d/content'))
        self.user_id = self.get('d/author/id')
        self.group_id = self.channel_id = self.guild_id = None
        self.is_group = False
        self.is_private = True

    def _parse_interaction(self):
        if self.get('d/type') == 13:
            self.ignore = True
            return
        self.message_type = self.INTERACTION
        chat_type = self.get('d/chat_type')
        scene = self.get('d/scene')
        self.chat_type = chat_type
        self.scene = scene
        if chat_type == 1 or scene == 'group':
            self.group_id = self.get('d/group_openid') or self.get('d/group_id')
            self.user_id = self.get('d/group_member_openid') or self.get('d/author/id')
            self.is_group, self.is_private = True, False
        elif chat_type == 2 or scene == 'c2c':
            self.group_id = None
            self.user_id = self.get('d/user_openid') or self.get('d/author/id')
            self.is_group, self.is_private = False, True
        else:
            self.group_id = self.get('d/group_openid') or self.get('d/group_id')
            self.user_id = self.get('d/group_member_openid') or self.get('d/user_openid') or self.get('d/author/id')
            self.is_group = bool(self.group_id)
            self.is_private = not self.is_group
        self.channel_id = self.guild_id = None
        button_data = self.get('d/data/resolved/button_data')
        self.content = self.sanitize_content(button_data) if button_data else ""
        self.id = self.get('id')

    def _parse_channel_message(self):
        self.message_type = self.CHANNEL_MESSAGE
        raw_content = self.get('d/content')
        mentions = self.get('d/mentions')
        if isinstance(mentions, list) and mentions:
            bot_id = mentions[0].get('id')
            if bot_id and raw_content:
                for prefix in [f'<@!{bot_id}>', f'<@{bot_id}>']:
                    if raw_content.startswith(prefix):
                        raw_content = raw_content[len(prefix):].lstrip()
                        break
        self.content = self.sanitize_content(raw_content.strip() if raw_content else "")
        self.user_id = self.get('d/author/id')
        self.group_id = None
        self.channel_id = self.get('d/channel_id')
        self.guild_id = self.get('d/guild_id')
        self.is_group = self.is_private = False

    def _parse_group_add_robot(self):
        self.message_type = self.GROUP_ADD_ROBOT
        self.user_id = self.get('d/op_member_openid')
        self.group_id = self.get('d/group_openid')
        self.channel_id = self.guild_id = None
        self.is_group, self.is_private = True, False
        self.timestamp = self.get('d/timestamp')
        self.id = self.get('id')
        self.handled = self.welcome_allowed = True
        from function.log_db import add_dau_event_to_db
        add_dau_event_to_db('group_join')
        self.content = f"机器人被邀请加入群聊 {self.group_id}"
        if ENABLE_WELCOME_MESSAGE:
            MessageTemplate.send(self, MSG_TYPE_WELCOME)
        self.welcome_allowed = False

    def _parse_group_del_robot(self):
        self.message_type = self.GROUP_DEL_ROBOT
        self.user_id = self.get('d/op_member_openid')
        self.group_id = self.get('d/group_openid')
        self.channel_id = self.guild_id = None
        self.is_group, self.is_private = True, False
        self.timestamp = self.get('d/timestamp')
        self.id = self.get('id')
        self.content = f"机器人被移出群聊 {self.group_id}"
        self.handled = True
        from function.log_db import add_dau_event_to_db
        add_dau_event_to_db('group_leave')

    def _parse_friend_add(self):
        self.message_type = self.FRIEND_ADD
        self.user_id = self.get('d/openid')
        self.group_id = self.channel_id = self.guild_id = None
        self.is_group, self.is_private = False, True
        self.timestamp = self.get('d/timestamp')
        self.id = self.get('id')
        self.content = f"用户 {self.user_id} 添加机器人为好友"
        self.handled = self.welcome_allowed = True
        from function.log_db import add_dau_event_to_db
        add_dau_event_to_db('friend_add')
        if ENABLE_FRIEND_ADD_MESSAGE:
            MessageTemplate.send(self, MSG_TYPE_FRIEND_ADD)
        self.welcome_allowed = False

    def _parse_friend_del(self):
        self.message_type = self.FRIEND_DEL
        self.user_id = self.get('d/openid')
        self.group_id = self.channel_id = self.guild_id = None
        self.is_group, self.is_private = False, True
        self.timestamp = self.get('d/timestamp')
        self.id = self.get('id')
        self.content = f"用户 {self.user_id} 删除机器人好友"
        self.handled = True
        from function.log_db import add_dau_event_to_db
        add_dau_event_to_db('friend_remove')

    def _check_send_conditions(self):
        return not (self.ignore or (getattr(self, 'handled', False) and not getattr(self, 'welcome_allowed', False)))
    
    def _prepare_media_data(self, data):
        if isinstance(data, str):
            try:
                return get_binary_content(data)
            except:
                import requests
                return requests.get(data).content
        return data
    
    def _set_message_id_in_payload(self, payload):
        if self.message_type in (self.GROUP_MESSAGE, self.DIRECT_MESSAGE):
            payload["msg_id"] = self.message_id
        elif self.message_type in (self.INTERACTION, self.GROUP_ADD_ROBOT):
            payload["event_id"] = self.get('id') or ""
        elif self.message_type == self.CHANNEL_MESSAGE:
            payload["msg_id"] = self.message_id
        return payload
    
    def _handle_auto_recall(self, message_id, auto_delete_time):
        if message_id and auto_delete_time:
            import threading
            threading.Timer(auto_delete_time, self.recall_message, args=[message_id]).start()
    
    def _send_media_message(self, data, content, file_type, content_type, auto_delete_time=None, converter=None):
        if not self._check_send_conditions():
            return None
            
        processed_data = self._prepare_media_data(data)
        
        if converter:
            processed_data = converter(processed_data)
            if not processed_data:
                return None
        
        file_info = self.upload_media(processed_data, file_type)
        if not file_info:
            return None
            
        payload = self._build_media_message_payload(content, file_info)
        endpoint = self._get_endpoint()
        
        if self.message_type in (self.GROUP_ADD_ROBOT, self.FRIEND_ADD):
            event_prefix = "ROBOT_ADD" if self.message_type == self.GROUP_ADD_ROBOT else "FRIEND_ADD"
            payload['event_id'] = self.get('id') or f"{event_prefix}_{int(time.time())}"
        
        message_id = self._send_with_error_handling(payload, endpoint, content_type, f"content: {content}")
        self._handle_auto_recall(message_id, auto_delete_time)
        return message_id

    def reply(self, content='', buttons=None, media=None, hide_avatar_and_center=None, auto_delete_time=None, use_markdown=None):
        if self.ignore or (getattr(self, 'handled', False) and not getattr(self, 'welcome_allowed', False)):
            return None
        if self.message_type not in self._MESSAGE_TYPE_TO_ENDPOINT:
            return None
        buttons = buttons or []
        media_payload = None
        if media:
            if isinstance(media, bytes):
                file_info = self.upload_media(media, file_type=3)
                if file_info:
                    media_payload = {'type': 3, 'file_info': file_info}
            elif isinstance(media, list) and media:
                media_payload = media[0]
        if hide_avatar_and_center is None:
            hide_avatar_and_center = HIDE_AVATAR_GLOBAL
        payload = self._build_message_payload(content, buttons, media_payload, hide_avatar_and_center, use_markdown)
        endpoint = self._get_endpoint('reply')
        if self.message_type in (self.GROUP_ADD_ROBOT, self.FRIEND_ADD):
            if 'event_id' not in payload or not payload['event_id']:
                event_prefix = "ROBOT_ADD" if self.message_type == self.GROUP_ADD_ROBOT else "FRIEND_ADD"
                payload['event_id'] = self.get('id') or f"{event_prefix}_{int(time.time())}"
        message_id = self._send_with_error_handling(payload, endpoint, "消息", f"content: {content}")
        if message_id and auto_delete_time and isinstance(auto_delete_time, (int, float)) and auto_delete_time > 0:
            import threading
            threading.Timer(auto_delete_time, self.recall_message, args=[message_id], daemon=True).start()
        return message_id

    def reply_image(self, image_data, content='', auto_delete_time=None):
        return self._send_media_message(image_data, content, 1, "图片消息", auto_delete_time)

    def reply_voice(self, voice_data, content='', auto_delete_time=None):
        return self._send_media_message(voice_data, content, 3, "语音消息", auto_delete_time, self._convert_to_silk)

    def reply_video(self, video_data, content='', auto_delete_time=None):
        return self._send_media_message(video_data, content, 2, "视频消息", auto_delete_time)

    def reply_ark(self, template_id, kv_data, content='', auto_delete_time=None):
        return self._send_simple_message(
            lambda: self._build_ark_message_payload(template_id, 
                self._convert_simple_ark_data(template_id, kv_data) if isinstance(kv_data, (tuple, list)) and template_id in [23, 24, 37] else kv_data, content),
            "ark卡片消息",
            auto_delete_time
        )

    def reply_markdown(self, template, params=None, keyboard_id=None, hide_avatar_and_center=None, auto_delete_time=None):
        if hide_avatar_and_center is None:
            hide_avatar_and_center = HIDE_AVATAR_GLOBAL
            
        def build_payload():
            template_data = self._build_markdown_template_data(template, params)
            if not template_data:
                return None
            return self._build_markdown_message_payload(template_data, keyboard_id, hide_avatar_and_center)
        
        if not self._check_send_conditions():
            return None
            
        payload = build_payload()
        if not payload:
            return None
            
        endpoint = self._get_endpoint()
        
        if self.message_type in (self.GROUP_ADD_ROBOT, self.FRIEND_ADD):
            event_prefix = "ROBOT_ADD" if self.message_type == self.GROUP_ADD_ROBOT else "FRIEND_ADD"
            payload['event_id'] = self.get('id') or f"{event_prefix}_{int(time.time())}"
        
        message_id = self._send_with_error_handling(payload, endpoint, "markdown模板消息", f"template: {template}, params: {params}")
        self._handle_auto_recall(message_id, auto_delete_time)
        return message_id

    def reply_md(self, template, params=None, keyboard_id=None, hide_avatar_and_center=None, auto_delete_time=None):
        return self.reply_markdown(template, params, keyboard_id, hide_avatar_and_center, auto_delete_time)

    def _get_endpoint(self, action='reply'):
        cache_key = (self.message_type, action, self.is_private)
        if cache_key in self._endpoint_cache:
            return self._endpoint_cache[cache_key]
        endpoint_type = self._MESSAGE_TYPE_TO_ENDPOINT.get(self.message_type)
        if not endpoint_type:
            raise ValueError(f"不支持的消息类型: {self.message_type}")
        if self.message_type == self.INTERACTION and self.is_private:
            endpoint_type = 'user'
        endpoint = self._fill_endpoint_template(self._BASE_ENDPOINTS[endpoint_type][action])
        self._endpoint_cache[cache_key] = endpoint
        return endpoint

    def _cleanup_temp_files(self, *file_paths):
        for path in file_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass

    def _convert_to_silk(self, audio_data):
        import subprocess, tempfile
        audio_path = pcm_path = silk_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as f:
                audio_path = f.name
                f.write(audio_data)
            pcm_path = audio_path + '.pcm'
            subprocess.run(['ffmpeg', '-y', '-i', audio_path, '-ar', '48000', '-ac', '1', '-f', 's16le', '-loglevel', 'error', '-hide_banner', pcm_path], 
                         check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import pilk
            silk_path = audio_path + '.silk'
            pilk.encode(pcm_path, silk_path, pcm_rate=48000, tencent=True)
            with open(silk_path, 'rb') as f:
                return f.read()
        except:
            return None
        finally:
            self._cleanup_temp_files(audio_path, pcm_path, silk_path)

    def _parse_response(self, response):
        if not response:
            return None
        if hasattr(self, '_response_cache') and response in self._response_cache:
            return self._response_cache[response]
        try:
            parsed = json.loads(response) if isinstance(response, str) else response if isinstance(response, dict) else None
            if parsed:
                if not hasattr(self, '_response_cache'):
                    self._response_cache = {}
                if len(self._response_cache) < 10:
                    self._response_cache[response] = parsed
            return parsed
        except:
            return None

    def recall_message(self, message_id):
        if not message_id:
            return None
        try:
            endpoint = self._get_endpoint('recall').replace('{message_id}', str(message_id))
            return BOTAPI(endpoint, "DELETE", None)
        except:
            return None

    def _send_with_error_handling(self, payload, endpoint, content_type="消息", extra_info=""):
        for retry_count in range(2):
            response = BOTAPI(endpoint, "POST", Json(payload))
            resp_obj = self._parse_response(response)
            if resp_obj and all(k in resp_obj for k in ("message", "code", "trace_id")):
                error_code = resp_obj.get('code')
                if error_code in self._IGNORE_ERROR_CODES:
                    return None
                if error_code == 11244 and retry_count < 1:
                    from function.Access import 获取新Token
                    获取新Token()
                    time.sleep(1)
                    continue
                self._log_error(f"发送{content_type}失败：{resp_obj.get('message')} code：{error_code}", resp_obj=resp_obj, send_payload=payload)
                MessageTemplate.send(self, MSG_TYPE_API_ERROR, error_code=error_code, trace_id=resp_obj.get('trace_id'), endpoint=endpoint)
                import json
                return json.dumps({
                    'error': True,
                    'message': resp_obj.get('message', '未知错误'),
                    'code': error_code
                })
            return self._extract_message_id(response)
        return None

    def _send_simple_message(self, payload_builder, content_type, auto_delete_time=None, **kwargs):
        if not self._check_send_conditions():
            return None
        payload = payload_builder(**kwargs)
        endpoint = self._get_endpoint()
        if self.message_type in (self.GROUP_ADD_ROBOT, self.FRIEND_ADD):
            event_prefix = "ROBOT_ADD" if self.message_type == self.GROUP_ADD_ROBOT else "FRIEND_ADD"
            payload['event_id'] = self.get('id') or f"{event_prefix}_{int(time.time())}"
        message_id = self._send_with_error_handling(payload, endpoint, content_type)
        self._handle_auto_recall(message_id, auto_delete_time)
        return message_id

    def _build_message_payload(self, content, buttons, media, hide_avatar_and_center=False, use_markdown=None):
        # use_markdown 参数：None=使用全局配置, True=强制markdown, False=强制普通消息
        should_use_markdown = USE_MARKDOWN if use_markdown is None else use_markdown
        msg_type = 7 if media else (2 if should_use_markdown else 0)
        payload = {
            "msg_type": msg_type,
            "msg_seq": random.randint(10000, 999999)
        }
        
        if self.message_type in (self.GROUP_MESSAGE, self.DIRECT_MESSAGE):
            payload["msg_id"] = self.message_id
        elif self.message_type in (self.INTERACTION, self.GROUP_ADD_ROBOT, self.FRIEND_ADD):
            payload["event_id"] = self.get('id') or ""
        elif self.message_type == self.CHANNEL_MESSAGE:
            payload["msg_id"] = self.message_id
        
        if media:
            payload['content'] = ''
        elif content:
            if should_use_markdown:
                payload['markdown'] = {'content': content}
                if hide_avatar_and_center:
                    if 'style' not in payload['markdown']:
                        payload['markdown']['style'] = {}
                    payload['markdown']['style']['layout'] = 'hide_avatar_and_center'
            else:
                payload['content'] = content
        
        if buttons:
            payload['keyboard'] = buttons
        if media:
            if isinstance(media, dict) and 'file_info' in media:
                payload['media'] = {'file_info': media['file_info']}
            else:
                payload['media'] = media
                
        return payload

    def _build_media_message_payload(self, content, file_info):
        payload = {
            "msg_type": 7,
            "msg_seq": random.randint(10000, 999999),
            "content": content or '',
            "media": {'file_info': file_info}
        }
        return self._set_message_id_in_payload(payload)

    def _build_ark_message_payload(self, template_id, kv_data, content):
        payload = {
            "msg_type": 3,
            "msg_seq": random.randint(10000, 999999),
            "content": content or '',
            "ark": {
                "template_id": template_id,
                "kv": kv_data
            }
        }
        return self._set_message_id_in_payload(payload)

    def _convert_simple_ark_data(self, template_id, simple_data):
        if template_id == 24:
            keys = ["#DESC#", "#PROMPT#", "#TITLE#", "#METADESC#", "#IMG#", "#LINK#", "#SUBTITLE#"]
            kv_data = []
            for i, value in enumerate(simple_data):
                if i < len(keys) and value is not None:
                    kv_data.append({"key": keys[i], "value": str(value)})
            return kv_data
            
        elif template_id == 37:
            keys = ["#PROMPT#", "#METATITLE#", "#METASUBTITLE#", "#METACOVER#", "#METAURL#"]
            kv_data = []
            for i, value in enumerate(simple_data):
                if i < len(keys) and value is not None:
                    kv_data.append({"key": keys[i], "value": str(value)})
            return kv_data
            
        elif template_id == 23:
            kv_data = []
            if len(simple_data) >= 1 and simple_data[0] is not None:
                kv_data.append({"key": "#DESC#", "value": str(simple_data[0])})
            if len(simple_data) >= 2 and simple_data[1] is not None:
                kv_data.append({"key": "#PROMPT#", "value": str(simple_data[1])})
            if len(simple_data) >= 3 and simple_data[2] is not None:
                obj_list = []
                for item in simple_data[2]:
                    if item and len(item) >= 1:
                        obj_kv = []
                        if item[0] and str(item[0]).strip():
                            obj_kv.append({"key": "desc", "value": str(item[0])})
                        if len(item) >= 2 and item[1] and str(item[1]).strip():
                            obj_kv.append({"key": "link", "value": str(item[1])})
                        if obj_kv:
                            obj_list.append({"obj_kv": obj_kv})
                kv_data.append({"key": "#LIST#", "obj": obj_list})
            return kv_data
            
        return simple_data

    def _build_markdown_template_data(self, template, params):
        try:
            from core.event.markdown_templates import get_template, MARKDOWN_TEMPLATES
            if not hasattr(self, '_template_id_cache'):
                self._template_id_cache = {}
            if template in self._template_id_cache:
                template_config = self._template_id_cache[template]
            else:
                template_config = get_template(template)
                if not template_config:
                    for name, config in MARKDOWN_TEMPLATES.items():
                        if config['id'] == template:
                            template_config = config
                            break
                self._template_id_cache[template] = template_config
            if not template_config:
                return None
            template_id = template_config['id']
            template_params = template_config['params']
            param_list = [{"key": param_name, "values": [str(params[i])]} for i, param_name in enumerate(template_params) if params and i < len(params) and params[i] is not None] if params else []
            return {"custom_template_id": template_id, "params": param_list}
        except:
            return None

    def _build_markdown_message_payload(self, template_data, keyboard_id=None, hide_avatar_and_center=False):
        payload = {
            "msg_type": 2,
            "msg_seq": random.randint(10000, 999999),
            "markdown": {
                "custom_template_id": template_data["custom_template_id"]
            }
        }
        
        if template_data.get("params"):
            payload["markdown"]["params"] = template_data["params"]
        
        if hide_avatar_and_center:
            if 'style' not in payload['markdown']:
                payload['markdown']['style'] = {}
            payload['markdown']['style']['layout'] = 'hide_avatar_and_center'
        
        if keyboard_id:
            payload["keyboard"] = {
                "id": str(keyboard_id)
            }
        
        return self._set_message_id_in_payload(payload)

    def _fill_endpoint_template(self, template):
        replacements = {
            '{group_id}': self.group_id,
            '{user_id}': self.user_id,
            '{channel_id}': self.channel_id
        }
        
        for key, value in replacements.items():
            if key in template and value:
                template = template.replace(key, value)
                
        return template

    def _extract_message_id(self, response):
        if not response:
            return None
        try:
            data = json.loads(response) if isinstance(response, str) else response if isinstance(response, dict) else None
            return data.get('id') or data.get('msg_id') or data.get('message_id') if data else response
        except:
            return response

    def upload_media(self, file_bytes, file_type):
        endpoint = f"/v2/groups/{self.group_id}/files" if self.is_group else f"/v2/users/{self.user_id}/files"
        req_data = {"srv_send_msg": False, "file_type": file_type, "file_data": base64.b64encode(file_bytes).decode()}
        resp = BOTAPI(endpoint, "POST", Json(req_data))
        if isinstance(resp, str):
            try:
                resp = json.loads(resp)
            except:
                return None
        return resp.get('file_info')

    def uploadToQQImageBed(self, image_data):
        return self.uploadToQQBotImageBed(image_data)

    def uploadToQQBotImageBed(self, image_data):
        global _image_upload_counter, _image_upload_msgid
        access_token = BOT凭证() or ''
        if not (access_token and IMAGE_BED_CHANNEL_ID):
            return ''
        md5hash = hashlib.md5(image_data).hexdigest().upper()
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(image_data)
                temp_path = f.name
            import magic
            mime = magic.Magic(mime=True)
            mime_type = mime.from_buffer(image_data)
            filename = f'image.{mime_type.split("/")[1]}'
            _image_upload_counter += 1
            if _image_upload_counter > 9000:
                _image_upload_msgid += 1
                _image_upload_counter = 0
            files = {'file_image': (filename, open(temp_path, 'rb'), mime_type)}
            sync_post(f'https://api.sgroup.qq.com/channels/{IMAGE_BED_CHANNEL_ID}/messages', files=files, 
                     data={'msg_id': str(_image_upload_msgid)}, headers={'Authorization': f'QQBot {access_token}'})
        except:
            pass
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except:
                    pass
        return f'https://gchat.qpic.cn/qmeetpic/0/0-0-{md5hash}/0'

    def uploadToBilibiliImageBed(self, image_data):
        if not BILIBILI_IMAGE_BED_CONFIG.get('enabled', False):
            return ''
        csrf_token = BILIBILI_IMAGE_BED_CONFIG.get('csrf_token', '')
        sessdata = BILIBILI_IMAGE_BED_CONFIG.get('sessdata', '')
        if not csrf_token or not sessdata or len(image_data) > 20 * 1024 * 1024:
            return ''
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as f:
                f.write(image_data)
                temp_path = f.name
            try:
                import magic
                mime_type = magic.Magic(mime=True).from_buffer(image_data)
            except:
                mime_type = 'image/jpeg'
            filename = f'image.{mime_type.split("/")[1] if "/" in mime_type else "jpg"}'
            files = {'file': (filename, open(temp_path, 'rb'), mime_type)}
            response = sync_post('https://api.bilibili.com/x/upload/web/image', files=files,
                data={'bucket': BILIBILI_IMAGE_BED_CONFIG.get('bucket', 'openplatform'), 'csrf': csrf_token},
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36', 
                        'Cookie': f'SESSDATA={sessdata}; bili_jct={csrf_token}'}, timeout=30)
            files['file'][1].close()
            if response and hasattr(response, 'json'):
                resp_data = response.json()
                if resp_data.get('code') == 0 and resp_data.get('data', {}).get('location'):
                    image_url = resp_data['data']['location']
                    return image_url.replace('http://', 'https://') if image_url.startswith('http://') else image_url
            return ''
        except:
            return ''
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except:
                    pass

    def _record_message_to_db(self):
        self._record_message_to_db_only()
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._notify_web_display(timestamp)
    
    def _record_message_to_db_only(self):
        from function.log_db import add_log_to_db
        from config import SAVE_RAW_MESSAGE_TO_DB
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db_entry = {'timestamp': timestamp, 'content': self.content or "", 'user_id': self.user_id or "未知用户", 'group_id': self.group_id or "c2c"}
        if SAVE_RAW_MESSAGE_TO_DB:
            db_entry['raw_message'] = json.dumps(self.raw_data, ensure_ascii=False, indent=2) if isinstance(self.raw_data, dict) else str(self.raw_data)
        add_log_to_db('received', db_entry)

    def _notify_web_display(self, timestamp):
        from web.app import add_display_message
        # formatted_message 仅用于兼容旧版本，web界面应使用结构化字段
        # 只传递消息内容，避免在消息列显示ID
        formatted_message = self.content or ""
        add_display_message(
            formatted_message, 
            timestamp, 
            user_id=self.user_id,
            group_id=self.group_id if self.group_id and self.group_id != "c2c" else None,
            message_content=self.content
        )

    def _record_user_and_group(self):
        user_is_new = False
        if self.user_id:
            if hasattr(self.db, 'exists_user') and not self.db.exists_user(self.user_id):
                user_is_new = True
            self.db.add_user(self.user_id)
        if self.group_id:
            self.db.add_group(self.group_id)
            if self.user_id:
                self.db.add_user_to_group(self.group_id, self.user_id)
        if self.is_private and self.user_id:
            self.db.add_member(self.user_id)
        if user_is_new and self.is_group and ENABLE_NEW_USER_WELCOME and self.message_type not in {self.GROUP_ADD_ROBOT, self.GROUP_DEL_ROBOT, self.FRIEND_ADD, self.FRIEND_DEL}:
            try:
                MessageTemplate.send(self, MSG_TYPE_USER_WELCOME, user_count=self.db.get_user_count())
            except:
                pass

    def record_last_message_id(self):
        if self.message_type in (self.GROUP_DEL_ROBOT, self.FRIEND_DEL):
            return False
        message_id_to_record = self.message_id if self.message_type in (self.GROUP_MESSAGE, self.DIRECT_MESSAGE, self.CHANNEL_MESSAGE) else self.get('id') if self.message_type in (self.INTERACTION, self.GROUP_ADD_ROBOT, self.FRIEND_ADD) else None
        if not message_id_to_record:
            return False
        if self.is_group and self.group_id:
            return record_last_message_id('group', self.group_id, message_id_to_record)
        elif self.is_private and self.user_id:
            return record_last_message_id('user', self.user_id, message_id_to_record)
        elif self.message_type == self.CHANNEL_MESSAGE and self.channel_id:
            return record_last_message_id('channel', self.channel_id, message_id_to_record)
        return False

    def _log_error(self, msg, tb=None, resp_obj=None, send_payload=None, raw_message=None):
        log_data = {'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'content': msg, 'traceback': tb or ""}
        if resp_obj is not None:
            log_data['resp_obj'] = str(resp_obj)
        if send_payload is not None:
            log_data['send_payload'] = json.dumps(send_payload, ensure_ascii=False, indent=2)
        if raw_message is not None:
            log_data['raw_message'] = json.dumps(raw_message, ensure_ascii=False, indent=2) if isinstance(raw_message, dict) else str(raw_message)
        add_log_to_db('error', log_data)
        add_error_log(msg, tb or "")

    def get(self, path):
        return Json取(self.raw_data, path)

    _face_pattern = re.compile(r'<faceType=\d+,faceId="[^"]+",ext="[^"]+">')
    
    def sanitize_content(self, content):
        if not content:
            return ""
        content = str(content)
        if content and content[0] == '/':
            content = content[1:]
        return self._face_pattern.sub('', content).strip()

    def rows(self, buttons):
        if not isinstance(buttons, list):
            buttons = [buttons]
        result = []
        for button in buttons:
            button_obj = {
                'id': button.get('id', str(random.randint(10000, 999999))),
                'render_data': {'label': button.get('text', button.get('link', '')), 'visited_label': button.get('show', button.get('text', button.get('link', ''))), 'style': button.get('style', 0)},
                'action': {'type': 0 if 'link' in button else button.get('type', 2), 'data': button.get('data', button.get('link', button.get('text', ''))), 'unsupport_tips': button.get('tips', '.'), 'permission': {'type': 2}}
            }
            if 'enter' in button and button['enter']:
                button_obj['action']['enter'] = True
            if 'reply' in button and button['reply']:
                button_obj['action']['reply'] = True
            if 'admin' in button:
                button_obj['action']['permission']['type'] = 1
            if 'list' in button:
                button_obj['action']['permission']['type'] = 0
                button_obj['action']['permission']['specify_user_ids'] = button['list']
            if 'role' in button:
                button_obj['action']['permission']['type'] = 3
                button_obj['action']['permission']['specify_role_ids'] = button['role']
            if 'limit' in button:
                button_obj['action']['click_limit'] = button['limit']
            result.append(button_obj)
        return {'buttons': result}

    def button(self, rows=None):
        return {'content': {'rows': rows or []}} 

    def get_image_size(self, image_input):
        try:
            from PIL import Image
            import io, os
            if isinstance(image_input, bytes):
                with Image.open(io.BytesIO(image_input)) as img:
                    width, height = img.size
                    return {'width': width, 'height': height, 'px': f'#{width}px #{height}px'}
            elif isinstance(image_input, str):
                if image_input.startswith(('http://', 'https://')):
                    import requests
                    response = requests.get(image_input, headers={'Range': 'bytes=0-65535'}, stream=True, timeout=10)
                    if response.status_code in [206, 200]:
                        with Image.open(io.BytesIO(response.content)) as img:
                            width, height = img.size
                            return {'width': width, 'height': height, 'px': f'#{width}px #{height}px'}
                elif os.path.exists(image_input):
                    with Image.open(image_input) as img:
                        width, height = img.size
                        return {'width': width, 'height': height, 'px': f'#{width}px #{height}px'}
            return None
        except:
            return None