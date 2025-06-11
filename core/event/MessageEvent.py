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
from io import BytesIO
import os

# ===== 2. 第三方库导入 =====
import requests

# ===== 3. 自定义模块导入 =====
from function.Access import BOT凭证, BOTAPI, Json, Json取
from function.database import Database
from config import USE_MARKDOWN, IMAGE_BED, ENABLE_NEW_USER_WELCOME, ENABLE_WELCOME_MESSAGE
from function.log_db import add_log_to_db
from core.plugin.message_templates import MessageTemplate, MSG_TYPE_WELCOME, MSG_TYPE_USER_WELCOME, MSG_TYPE_API_ERROR

# ===== 4. 可选模块导入（带异常处理）=====
try:
    from web_panel.app import add_error_log
except ImportError:
    def add_error_log(log, traceback_info=None):
        pass

# ===== 5. 全局变量 =====
_image_upload_counter = 0  # 图床上传计数器
_image_upload_msgid = 0    # 图床消息ID计数器

# ===== 6. 主类定义 =====
class MessageEvent:
    """
    消息事件主类，负责解析和处理来自不同平台（如群聊、私聊、频道等）的消息事件，
    并提供自动回复、消息撤回、媒体上传、数据库记录、日志记录等一系列功能。
    该类是机器人消息处理的核心，支持多种消息类型和多平台兼容。
    """

    # --- 常量定义 ---
    GROUP_MESSAGE = 'GROUP_AT_MESSAGE_CREATE'      # 群消息类型
    DIRECT_MESSAGE = 'C2C_MESSAGE_CREATE'          # 私聊消息类型
    INTERACTION = 'INTERACTION_CREATE'             # 按钮交互消息类型
    CHANNEL_MESSAGE = 'AT_MESSAGE_CREATE'          # 频道消息类型
    GROUP_ADD_ROBOT = 'GROUP_ADD_ROBOT'            # 被拉进群事件类型
    UNKNOWN_MESSAGE = 'UNKNOWN'                    # 未知类型

    # 消息类型与解析方法的映射表，根据消息类型自动调用对应的解析方法
    _MESSAGE_TYPE_PARSERS = {
        GROUP_MESSAGE: '_parse_group_message',
        DIRECT_MESSAGE: '_parse_direct_message',
        INTERACTION: '_parse_interaction',
        CHANNEL_MESSAGE: '_parse_channel_message',
        GROUP_ADD_ROBOT: '_parse_group_add_robot',
    }

    # 不同消息类型对应的API接口路径模板，自动适配消息发送和撤回
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

    def __init__(self, data):
        """
        构造函数，初始化消息事件对象，自动解析消息内容、记录用户和群组信息。
        :param data: 原始消息数据（字典）
        """
        self.is_private = False  # 是否为私聊
        self.is_group = False    # 是否为群聊
        self.raw_data = data     # 原始消息数据
        self.user_id = None     # 用户ID
        self.group_id = None    # 群ID
        self.content = ""       # 消息内容
        self.message_type = self.UNKNOWN_MESSAGE  # 消息类型
        self.event_type = self.get('t')           # 事件类型
        self.message_id = self.get('d/id')        # 消息ID
        self.timestamp = self.get('d/timestamp')  # 时间戳
        self.matches = None                       # 匹配内容
        self.db = Database()                      # 数据库实例
        self.ignore = False                       # 是否忽略此消息
        self._parse_message()                     # 解析消息内容
        # 如果消息被标记为忽略，则不进行后续处理
        if not self.ignore:
            self._record_user_and_group()         # 记录用户和群组信息

    # --- 消息解析相关 ---
    def _parse_message(self):
        """
        根据消息类型分发到对应的解析方法，提取关键信息。
        未知类型会记录错误日志。
        """
        if self.event_type in self._MESSAGE_TYPE_PARSERS:
            parse_method = getattr(self, self._MESSAGE_TYPE_PARSERS[self.event_type])
            parse_method()
            # 如果已设置忽略标志，则直接返回，不执行后续操作
            if self.ignore:
                return
        else:
            # 记录未知类型及原始消息到日志
            raw_data_str = json.dumps(self.raw_data, ensure_ascii=False, indent=2) if isinstance(self.raw_data, dict) else str(self.raw_data)
            self._log_error(f"未知消息类型: event_type={self.event_type}", f'原始消息数据: {raw_data_str}')
            self.message_type = self.UNKNOWN_MESSAGE
            self.content = ""
            self.user_id = None
            self.group_id = None
            self.channel_id = None
            self.guild_id = None
            self.is_group = False
            self.is_private = False

    def _parse_group_message(self):
        """
        解析群消息，提取消息内容、用户ID、群ID等。
        """
        self.message_type = self.GROUP_MESSAGE
        self.content = self.sanitize_content(self.get('d/content'))
        self.user_id = self.get('d/author/id')
        self.group_id = self.get('d/group_id')
        self.channel_id = None
        self.guild_id = None
        self.is_group = True
        self.is_private = False

    def _parse_direct_message(self):
        """
        解析私聊消息，提取消息内容、用户ID。
        """
        self.message_type = self.DIRECT_MESSAGE
        self.content = self.sanitize_content(self.get('d/content'))
        self.user_id = self.get('d/author/id')
        self.group_id = None
        self.channel_id = None
        self.guild_id = None
        self.is_group = False
        self.is_private = True

    def _parse_interaction(self):
        """
        解析按钮交互消息，区分群聊/私聊场景，提取按钮数据。
        如果type=13则设置忽略标志并直接返回，不执行任何操作。
        """
        # 对于type=13的交互消息，设置忽略标志，不记录日志，不做任何操作
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
        self.channel_id = None
        self.guild_id = None
        button_data = self.get('d/data/resolved/button_data')
        self.content = self.sanitize_content(button_data) if button_data else ""
        self.id = self.get('id')

    def _parse_channel_message(self):
        """
        解析频道消息，处理@机器人前缀，提取消息内容、用户ID、频道ID等。
        """
        self.message_type = self.CHANNEL_MESSAGE
        raw_content = self.get('d/content')
        mention_prefix = ''
        bot_id = None
        mentions = self.get('d/mentions')
        if isinstance(mentions, list) and len(mentions) > 0:
            bot_id = mentions[0].get('id')
        if bot_id:
            mention_prefix = f'<@!{bot_id}>'
            if raw_content.startswith(mention_prefix):
                raw_content = raw_content[len(mention_prefix):].lstrip()
            else:
                mention_prefix2 = f'<@{bot_id}>'
                if raw_content.startswith(mention_prefix2):
                    raw_content = raw_content[len(mention_prefix2):].lstrip()
        self.content = self.sanitize_content(raw_content.strip())
        self.user_id = self.get('d/author/id')
        self.group_id = None
        self.channel_id = self.get('d/channel_id')
        self.guild_id = self.get('d/guild_id')
        self.is_group = False
        self.is_private = False

    def _parse_group_add_robot(self):
        """
        解析被拉进群事件，提取邀请人ID、群ID。
        并直接处理欢迎消息，不传递给插件处理。
        """
        self.message_type = self.GROUP_ADD_ROBOT
        self.content = ""
        self.user_id = self.get('d/op_member_openid')
        self.group_id = self.get('d/group_openid')
        self.channel_id = None
        self.guild_id = None
        self.is_group = True
        self.is_private = False
        self.timestamp = self.get('d/timestamp')
        self.id = self.get('id')
        
        # 直接处理欢迎消息，不传递给插件
        self.msg_type = 'group_welcome_robot'  # 设置特殊标记
        
        # 如果启用了欢迎消息，直接发送
        if ENABLE_WELCOME_MESSAGE:
            try:
                # 使用消息模板系统发送欢迎消息
                MessageTemplate.send(self, MSG_TYPE_WELCOME)
                # 标记该消息已处理，避免传递到插件系统
                self.handled = True
            except Exception as e:
                self._log_error(f"发送群欢迎消息失败: {str(e)}", traceback.format_exc())
        else:
            # 即使不发送欢迎消息，也标记为已处理，避免传递到插件系统
            self.handled = True

    # --- API交互相关 ---
    def reply(self, content='', buttons=None, media=None):
        """
        根据消息类型自动回复消息，支持文本、按钮、媒体等多种消息格式。
        :param content: 回复内容
        :param buttons: 按钮对象
        :param media: 媒体文件（如语音、图片）
        :return: 消息ID或错误提示
        """
        # 如果消息被标记为忽略，直接返回而不执行任何操作
        if self.ignore or getattr(self, 'handled', False):
            return None
            
        if self.message_type not in self._API_ENDPOINTS:
            # 记录详细错误日志，包含原始消息内容
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
                    media_payload = { 'type': 3, 'file_info': file_info }
            elif isinstance(media, list):
                media_payload = media[0] if media else None
        payload = self._build_message_payload(content, buttons, media_payload)
        if self.message_type == self.INTERACTION and self.is_private:
            endpoint_template = self._API_ENDPOINTS[self.message_type]['reply_private']
        else:
            endpoint_template = self._API_ENDPOINTS[self.message_type]['reply']
        endpoint = self._fill_endpoint_template(endpoint_template)
        if self.message_type == self.GROUP_ADD_ROBOT:
            if 'event_id' not in payload or not payload['event_id']:
                payload['event_id'] = self.get('id') or f"ROBOT_ADD_{int(time.time())}"
        response = BOTAPI(endpoint, "POST", Json(payload))
        try:
            import json as _json
            resp_obj = None
            if isinstance(response, str):
                try:
                    resp_obj = _json.loads(response)
                except Exception:
                    resp_obj = None
            elif isinstance(response, dict):
                resp_obj = response
            if resp_obj and all(k in resp_obj for k in ("message", "code", "trace_id")):
                error_code = resp_obj.get('code')
                
                # 特定错误码处理：被移出群聊或被禁言，直接忽略错误
                if error_code in [11293, 40054002, 40054003]:
                    # 这些错误不记录日志，不发送错误消息
                    return None
                # 记录错误日志（除了特定错误码外）
                self._log_error(f"消息发送失败：{resp_obj.get('message')} code：{error_code} trace_id：{resp_obj.get('trace_id')}", f"resp_obj: {str(resp_obj)}\nsend_payload: {json.dumps(payload, ensure_ascii=False)}\nraw_message: {json.dumps(self.raw_data, ensure_ascii=False, indent=2) if isinstance(self.raw_data, dict) else str(self.raw_data)}")
                
                # 使用模板系统处理API错误
                return MessageTemplate.send(self, MSG_TYPE_API_ERROR, error_code=error_code, 
                                          trace_id=resp_obj.get('trace_id'), endpoint=endpoint)
        except Exception:
            pass
        return self._extract_message_id(response)

    def recall_message(self, message_id):
        """
        撤回指定消息，自动选择正确的API端点。
        :param message_id: 要撤回的消息ID
        :return: 撤回结果
        """
        # 首先检查message_id是否为None
        if message_id is None:
            return None
            
        if self.message_type not in self._API_ENDPOINTS:
            return None
        try:
            if self.message_type == self.INTERACTION and self.is_private:
                endpoint_template = self._API_ENDPOINTS[self.message_type]['recall_private']
            elif 'recall' in self._API_ENDPOINTS[self.message_type]:
                endpoint_template = self._API_ENDPOINTS[self.message_type]['recall']
            else:
                return None
            endpoint = self._fill_endpoint_template(endpoint_template)
            # 确保message_id是字符串类型
            endpoint = endpoint.replace('{message_id}', str(message_id))
            response = BOTAPI(endpoint, "DELETE", None)
            return response
        except Exception as e:
            self._log_error(f"撤回消息时发生错误: {str(e)}")
            return None

    def _build_message_payload(self, content, buttons, media):
        """
        构建消息发送的payload，根据内容、按钮、媒体自动适配格式。
        """
        msg_type = 7 if media else (2 if USE_MARKDOWN else 0)
        payload = {
            "msg_type": msg_type,
            "msg_seq": random.randint(10000, 999999)
        }
        if self.message_type == self.GROUP_MESSAGE or self.message_type == self.DIRECT_MESSAGE:
            payload["msg_id"] = self.message_id
        elif self.message_type == self.INTERACTION or self.message_type == self.GROUP_ADD_ROBOT:
            payload["event_id"] = self.get('id') or self.get('d/id') or ""
        elif self.message_type == self.CHANNEL_MESSAGE:
            payload["msg_id"] = self.get('d/id')
        if media:
            payload['content'] = ''
        else:
            if content:
                if USE_MARKDOWN:
                    payload['markdown'] = {'content': content}
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

    def _fill_endpoint_template(self, template):
        """
        填充API端点模板中的参数（如group_id、user_id、channel_id）。
        """
        if '{group_id}' in template and self.group_id:
            template = template.replace('{group_id}', self.group_id)
        if '{user_id}' in template and self.user_id:
            template = template.replace('{user_id}', self.user_id)
        if '{channel_id}' in template and self.channel_id:
            template = template.replace('{channel_id}', self.channel_id)
        return template

    def _extract_message_id(self, response):
        """
        从API响应中提取消息ID。
        """
        msg_id = None
        if response:
            try:
                if isinstance(response, str):
                    response_data = json.loads(response)
                    msg_id = response_data.get('id') or response_data.get('msg_id') or response_data.get('message_id')
                elif isinstance(response, dict):
                    msg_id = response.get('id') or response.get('msg_id') or response.get('message_id')
            except Exception:
                pass
        return msg_id if msg_id else response

    # --- 媒体上传相关 ---
    def upload_media(self, file_bytes, file_type):
        """
        上传媒体文件到群或私聊，返回file_info。
        :param file_bytes: 文件字节流
        :param file_type: 文件类型（如3为语音）
        :return: file_info对象
        """
        if self.is_group:
            endpoint = f"/v2/groups/{self.group_id}/files"
        else:
            endpoint = f"/v2/users/{self.user_id}/files"
        req_data = {
            "srv_send_msg": False,
            "file_type": file_type,
            "file_data": base64.b64encode(file_bytes).decode()
        }
        resp = BOTAPI(endpoint, "POST", Json(req_data))
        if isinstance(resp, str):
            resp = json.loads(resp)
        return resp.get('file_info')

    def uploadToQQImageBed(self, image_data, type=None):
        """
        通用上传图片到图床，type=qqshare或qqbot，自动选择已配置的图床。
        :param image_data: 图片字节流
        :param type: 图床类型
        :return: 图片URL
        """
        if type is None:
            has_qqbot_config = bool(IMAGE_BED.get('qq_bot', {}).get('channel_id'))
            has_qqshare_config = bool(IMAGE_BED.get('qq_share', {}).get('p_uin')) and bool(IMAGE_BED.get('qq_share', {}).get('p_skey'))
            if has_qqbot_config:
                type = 'qqbot'
            elif has_qqshare_config:
                type = 'qqshare'
            else:
                print("错误: 未检测到任何可用的图床配置，无法上传图片")
                return ''
        if type == 'qqbot':
            return self.uploadToQQBotImageBed(image_data)
        else:
            return self.uploadToQQShareImageBed(image_data)

    def uploadToQQBotImageBed(self, image_data):
        """
        使用QQ机器人官方图床上传图片，返回图片URL。
        :param image_data: 图片字节流
        :return: 图片URL
        """
        global _image_upload_counter, _image_upload_msgid
        access_token = BOT凭证() or ''
        if not access_token:
            return ''
        channel = IMAGE_BED.get('qq_bot', {}).get('channel_id')
        appid = IMAGE_BED.get('qq_bot', {}).get('appid', '')
        if not channel:
            return ''
        md5hash = hashlib.md5(image_data).hexdigest().upper()
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
        files = {
            'file_image': (filename, open(temp_path, 'rb'), mime_type)
        }
        data = {
            'msg_id': str(_image_upload_msgid)
        }
        headers = {
            'Authorization': f'QQBot {access_token}'
        }
        if appid:
            headers['X-Union-Appid'] = appid
        try:
            response = requests.post(
                f'https://api.sgroup.qq.com/channels/{channel}/messages',
                files=files,
                data=data,
                headers=headers,
                verify=False
            )
        except Exception:
            pass
        try:
            os.unlink(temp_path)
        except:
            pass
        image_url = f'https://gchat.qpic.cn/qmeetpic/0/0-0-{md5hash}/0'
        return image_url

    def uploadToQQShareImageBed(self, image_data):
        """
        使用QQShare方式上传图片，返回图片URL。
        :param image_data: 图片字节流
        :return: 图片URL
        """
        p_uin = IMAGE_BED.get('qq_share', {}).get('p_uin', '')
        p_skey = IMAGE_BED.get('qq_share', {}).get('p_skey', '')
        if not p_uin or not p_skey:
            print("错误: 未配置QQShare图床必要参数(p_uin或p_skey)，无法上传图片")
            return ''
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(image_data)
            temp_path = f.name
        filename = f'upload_{int(random.random() * 10000)}.png'
        files = {
            'share_image': (filename, open(temp_path, 'rb'), 'image/png')
        }
        cookies = {
            'p_uin': p_uin,
            'p_skey': p_skey
        }
        headers = {
            'User-Agent': 'android_34_OP5D06L1_14_9.1.5',
            'Referer': 'http://www.qq.com',
            'Host': 'cgi.connect.qq.com',
            'Accept-Encoding': 'gzip'
        }
        try:
            response = requests.post(
                'https://cgi.connect.qq.com/qqconnectopen/upload_share_image',
                files=files,
                cookies=cookies,
                headers=headers
            )
            os.unlink(temp_path)
            if response.status_code == 200:
                try:
                    result = response.json()
                    if 'url' in result:
                        return result['url']
                except:
                    pass
        except Exception as e:
            print(f"上传图片失败: {str(e)}")
        return ""

    # --- 数据库与日志 ---
    def _record_user_and_group(self):
        """
        记录用户和群组信息到数据库，并在新用户进群时发送欢迎消息。
        """
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
        if user_is_new and self.is_group and ENABLE_NEW_USER_WELCOME:
            try:
                user_count = self.db.get_user_count()
                # 使用消息模板系统发送新用户欢迎
                MessageTemplate.send(self, MSG_TYPE_USER_WELCOME, user_count=user_count)
            except Exception as e:
                self._log_error(f'新用户欢迎消息发送失败: {str(e)}')

    def _log_error(self, msg, tb=None):
        """
        统一的错误日志和异常处理方法，便于追踪和前端展示。
        :param msg: 错误内容
        :param tb: 堆栈信息
        """
        log_data = {
            'timestamp': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'content': msg,
            'traceback': tb or traceback.format_exc()
        }
        add_log_to_db('error', log_data)
        add_error_log(msg, tb or traceback.format_exc())

    # --- 工具方法 ---
    def get(self, path):
        """
        从原始数据中提取指定路径的字段。
        :param path: 路径字符串
        :return: 字段值
        """
        return Json取(self.raw_data, path)

    def sanitize_content(self, content):
        """
        清理内容，去除前缀斜杠、前后空格和表情代码。
        :param content: 原始内容
        :return: 清理后的内容
        """
        if not content:
            return ""
        content = str(content)
        if content.startswith('/'):
            content = content[1:]
        import re
        content = re.sub(r'<faceType=\d+,faceId="[^"]+",ext="[^"]+">', '', content)
        
        return content.strip()

    def rows(self, buttons):
        """
        创建按钮行，支持多种按钮类型和权限设置。
        :param buttons: 按钮列表
        :return: 按钮行对象
        """
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
        """
        创建键盘对象，包含多行按钮。
        :param rows: 按钮行列表
        :return: 键盘对象
        """
        rows = rows or []
        return {
            'content': {
                'rows': rows
            }
        } 