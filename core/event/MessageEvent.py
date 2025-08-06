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
from config import USE_MARKDOWN, IMAGE_BED_CHANNEL_ID, ENABLE_NEW_USER_WELCOME, ENABLE_WELCOME_MESSAGE, HIDE_AVATAR_GLOBAL
from function.log_db import add_log_to_db
from core.plugin.message_templates import MessageTemplate, MSG_TYPE_WELCOME, MSG_TYPE_USER_WELCOME, MSG_TYPE_API_ERROR
from function.httpx_pool import sync_post, get_binary_content

# ===== 4. 可选模块导入（带异常处理）=====
try:
    from web.app import add_error_log
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
    def _check_send_conditions(self):
        """检查发送条件"""
        return not (self.ignore or (getattr(self, 'handled', False) and not getattr(self, 'welcome_allowed', False)))
    
    def _prepare_media_data(self, data):
        """准备媒体数据（图片、语音、视频）"""
        if isinstance(data, str):
            try:
                return get_binary_content(data)
            except Exception:
                import requests
                return requests.get(data).content
        return data
    
    def _set_message_id_in_payload(self, payload):
        """为payload设置消息ID"""
        if self.message_type in (self.GROUP_MESSAGE, self.DIRECT_MESSAGE):
            payload["msg_id"] = self.message_id
        elif self.message_type in (self.INTERACTION, self.GROUP_ADD_ROBOT):
            payload["event_id"] = self.get('id') or self.get('d/id') or ""
        elif self.message_type == self.CHANNEL_MESSAGE:
            payload["msg_id"] = self.get('d/id')
        return payload
    
    def _handle_auto_recall(self, message_id, auto_delete_time):
        """处理自动撤回"""
        if message_id and auto_delete_time:
            import threading
            threading.Timer(auto_delete_time, self.recall_message, args=[message_id]).start()
    
    def _send_media_message(self, data, content, file_type, content_type, auto_delete_time=None, converter=None):
        """通用媒体消息发送方法"""
        if not self._check_send_conditions():
            return None
            
        # 准备媒体数据
        processed_data = self._prepare_media_data(data)
        
        # 如果有转换器（如语音转silk），应用转换
        if converter:
            processed_data = converter(processed_data)
            if not processed_data:
                return None
        
        # 上传媒体
        file_info = self.upload_media(processed_data, file_type)
        if not file_info:
            return None
            
        # 构建payload
        payload = self._build_media_message_payload(content, file_info)
        endpoint = self._get_endpoint()
        
        if self.message_type == self.GROUP_ADD_ROBOT:
            payload['event_id'] = self.get('id') or f"ROBOT_ADD_{int(time.time())}"
        
        # 发送消息
        message_id = self._send_with_error_handling(payload, endpoint, content_type, f"content: {content}")
        
        # 处理自动撤回
        self._handle_auto_recall(message_id, auto_delete_time)
        
        return message_id

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
        
        message_id = self._send_with_error_handling(payload, endpoint, "消息", f"content: {content}")
        
        # 如果设置了自动撤回时间，启动定时器
        if message_id and auto_delete_time and isinstance(auto_delete_time, (int, float)) and auto_delete_time > 0:
            import threading
            timer = threading.Timer(auto_delete_time, self.recall_message, args=[message_id])
            timer.daemon = True
            timer.start()
            
        return message_id

    def reply_image(self, image_data, content='', auto_delete_time=None):
        """发送图片消息"""
        return self._send_media_message(image_data, content, 1, "图片消息", auto_delete_time)

    def reply_voice(self, voice_data, content='', auto_delete_time=None):
        """发送语音消息"""
        return self._send_media_message(voice_data, content, 3, "语音消息", auto_delete_time, self._convert_to_silk)

    def reply_video(self, video_data, content='', auto_delete_time=None):
        """发送视频消息"""
        return self._send_media_message(video_data, content, 2, "视频消息", auto_delete_time)

    def reply_ark(self, template_id, kv_data, content='', auto_delete_time=None):
        """发送ark卡片消息"""
        return self._send_simple_message(
            lambda: self._build_ark_message_payload(template_id, self._convert_simple_ark_data(template_id, kv_data) if isinstance(kv_data, (tuple, list)) and template_id in [23, 24, 37] else kv_data, content),
            "ark卡片消息",
            auto_delete_time
        )

    def reply_markdown(self, template, params=None, keyboard_id=None, auto_delete_time=None):
        """发送markdown模板消息
        
        Args:
            template: 模板名称或模板ID
            params: 参数列表/元组，按模板参数顺序传入
            keyboard_id: 按钮模板ID（官方申请获得）
            auto_delete_time: 自动撤回时间（秒）
            
        Returns:
            消息ID或None
        """
        def build_payload():
            template_data = self._build_markdown_template_data(template, params)
            if not template_data:
                return None
            return self._build_markdown_message_payload(template_data, keyboard_id)
        
        if not self._check_send_conditions():
            return None
            
        payload = build_payload()
        if not payload:
            return None
            
        endpoint = self._get_endpoint()
        
        if self.message_type == self.GROUP_ADD_ROBOT:
            payload['event_id'] = self.get('id') or f"ROBOT_ADD_{int(time.time())}"
        
        message_id = self._send_with_error_handling(payload, endpoint, "markdown模板消息", f"template: {template}, params: {params}")
        
        self._handle_auto_recall(message_id, auto_delete_time)
        
        return message_id

    def reply_md(self, template, params=None, keyboard_id=None, auto_delete_time=None):
        """reply_markdown的简化别名"""
        return self.reply_markdown(template, params, keyboard_id, auto_delete_time)

    def _get_endpoint(self):
        """获取API端点"""
        endpoint_template = self._API_ENDPOINTS[self.message_type]['reply_private'] if (self.message_type == self.INTERACTION and self.is_private) else self._API_ENDPOINTS[self.message_type]['reply']
        return self._fill_endpoint_template(endpoint_template)

    def _cleanup_temp_files(self, *file_paths):
        """清理临时文件"""
        for path in file_paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def _convert_to_silk(self, audio_data):
        """将音频数据转换为silk格式 - 使用ffmpeg+pilk"""
        import tempfile
        import subprocess
        
        audio_path = None
        pcm_path = None
        silk_path = None
        
        try:
            # 创建临时音频文件
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as f:
                audio_path = f.name
                f.write(audio_data)
            
            # 用ffmpeg转换为pcm（48k采样率，单声道）
            pcm_path = audio_path + '.pcm'
            ffmpeg_cmd = [
                'ffmpeg', '-y', '-i', audio_path,
                '-ar', '48000', '-ac', '1', '-f', 's16le', 
                '-loglevel', 'error', 
                '-hide_banner',  
                pcm_path
            ]
            
            subprocess.run(ffmpeg_cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # pcm转silk
            import pilk
            silk_path = audio_path + '.silk'
            pilk.encode(pcm_path, silk_path, pcm_rate=48000, tencent=True)
            
            # 读取silk数据
            with open(silk_path, 'rb') as f:
                silk_data = f.read()
                
            return silk_data
                
        except Exception:
            return None
        finally:
            # 清理临时文件
            self._cleanup_temp_files(audio_path, pcm_path, silk_path)

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

    def _send_with_error_handling(self, payload, endpoint, content_type="消息", extra_info=""):
        """通用的发送错误处理方法"""
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
                            f"发送{content_type}Token过期重试2次后仍然失败",
                            f"{extra_info}\npayload: {json.dumps(payload, ensure_ascii=False)}\nraw_message: {json.dumps(self.raw_data, ensure_ascii=False, indent=2) if isinstance(self.raw_data, dict) else str(self.raw_data)}"
                        )
                        return None
                
                # 其他错误码处理
                self._log_error(
                    f"发送{content_type}失败：{resp_obj.get('message')} code：{error_code} trace_id：{resp_obj.get('trace_id')}", 
                    f"resp_obj: {str(resp_obj)}\nsend_payload: {json.dumps(payload, ensure_ascii=False)}\nraw_message: {json.dumps(self.raw_data, ensure_ascii=False, indent=2) if isinstance(self.raw_data, dict) else str(self.raw_data)}"
                )
                return MessageTemplate.send(self, MSG_TYPE_API_ERROR, error_code=error_code, 
                                           trace_id=resp_obj.get('trace_id'), endpoint=endpoint)
            
            # 没有错误则返回消息ID
            return self._extract_message_id(response)
        
        return None

    def _send_simple_message(self, payload_builder, content_type, auto_delete_time=None, **kwargs):
        """通用的简单消息发送方法"""
        if not self._check_send_conditions():
            return None
            
        payload = payload_builder(**kwargs)
        endpoint = self._get_endpoint()
        
        if self.message_type == self.GROUP_ADD_ROBOT:
            payload['event_id'] = self.get('id') or f"ROBOT_ADD_{int(time.time())}"
        
        message_id = self._send_with_error_handling(payload, endpoint, content_type, f"payload: {json.dumps(payload, ensure_ascii=False)}")
        
        self._handle_auto_recall(message_id, auto_delete_time)
        
        return message_id

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

    def _build_media_message_payload(self, content, file_info):
        payload = {
            "msg_type": 7,
            "msg_seq": random.randint(10000, 999999),
            "content": content or '',
            "media": {'file_info': file_info}
        }
        
        return self._set_message_id_in_payload(payload)

    def _build_ark_message_payload(self, template_id, kv_data, content):
        """构建ark消息payload"""
        payload = {
            "msg_type": 3,  # 3表示ark消息
            "msg_seq": random.randint(10000, 999999),
            "content": content or '',
            "ark": {
                "template_id": template_id,
                "kv": kv_data
            }
        }
        
        return self._set_message_id_in_payload(payload)

    def _convert_simple_ark_data(self, template_id, simple_data):
        """将简化的ark参数转换为标准格式"""
        if template_id == 24:
            # ark24: 7个固定参数
            keys = ["#DESC#", "#PROMPT#", "#TITLE#", "#METADESC#", "#IMG#", "#LINK#", "#SUBTITLE#"]
            kv_data = []
            for i, value in enumerate(simple_data):
                if i < len(keys) and value is not None:
                    kv_data.append({"key": keys[i], "value": str(value)})
            return kv_data
            
        elif template_id == 37:
            # ark37: 5个固定参数
            keys = ["#PROMPT#", "#METATITLE#", "#METASUBTITLE#", "#METACOVER#", "#METAURL#"]
            kv_data = []
            for i, value in enumerate(simple_data):
                if i < len(keys) and value is not None:
                    kv_data.append({"key": keys[i], "value": str(value)})
            return kv_data
            
        elif template_id == 23:
            # ark23: 2个固定参数 + 1个动态列表
            kv_data = []
            if len(simple_data) >= 1 and simple_data[0] is not None:
                kv_data.append({"key": "#DESC#", "value": str(simple_data[0])})
            if len(simple_data) >= 2 and simple_data[1] is not None:
                kv_data.append({"key": "#PROMPT#", "value": str(simple_data[1])})
            if len(simple_data) >= 3 and simple_data[2] is not None:
                # 转换简化的列表格式为标准格式
                obj_list = []
                for item in simple_data[2]:
                    if item and len(item) >= 1:  # 确保有至少1个参数（desc）
                        obj_kv = []
                        # 第1个参数自动对应desc
                        if item[0] and str(item[0]).strip():
                            obj_kv.append({"key": "desc", "value": str(item[0])})
                        # 第2个参数自动对应link（如果存在且不为空）
                        if len(item) >= 2 and item[1] and str(item[1]).strip():
                            obj_kv.append({"key": "link", "value": str(item[1])})
                        if obj_kv:
                            obj_list.append({"obj_kv": obj_kv})
                kv_data.append({"key": "#LIST#", "obj": obj_list})
            return kv_data
            
        return simple_data

    def _build_markdown_template_data(self, template, params):
        """构建markdown模板数据"""
        try:
            from core.event.markdown_templates import get_template, MARKDOWN_TEMPLATES
            
            template_config = None
            
            # 判断是模板名称还是模板ID
            if template in MARKDOWN_TEMPLATES:
                template_config = get_template(template)
            else:
                # 查找匹配的模板ID
                for name, config in MARKDOWN_TEMPLATES.items():
                    if config['id'] == template:
                        template_config = config
                        break
            
            if not template_config:
                self._log_error(f"未找到模板ID: {template}")
                return None
                
            template_id = template_config['id']
            template_params = template_config['params']
            
            # 构建参数列表
            param_list = []
            if params:
                for i, param_name in enumerate(template_params):
                    if i < len(params) and params[i] is not None:
                        param_list.append({
                            "key": param_name,
                            "values": [str(params[i])]
                        })
            
            return {
                "custom_template_id": template_id,
                "params": param_list
            }
            
        except Exception:
            return None

    def _build_markdown_message_payload(self, template_data, keyboard_id=None):
        """构建markdown消息payload"""
        payload = {
            "msg_type": 2,  # 2表示markdown消息
            "msg_seq": random.randint(10000, 999999),
            "markdown": {
                "custom_template_id": template_data["custom_template_id"]
            }
        }
        
        if template_data.get("params"):
            payload["markdown"]["params"] = template_data["params"]
        
        # 添加按钮模板ID
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
            data = {'msg_id': str(_image_upload_msgid)}
            headers = {'Authorization': f'QQBot {access_token}'}
                
            sync_post(
                f'https://api.sgroup.qq.com/channels/{IMAGE_BED_CHANNEL_ID}/messages',
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

    def get_image_size(self, image_input):
        """获取图片尺寸信息
        
        Args:
            image_input: 图片链接URL、本地文件路径或二进制数据
            
        Returns:
            dict: {'width': int, 'height': int, 'px': '#WIDTHpx #HEIGHTpx'} 或 None
        """
        try:
            from PIL import Image
            import io
            import os
            
            if isinstance(image_input, bytes):
                # 处理二进制数据
                with Image.open(io.BytesIO(image_input)) as img:
                    width, height = img.size
                    return {
                        'width': width,
                        'height': height, 
                        'px': f'#{width}px #{height}px'
                    }
                    
            elif isinstance(image_input, str):
                if image_input.startswith(('http://', 'https://')):
                    # 处理网络图片链接 - 只下载64KB数据
                    import requests
                    headers = {'Range': 'bytes=0-65535'}
                    response = requests.get(image_input, headers=headers, stream=True, timeout=10)
                    
                    if response.status_code in [206, 200]:  # 206=Partial Content, 200=Full Content
                        partial_data = response.content
                        with Image.open(io.BytesIO(partial_data)) as img:
                            width, height = img.size
                            return {
                                'width': width,
                                'height': height,
                                'px': f'#{width}px #{height}px'
                            }
                else:
                    # 处理本地文件路径
                    if os.path.exists(image_input):
                        with Image.open(image_input) as img:
                            width, height = img.size
                            return {
                                'width': width,
                                'height': height,
                                'px': f'#{width}px #{height}px'
                            }
            
            return None
            
        except Exception:
            return None 