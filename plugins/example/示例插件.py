#!/usr/bin/env python
# -*- coding: utf-8 -*-

from core.plugin.PluginManager import Plugin

class media_plugin(Plugin):
    priority = 10
    
    @staticmethod
    def get_regex_handlers():
        return {
            # 媒体发送示例
            r'^md图片$': 'send_advanced_image',      # Markdown模板图片
            r'^图片$': 'send_force_image',     # 使用reply_image方法
            r'^语音$': 'send_voice',              # 使用reply_voice方法
            r'^视频$': 'send_video',              # 使用reply_video方法
            # 图片尺寸获取示例
            r'^图片尺寸$': 'get_image_dimensions',  # 获取图片尺寸
            # markdown模板发送示例
            r'^md模板$': 'send_markdown_template',  # markdown模板 示例模板，你应该改为你自己的模板
            r'^md按钮$': 'send_markdown_with_button', # 带按钮的模板 同上
            # ark卡片发送示例
            r'^ark23$': 'send_ark23',             # 列表卡片
            r'^ark24$': 'send_ark24',             # 信息卡片
            r'^ark37$': 'send_ark37',             # 通知卡片
            # 消息信息获取示例
            r'^消息信息$': 'get_message_info'       # 获取消息详细信息
        }
    #  请注意markdown需要被动
    @staticmethod
    def send_advanced_image(event):
        """旧版markdown模板发送方式"""
        image_url = "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"
        
        # 方法1：原始复杂方式（兼容保留）
        template_id = "102321943_1747061997"
        
        params = [
            {
            "key": "px",
            "values": ["珊瑚宫心海 #1200px #2133px"]
        }, {
            "key": "url",
            "values": [image_url]
        }, {
            "key": "text",
            "values": ["\r\r>ElainaBot Markdown图片例子"]
        }]
        
        custom_template = {
            "custom_template_id": template_id,
            "params": params
        }
        
        event.reply(custom_template)  # 参数：自定义模板对象

    @staticmethod
    def send_force_image(event):
        """发送强制普通图片示例"""
        image_url = "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"
        
        event.reply_image(image_url, "这是使用reply_image方法发送的强制普通图片")  # 参数：图片URL或二进制数据, 文本内容

    @staticmethod
    def send_voice(event):
        """发送语音示例"""
        voice_url = "https://i.elaina.vin/api/tts/audio/audio_2695341589e953aa4ca3135f89848407.mp3"
        
        event.reply_voice(voice_url)  # 参数：语音文件URL或二进制数据，自动转换为silk格式

    @staticmethod
    def send_video(event):
        """发送视频示例"""
        video_url = "https://i.elaina.vin/1.mp4"
        
        event.reply_video(video_url)  # 参数：视频文件URL或二进制数据

    @staticmethod
    def get_image_dimensions(event):
        """获取图片尺寸示例"""
        image_url = "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"
        
        size_info = event.get_image_size(image_url)  # 参数：图片URL、本地路径或二进制数据
        
        if size_info:
            event.reply(f"""📐 图片尺寸信息：
📏 宽度：{size_info['width']}px
📐 高度：{size_info['height']}px  
🎯 格式化：{size_info['px']}
""")
        else:
            event.reply("❌ 无法获取图片尺寸信息")

    @staticmethod
    def send_markdown_template(event):
        """发送markdown模板示例"""
        image_url = "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"
        
        # 获取图片尺寸
        size_info = event.get_image_size(image_url)
        if size_info:
            px_value = size_info['px']  # "#1200px #2133px"
        else:
            px_value = "#1200px #2133px"  # 默认值
        
        # 新的markdown模板方法 1是在markdown_templates中映射的id
        event.reply_markdown("1", (    
            "✨ 这是文本1",           # text
            px_value,               # size  
            image_url,              # url
            px_value,               # size2
            image_url,              # url2
            px_value,               # size3
            image_url,              # url3
            px_value,               # size4
            image_url,              # url4
            "🎉 这是文本2"           # text2
        ))  # 参数：模板名称, (参数列表)

    @staticmethod
    def send_markdown_with_button(event):
        """发送带按钮的markdown模板示例"""
        image_url = "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"
        
        # 获取图片尺寸
        size_info = event.get_image_size(image_url)
        if size_info:
            px_value = size_info['px']  # "#1200px #2133px"
        else:
            px_value = "#1200px #2133px"  # 默认值
        
        # 使用带按钮的markdown模板
        event.reply_markdown(
            "1",                              # 模板名称
            (
                "✨ 这是带按钮的模板",               # text
                px_value,                        # size  
                image_url,                       # url
                px_value,                        # size2
                image_url,                       # url2
                px_value,                        # size3
                image_url,                       # url3
                px_value,                        # size4
                image_url,                       # url4
                "🎯 点击下方按钮互动"                 # text2
            ),
            "102321943_1752737844"               # keyboard_id - 按钮模板ID
        )  # 参数：模板名称, (参数列表), 按钮ID

    @staticmethod
    def send_ark23(event):
        """发送ark23列表卡片示例"""
        list_items = [
            ['功能1: 图片发送'],                                            # 第1项：只有描述
            ['功能2: 语音发送'],                                            # 第2项：只有描述  
            ['功能3: 视频发送', 'https://i.elaina.vin/api/']                # 第3项：描述+链接
        ]
        
        event.reply_ark(23, ("这是一个列表卡片示例", "ElainaBot卡片测试", list_items))  # 参数：描述, 提示, [描述, 链接(可选)]

    @staticmethod
    def send_ark24(event):
        """发送ark24信息卡片示例"""
        event.reply_ark(24, (
            "ElainaBot是一个功能强大的QQ机器人，支持多种媒体格式发送和丰富的功能模块。",
            "机器人信息",
            "ElainaBot - 智能QQ机器人",
            "基于Python开发的多功能QQ机器人，支持插件化开发，提供图片、语音、视频发送等功能。",
            "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0",
            "https://i.elaina.vin/api/",
            "Python QQ Bot"
        ))  # 参数：描述, 提示, 标题, 元描述, 图片, 链接, 子标题

    @staticmethod
    def send_ark37(event):
        """发送ark37通知卡片示例"""
        event.reply_ark(37, (
            "系统通知",
            "ElainaBot状态更新",
            "新功能上线通知",
            "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0",
            "https://i.elaina.vin/api/"
        ))  # 参数：提示, 标题, 子标题, 封面, 链接

    @staticmethod
    def get_message_info(event):
        """获取消息详细信息示例"""
        import json
        
        # 基础信息
        info_text = "📋 消息详细信息：\n\n"
        
        # 消息类型信息
        info_text += f"🔍 消息类型：{event.message_type}\n"
        info_text += f"📝 消息内容：{event.content}\n"
        info_text += f"🆔 消息ID：{event.message_id}\n"
        info_text += f"⏰ 时间戳：{event.timestamp}\n\n"
        
        # 用户和群组信息
        info_text += f"👤 发送用户ID：{event.user_id}\n"
        
        if event.is_group:
            info_text += f"👥 群聊ID：{event.group_id}\n"
            info_text += f"📱 聊天类型：群聊\n"
        elif event.is_private:
            info_text += f"📱 聊天类型：私聊\n"
        else:
            info_text += f"📱 聊天类型：未知\n"
        
        # 频道
        if hasattr(event, 'channel_id') and event.channel_id:
            info_text += f"📺 频道ID：{event.channel_id}\n"
        if hasattr(event, 'guild_id') and event.guild_id:
            info_text += f"🏰 子频道ID：{event.guild_id}\n"
        
        # 交互类型信息
        if event.message_type == event.INTERACTION:
            if hasattr(event, 'chat_type'):
                info_text += f"💬 交互聊天类型：{event.chat_type}\n"
            if hasattr(event, 'scene'):
                info_text += f"🎭 交互场景：{event.scene}\n"
        
        info_text += "\n📄 原始消息数据（前500字符）：\n"
        raw_data_str = json.dumps(event.raw_data, ensure_ascii=False, indent=2)
        if len(raw_data_str) > 500:
            info_text += raw_data_str[:500] + "..."
        else:
            info_text += raw_data_str
        
        event.reply(info_text)  # 参数：获取当前消息的详细信息

