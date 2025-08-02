#!/usr/bin/env python
# -*- coding: utf-8 -*-

# ===== 1. 标准库导入 =====
import json
import random
import tempfile
import hashlib
import datetime
import time
import re
import base64
import traceback
import os

# ===== 2. 第三方库导入 =====

# ===== 3. 自定义模块导入 =====
from function.Access import BOT凭证, BOTAPI, Json, Json取
from function.database import Database
from config import USE_MARKDOWN, IMAGE_BED, ENABLE_NEW_USER_WELCOME, ENABLE_WELCOME_MESSAGE, HIDE_AVATAR_GLOBAL
from function.log_db import add_log_to_db
from core.plugin.message_templates import MessageTemplate, MSG_TYPE_WELCOME, MSG_TYPE_USER_WELCOME, MSG_TYPE_API_ERROR
from function.httpx_pool import sync_post

# ===== 4. 可选模块导入（带异常处理）=====
try:
    from web_panel.app import add_error_log
except ImportError:
    def add_error_log(log, traceback_info=None):
        pass

# ===== 5. 全局变量 =====
_image_upload_counter = 0
_image_upload_msgid = 0

# ===== 6. 主类定义 =====
class MessageEvent:
    # --- 常量定义 ---
    GROUP_MESSAGE = 'GROUP_AT_MESSAGE_CREATE'
    DIRECT_MESSAGE = 'C2C_MESSAGE_CREATE'
    INTERACTION = 'INTERACTION_CREATE'
    CHANNEL_MESSAGE = 'AT_MESSAGE_CREATE'
    GROUP_ADD_ROBOT = 'GROUP_ADD_ROBOT'
    UNKNOWN_MESSAGE = 'UNKNOWN'

    # 消息类型与解析方法的映射表
    _MESSAGE_TYPE_PARSERS = {
        GROUP_MESSAGE: '_parse_group_message',
        DIRECT_MESSAGE: '_parse_direct_message',
        INTERACTION: '_parse_interaction',
        CHANNEL_MESSAGE: '_parse_channel_message',
        GROUP_ADD_ROBOT: '_parse_group_add_robot',
    }

    # API接口路径模板
    _API_ENDPOINTS = {
        GROUP_MESSAGE: {
            'reply': '/v2/groups/{group_id}/messages',
            'recall': '/v2/groups/{group_id}/messages/{message_id}'
        },
        DIRECT_MESSAGE: {
            'reply': '/v2/users/{user_id}/messages',
            'recall': '/v2/users/{user_id}/messages/{message_id}'
        },
        INTERACTION: {
            'reply': '/v2/groups/{group_id}/messages',
            'reply_private': '/v2/users/{user_id}/messages',
            'recall': '/v2/groups/{group_id}/messages/{message_id}',
            'recall_private': '/v2/users/{user_id}/messages/{message_id}'
        },
        CHANNEL_MESSAGE: {
            'reply': '/channels/{channel_id}/messages',
            'recall': '/channels/{channel_id}/messages/{message_id}'
        },
        GROUP_ADD_ROBOT: {
            'reply': '/v2/groups/{group_id}/messages'
        }
    }

    # 特定错误码列表，被移出群聊或被禁言
    _IGNORE_ERROR_CODES = [11293, 40054002, 40054003]

    def __init__(self, data):
        self.is_private = False
        self.is_group = False
        self.raw_data = data
        self.user_id = None
        self.group_id = None
        self.content = ""
        self.message_type = self.UNKNOWN_MESSAGE
        self.event_type = self.get('t')
        self.message_id = self.get('d/id')
        self.timestamp = self.get('d/timestamp')
        self.matches = None
        self.db = Database()
        self.ignore = False
        self._parse_message()
        if not self.ignore:
            self._record_user_and_group()

    # --- 消息解析相关 ---
    def _parse_message(self):
        if self.event_type in self._MESSAGE_TYPE_PARSERS:
            parse_method = getattr(self, self._MESSAGE_TYPE_PARSERS[self.event_type])
            parse_method()
            if self.ignore:
                return
        else:
            raw_data_str = json.dumps(self.raw_data, ensure_ascii=False, indent=2) if isinstance(self.raw_data, dict) else str(self.raw_data)
            self._log_error(f"未知消息类型: event_type={self.event_type}", f'原始消息数据: {raw_data_str}')
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
        interaction_type = self.get('d/type')
        if interaction_type == 13:
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
            self.is_group = True
            self.is_private = False
        elif chat_type == 2 or scene == 'c2c':
            self.group_id = None
            self.user_id = self.get('d/user_openid') or self.get('d/author/id')
            self.is_group = False
            self.is_private = True
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
                mention_prefix = f'<@!{bot_id}>'
                if raw_content.startswith(mention_prefix):
                    raw_content = raw_content[len(mention_prefix):].lstrip()
                else:
                    mention_prefix2 = f'<@{bot_id}>'
                    if raw_content.startswith(mention_prefix2):
                        raw_content = raw_content[len(mention_prefix2):].lstrip()
                        
        self.content = self.sanitize_content(raw_content.strip() if raw_content else "")
        self.user_id = self.get('d/author/id')
        self.group_id = None
        self.channel_id = self.get('d/channel_id')
        self.guild_id = self.get('d/guild_id')
        self.is_group = False
        self.is_private = False

    def _parse_group_add_robot(self):
        self.message_type = self.GROUP_ADD_ROBOT
        self.content = ""
        self.user_id = self.get('d/op_member_openid')
        self.group_id = self.get('d/group_openid')
        self.channel_id = self.guild_id = None
        self.is_group = True
        self.is_private = False
        self.timestamp = self.get('d/timestamp')
        self.id = self.get('id')
        
        self.handled = True
        self.welcome_allowed = True
        
        if ENABLE_WELCOME_MESSAGE:
            MessageTemplate.send(self, MSG_TYPE_WELCOME)
            
        self.welcome_allowed = False

    # --- API交互相关 ---
    def reply(self, content='', buttons=None, media=None, hide_avatar_and_center=None, auto_delete_time=None):
        if self.ignore or (getattr(self, 'handled', False) and not getattr(self, 'welcome_allowed', False)):
            return None
            
        if self.message_type not in self._API_ENDPOINTS:
            raw_data_str = json.dumps(self.raw_data, ensure_ascii=False, indent=2) if isinstance(self.raw_data, dict) else str(self.raw_data)
            self._log_error(
                f"不支持的消息类型: {self.message_type}，无法自动回复。",
                f"content: {content}\nraw_data: {raw_data_str}\ntraceback: {traceback.format_exc()}"
            )
            return "不支持的消息类型，无法自动回复。"
            
        buttons = buttons or []
        media_payload = None
        
        if media:
            if isinstance(media, bytes):
                file_info = self.upload_media(media, file_type=3)
                if file_info:
                    media_payload = {'type': 3, 'file_info': file_info}
            elif isinstance(media, list) and media:
                media_payload = media[0]
                
        # 如果没有明确指定hide_avatar_and_center，则使用全局配置
        if hide_avatar_and_center is None:
            hide_avatar_and_center = HIDE_AVATAR_GLOBAL
            
        payload = self._build_message_payload(content, buttons, media_payload, hide_avatar_and_center)
        
        if self.message_type == self.INTERACTION and self.is_private:
            endpoint_template = self._API_ENDPOINTS[self.message_type]['reply_private']
        else:
            endpoint_template = self._API_ENDPOINTS[self.message_type]['reply']
            
        endpoint = self._fill_endpoint_template(endpoint_template)
        
        if self.message_type == self.GROUP_ADD_ROBOT:
            if 'event_id' not in payload or not payload['event_id']:
                payload['event_id'] = self.get('id') or f"ROBOT_ADD_{int(time.time())}"
        
        max_retries = 2
        retry_count = 0
        
        while retry_count < max_retries:
            response = BOTAPI(endpoint, "POST", Json(payload))
            resp_obj = self._parse_response(response)
            
            if resp_obj and all(k in resp_obj for k in ("message", "code", "trace_id")):
                error_code = resp_obj.get('code')
                
                # 特定错误码处理
                if error_code in self._IGNORE_ERROR_CODES:
                    return None
                
                # Token过期特殊处理
                if error_code == 11244:
                    retry_count += 1
                    if retry_count < max_retries:
                        from function.Access import 获取新Token
                        获取新Token()
                        time.sleep(1)
                        continue
                    else:
                        self._log_error(
                            "Token过期重试2次后仍然失败",
                            f"content: {content}\npayload: {json.dumps(payload, ensure_ascii=False)}\nraw_message: {json.dumps(self.raw_data, ensure_ascii=False, indent=2) if isinstance(self.raw_data, dict) else str(self.raw_data)}"
                        )
                        return None
                
                # 其他错误码处理
                self._log_error(
                    f"消息发送失败：{resp_obj.get('message')} code：{error_code} trace_id：{resp_obj.get('trace_id')}", 
                    f"resp_obj: {str(resp_obj)}\nsend_payload: {json.dumps(payload, ensure_ascii=False)}\nraw_message: {json.dumps(self.raw_data, ensure_ascii=False, indent=2) if isinstance(self.raw_data, dict) else str(self.raw_data)}"
                )
                return MessageTemplate.send(self, MSG_TYPE_API_ERROR, error_code=error_code, 
                                           trace_id=resp_obj.get('trace_id'), endpoint=endpoint)
            
            # 没有错误则返回消息ID
            message_id = self._extract_message_id(response)
            
            # 如果设置了自动撤回时间，启动定时器
            if message_id and auto_delete_time and isinstance(auto_delete_time, (int, float)) and auto_delete_time > 0:
                import threading
                timer = threading.Timer(auto_delete_time, self.recall_message, args=[message_id])
                timer.daemon = True
                timer.start()
                
            return message_id
        
        return None

    def _parse_response(self, response):
        """解析API响应"""
        if not response:
            return None
            
        try:
            if isinstance(response, str):
                return json.loads(response)
            elif isinstance(response, dict):
                return response
        except Exception:
            pass
        return None

    def recall_message(self, message_id):
        if message_id is None or self.message_type not in self._API_ENDPOINTS:
            return None
            
        try:
            if self.message_type == self.INTERACTION and self.is_private:
                endpoint_template = self._API_ENDPOINTS[self.message_type]['recall_private']
            elif 'recall' in self._API_ENDPOINTS[self.message_type]:
                endpoint_template = self._API_ENDPOINTS[self.message_type]['recall']
            else:
                return None
                
            endpoint = self._fill_endpoint_template(endpoint_template)
            endpoint = endpoint.replace('{message_id}', str(message_id))
            return BOTAPI(endpoint, "DELETE", None)
            
        except Exception as e:
            self._log_error(f"撤回消息时发生错误: {str(e)}")
            return None

    def _build_message_payload(self, content, buttons, media, hide_avatar_and_center=False):
        msg_type = 7 if media else (2 if USE_MARKDOWN else 0)
        payload = {
            "msg_type": msg_type,
            "msg_seq": random.randint(10000, 999999)
        }
        
        # 设置msg_id或event_id
        if self.message_type in (self.GROUP_MESSAGE, self.DIRECT_MESSAGE):
            payload["msg_id"] = self.message_id
        elif self.message_type in (self.INTERACTION, self.GROUP_ADD_ROBOT):
            payload["event_id"] = self.get('id') or self.get('d/id') or ""
        elif self.message_type == self.CHANNEL_MESSAGE:
            payload["msg_id"] = self.get('d/id')
        
        # 设置内容
        if media:
            payload['content'] = ''
        elif content:
            if USE_MARKDOWN:
                payload['markdown'] = {'content': content}
                # 添加隐藏头像和居中参数
                if hide_avatar_and_center:
                    if 'style' not in payload['markdown']:
                        payload['markdown']['style'] = {}
                    payload['markdown']['style']['layout'] = 'hide_avatar_and_center'
            else:
                payload['content'] = content
        
        # 添加按钮和媒体
        if buttons:
            payload['keyboard'] = buttons
        if media:
            if isinstance(media, dict) and 'file_info' in media:
                payload['media'] = {'file_info': media['file_info']}
            else:
                payload['media'] = media
                
        return payload

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
            if isinstance(response, str):
                response_data = json.loads(response)
                return response_data.get('id') or response_data.get('msg_id') or response_data.get('message_id')
            elif isinstance(response, dict):
                return response.get('id') or response.get('msg_id') or response.get('message_id')
        except Exception:
            pass
            
        return response

    # --- 媒体上传相关 ---
    def upload_media(self, file_bytes, file_type):
        endpoint = f"/v2/groups/{self.group_id}/files" if self.is_group else f"/v2/users/{self.user_id}/files"
        req_data = {
            "srv_send_msg": False,
            "file_type": file_type,
            "file_data": base64.b64encode(file_bytes).decode()
        }
        resp = BOTAPI(endpoint, "POST", Json(req_data))
        
        if isinstance(resp, str):
            try:
                resp = json.loads(resp)
            except:
                return None
                
        return resp.get('file_info')

    def uploadToQQImageBed(self, image_data, type=None):
        if type is None:
            config = IMAGE_BED or {}
            has_qqbot_config = bool(config.get('qq_bot', {}).get('channel_id'))
            has_qqshare_config = bool(config.get('qq_share', {}).get('p_uin')) and bool(config.get('qq_share', {}).get('p_skey'))
            
            if has_qqbot_config:
                type = 'qqbot'
            elif has_qqshare_config:
                type = 'qqshare'
            else:
                return ''
                
        return self.uploadToQQBotImageBed(image_data) if type == 'qqbot' else self.uploadToQQShareImageBed(image_data)

    def uploadToQQBotImageBed(self, image_data):
        global _image_upload_counter, _image_upload_msgid
        
        access_token = BOT凭证() or ''
        channel = (IMAGE_BED or {}).get('qq_bot', {}).get('channel_id')
        appid = (IMAGE_BED or {}).get('qq_bot', {}).get('appid', '')
        
        if not (access_token and channel):
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
            data = {'msg_id': str(_image_upload_msgid)}
            headers = {'Authorization': f'QQBot {access_token}'}
            
            if appid:
                headers['X-Union-Appid'] = appid
                
            sync_post(
                f'https://api.sgroup.qq.com/channels/{channel}/messages',
                files=files,
                data=data,
                headers=headers
            )
            
        except Exception:
            pass
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        return f'https://gchat.qpic.cn/qmeetpic/0/0-0-{md5hash}/0'

    def uploadToQQShareImageBed(self, image_data):
        config = (IMAGE_BED or {}).get('qq_share', {})
        p_uin = config.get('p_uin', '')
        p_skey = config.get('p_skey', '')
        
        if not (p_uin and p_skey):
            return ''
            
        temp_path = None
        
        try:
            with tempfile.NamedTemporaryFile(delete=False) as f:
                f.write(image_data)
                temp_path = f.name
                
            filename = f'upload_{int(random.random() * 10000)}.png'
            files = {'share_image': (filename, open(temp_path, 'rb'), 'image/png')}
            cookies = {'p_uin': p_uin, 'p_skey': p_skey}
            headers = {
                'User-Agent': 'android_34_OP5D06L1_14_9.1.5',
                'Referer': 'http://www.qq.com',
                'Host': 'cgi.connect.qq.com',
                'Accept-Encoding': 'gzip'
            }
            
            response = sync_post(
                'https://cgi.connect.qq.com/qqconnectopen/upload_share_image',
                files=files,
                cookies=cookies,
                headers=headers
            )
            
            if response.status_code == 200:
                result = response.json()
                if 'url' in result:
                    return result['url']
                    
        except Exception:
            pass
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except:
                    pass
                    
        return ""

    # --- 数据库与日志 ---
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
            
        if user_is_new and self.is_group and ENABLE_NEW_USER_WELCOME and self.message_type != self.GROUP_ADD_ROBOT:
            try:
                user_count = self.db.get_user_count()
                MessageTemplate.send(self, MSG_TYPE_USER_WELCOME, user_count=user_count)
            except Exception as e:
                self._log_error(f'新用户欢迎消息发送失败: {str(e)}')

    def _log_error(self, msg, tb=None):
        log_data = {
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'content': msg,
            'traceback': tb or traceback.format_exc()
        }
        add_log_to_db('error', log_data)
        add_error_log(msg, tb or traceback.format_exc())

    # --- 工具方法 ---
    def get(self, path):
        return Json取(self.raw_data, path)

    # 预编译正则表达式，避免重复编译
    _face_pattern = re.compile(r'<faceType=\d+,faceId="[^"]+",ext="[^"]+">')
    
    def sanitize_content(self, content):
        if not content:
            return ""
            
        content = str(content)
        if content.startswith('/'):
            content = content[1:]
            
        # 使用预编译的正则表达式
        content = self._face_pattern.sub('', content)
        
        return content.strip()

    def rows(self, buttons):
        if not isinstance(buttons, list):
            buttons = [buttons]
            
        result = []
        for button in buttons:
            button_type = 0 if 'link' in button else button.get('type', 2)
            button_obj = {
                'id': button.get('id', str(random.randint(10000, 999999))),
                'render_data': {
                    'label': button.get('text', button.get('link', '')),
                    'visited_label': button.get('show', button.get('text', button.get('link', ''))),
                    'style': button.get('style', 0)
                },
                'action': {
                    'type': button_type,
                    'data': button.get('data', button.get('link', button.get('text', ''))),
                    'unsupport_tips': button.get('tips', '.'),
                    'permission': {'type': 2}
                }
            }
            
            # 按钮附加属性
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