#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import random
import tempfile
import hashlib
import requests
from io import BytesIO
from function.Access import BOT凭证, BOTAPI, Json, Json取
from function.database import Database
from config import USE_MARKDOWN, IMAGE_BED

class MessageEvent:
    def __init__(self, data):
        """初始化消息事件"""
        self.raw_data = data
        self.event_type = self.get('t')  # 事件类型，如GROUP_AT_MESSAGE_CREATE, INTERACTION_CREATE
        self.message_id = self.get('d/id')
        self.content = self.sanitize_content(self.get('d/content'))
        self.sender_id = self.get('d/author/id') or None
        self.timestamp = self.get('d/timestamp')

        # 动态提取群/频道信息
        self.group_id = self.get('d/group_id') or None
        
        # 处理交互事件的特殊字段
        if self.event_type == "INTERACTION_CREATE":
            # 获取交互事件特有的字段
            self.group_id = self.get('d/group_openid') or self.group_id
            self.user_id = self.get('d/group_member_openid') or self.get('d/author/id')
            
            # 如果有button_data，提取作为content
            button_data = self.get('d/data/resolved/button_data')
            if button_data:
                self.content = self.sanitize_content(button_data)
        else:
            self.user_id = self.get('d/author/id') or self.sender_id
            
        self.channel_id = self.get('d/channel_id') or None
        self.guild_id = self.get('d/guild_id') or None
        self.matches = None

        # 初始化数据库连接
        self.db = Database()
        
        # 记录用户和群组信息
        self._record_user_and_group()

    def _record_user_and_group(self):
        """记录用户和群组信息到数据库"""
        if self.user_id:
            self.db.add_user(self.user_id)
        
        if self.group_id:
            self.db.add_group(self.group_id)
            if self.user_id:
                self.db.add_user_to_group(self.group_id, self.user_id)

    def reply(self, content='', buttons=None, media=None):
        """回复消息"""
        buttons = buttons or []
        media = media or []
        
        # 根据配置决定消息类型
        msg_type = 2 if USE_MARKDOWN else 0
        
        payload = {
            "msg_type": msg_type,
            "msg_seq": random.randint(10000, 999999)
        }
        
        if content:
            if USE_MARKDOWN:
                payload['markdown'] = {'content': content}
            else:
                payload['content'] = content
            
        if buttons:
            payload['keyboard'] = buttons
            
        if media:
            payload['media'] = media
            
        response = None
        
        if self.event_type == "GROUP_AT_MESSAGE_CREATE":  # 群消息
            payload['msg_id'] = self.message_id
            response = BOTAPI(f"/v2/groups/{self.group_id}/messages", "POST", Json(payload))
        elif self.event_type == "INTERACTION_CREATE":  # 按钮消息
            # 使用PHP版本的逻辑：直接获取id字段而不是d/id
            payload['event_id'] = self.get('id')
            response = BOTAPI(f"/v2/groups/{self.group_id}/messages", "POST", Json(payload))
        elif self.event_type == "C2C_MESSAGE_CREATE":  # 私聊消息
            payload['msg_id'] = self.message_id
            response = BOTAPI(f"/v2/users/{self.user_id}/messages", "POST", Json(payload))
        
        return response

    def get(self, path):
        """获取原始数据中的字段"""
        return Json取(self.raw_data, path)

    def sanitize_content(self, content):
        """清理内容，去除前缀斜杠和前后空格"""
        if not content:
            return ""
        
        content = str(content)
        # 去除前缀斜杠和前后空白
        if content.startswith('/'):
            content = content[1:]
        return content.strip()

    def rows(self, buttons):
        """创建按钮行"""
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
            
            # 指令按钮特殊处理
            if button_type == 2:
                if 'enter' in button:
                    button_obj['action']['enter'] = True
                if 'reply' in button:
                    button_obj['action']['reply'] = True
            
            # 权限处理
            if 'admin' in button:
                button_obj['action']['permission']['type'] = 1
            if 'list' in button:
                button_obj['action']['permission']['type'] = 0
                button_obj['action']['permission']['specify_user_ids'] = button['list']
            if 'role' in button:
                button_obj['action']['permission']['type'] = 3
                button_obj['action']['permission']['specify_role_ids'] = button['role']
            
            # 点击次数限制
            if 'limit' in button:
                button_obj['action']['click_limit'] = button['limit']
            
            result.append(button_obj)
        
        return {'buttons': result}

    def button(self, rows=None):
        """创建键盘对象"""
        rows = rows or []
        return {
            'content': {
                'rows': rows
            }
        }

    def file_info(self, media, type):
        """获取文件信息"""
        if not isinstance(media, dict) or 'type' not in media or 'url' not in media:
            return None
        
        # 群组消息特殊处理
        if type == 'group':
            return {
                'type': media['type'],
                'url': media['url']
            }
        
        # 其他类型消息处理
        return {
            'type': media['type'],
            'url': media['url']
        }

    def uploadToQQImageBed(self, image_data, type=None):
        """通用上传图片到图床，type=qqshare或qqbot
        如果不指定type，将自动选择已配置的图床
        """
        # 自动选择图床类型
        if type is None:
            # 检查官方图床配置
            has_qqbot_config = bool(IMAGE_BED.get('qq_bot', {}).get('channel_id'))
            # 检查互联图床配置
            has_qqshare_config = bool(IMAGE_BED.get('qq_share', {}).get('p_uin')) and bool(IMAGE_BED.get('qq_share', {}).get('p_skey'))
            
            if has_qqbot_config:
                type = 'qqbot'  # 优先使用官方图床
            elif has_qqshare_config:
                type = 'qqshare'  # 其次使用互联图床
            else:
                print("错误: 未检测到任何可用的图床配置，无法上传图片")
                return ''
        
        # 根据选择的类型上传图片
        if type == 'qqbot':
            return self.uploadToQQBotImageBed(image_data)
        else:
            return self.uploadToQQShareImageBed(image_data)

    def uploadToQQBotImageBed(self, image_data):
        """QQ机器人官方图床方式"""
        access_token = BOT凭证() or ''
        # 从配置中获取频道ID
        channel = IMAGE_BED.get('qq_bot', {}).get('channel_id')
        
        # 检查channel_id是否已配置
        if not channel:
            print("错误: 未配置QQ机器人官方图床的频道ID(channel_id)，无法上传图片")
            return ''
            
        md5hash = hashlib.md5(image_data).hexdigest().upper()
        
        if not access_token:
            return ''
        
        # 创建临时文件
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(image_data)
            temp_path = f.name
        
        # 获取MIME类型
        import magic
        mime = magic.Magic(mime=True)
        mime_type = mime.from_buffer(image_data)
        filename = f'image.{mime_type.split("/")[1]}'
        
        # 上传请求
        files = {
            'file_image': (filename, open(temp_path, 'rb'), mime_type)
        }
        data = {
            'msg_id': str(random.randint(1000000, 9999999))
        }
        
        headers = {
            'Authorization': f'QQBot {access_token}'
        }
        
        requests.post(
            f'https://api.sgroup.qq.com/channels/{channel}/messages',
            files=files,
            data=data,
            headers=headers,
            verify=False
        )
        
        import os
        os.unlink(temp_path)
        
        return f'https://gchat.qpic.cn/qmeetpic/0/0-0-{md5hash}/0'

    def uploadToQQShareImageBed(self, image_data):
        """QQShare方式"""
        # 从配置中获取QQ号和p_skey
        p_uin = IMAGE_BED.get('qq_share', {}).get('p_uin', '')  # QQ号
        p_skey = IMAGE_BED.get('qq_share', {}).get('p_skey', '')  # p_skey值
        
        # 检查必要参数是否已配置
        if not p_uin or not p_skey:
            print("错误: 未配置QQShare图床必要参数(p_uin或p_skey)，无法上传图片")
            return ''
        
        # 创建临时文件
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
            
            import os
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