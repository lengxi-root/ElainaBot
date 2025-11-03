#!/usr/bin/env python
# -*- coding: utf-8 -*-

from core.plugin.PluginManager import Plugin
from function.cos_uploader import upload_image
from function.httpx_pool import sync_get, sync_post, get_json, get_binary_content
from function.db_pool import ConnectionManager, execute_query, execute_update, execute_transaction
import httpx
import json
import time
import threading

class media_plugin(Plugin):
    priority = 10 #（优先级，越高越先执行）
    
    @staticmethod
    def get_regex_handlers():
        return {
            # 基础媒体发送示例（无需特殊权限）
            r'^图片$': 'send_force_image',     # 使用reply_image方法
            r'^语音$': 'send_voice',              # 使用reply_voice方法
            r'^视频$': 'send_video',              # 使用reply_video方法
            r'^图片尺寸$': 'get_image_dimensions',  # 获取图片尺寸
            
            # 图床上传示例（无需特殊权限）
            r'^cos上传$': 'upload_to_cos',  # 上传图片到腾讯云COS
            r'^b站图床$': 'upload_to_bilibili',  # 上传图片到B站图床
            r'^qq频道图床$': 'upload_to_qq',  # 上传图片到QQ频道图床
            
            # 消息撤回示例（无需特殊权限）
            r'^撤回测试$': 'test_recall',  # 测试消息撤回
            r'^自动撤回$': 'test_auto_recall',  # 测试自动撤回
            
            # 数据库操作示例（无需特殊权限）
            r'^数据库测试$': 'test_database',  # 测试数据库操作
            r'^数据库连接池$': 'test_db_pool',  # 测试数据库连接池
            
            # 消息和调试信息（无需特殊权限）
            r'^消息信息$': 'get_message_info',  # 获取消息详细信息
            r'^原始数据$': 'get_raw_server_data',  # 获取原始服务器数据
            
            # HTTP连接池示例（无需特殊权限）
            r'^http测试$': 'test_http_pool',  # 测试HTTP连接池

            # dau为每日活跃用户数，具体进入QQ开发者平台查看
            # markdown模板被动权限要求金牌机器人（月均2000dau申请）
            # config中的USE_MARKDOWN=True为原生markdown，不再受模板显示，要求钻石机器人（月均10000dau申请）
            r'^md图片$': 'send_advanced_image',  # Markdown模板图片（需要markdown权限）
            r'^md模板$': 'send_markdown_template',  # Markdown模板（需要markdown权限）
            r'^按钮测试$': 'test_buttons',  # 消息按钮（需要按钮权限）
            # ark权限要求私域机器人或者公域银牌机器人（月均400dau申请）
            r'^ark23$': 'send_ark23',  # ARK列表卡片
            r'^ark24$': 'send_ark24',  # ARK信息卡片
            r'^ark37$': 'send_ark37',  # ARK通知卡片
        }
    #  请注意markdown需要被动
    @staticmethod
    def send_advanced_image(event):
        """markdown模板 raw发送方式"""
        image_url = "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"
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
        """发送普通图片示例"""
        image_url = "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png"
        
        event.reply_image(image_url, "这是使用reply_image方法发送的普通图片")  # 参数：图片URL或二进制数据, 文本内容

    @staticmethod
    def send_voice(event):
        """发送语音示例"""
        voice_url = "https://act-upload.mihoyo.com/sr-wiki/2025/06/03/160045374/420e9ac5c0c9d2b2c44b91f453b65061_2267222992827173477.wav"
        
        event.reply_voice(voice_url)  # 参数：语音文件URL或二进制数据，自动转换为silk格式

    @staticmethod
    def send_video(event):
        """发送视频示例"""
        video_url = "https://txmov2.a.kwimgs.com/upic/2023/08/21/20/BMjAyMzA4MjEyMDMxNDdfMTQ3MDA5Nzk3Nl8xMTEwNjMwNjAyNjhfMl8z_b_B45bad63ac156a3096f9e46dc4fed890e.mp4?clientCacheKey=3xupcv5he9bsg2c_b.mp4&tt=b&di=65ed8104&bp=14214"
        
        event.reply_video(video_url)  # 参数：视频文件URL或二进制数据

    @staticmethod
    def get_image_dimensions(event):
        """获取图片尺寸示例"""
        image_url = "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"
        size_info = event.get_image_size(image_url)
        
        if size_info:
            event.reply(f"📐 图片尺寸信息：\n📏 宽度：{size_info['width']}px\n📐 高度：{size_info['height']}px\n🎯 格式化：{size_info['px']}")
        else:
            event.reply("❌ 无法获取图片尺寸信息")

    @staticmethod
    def upload_to_cos(event):
        """上传图片到腾讯云COS示例"""
        image_url = "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png"
        
        try:
            image_data = get_binary_content(image_url)
            result = upload_image(image_data, "test_image.png", user_id=event.user_id, return_url_only=False)
            
            if result:
                event.reply(f"✅ 上传成功！\n📎 文件URL: {result['file_url']}\n📏 尺寸: {result.get('width', '未知')}x{result.get('height', '未知')}\n📦 COS Key: {result['cos_key']}")
            else:
                event.reply("❌ 上传失败，请检查COS配置")
        except Exception as e:
            event.reply(f"❌ 上传出错: {str(e)}")

    @staticmethod
    def upload_to_bilibili(event):
        """上传图片到B站图床示例"""
        image_url = "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png"
        
        try:
            image_data = get_binary_content(image_url)
            result = event.uploadToBilibiliImageBed(image_data)
            
            if result:
                event.reply(f"✅ 上传成功！\n📎 图片URL: {result}")
            else:
                event.reply("❌ 上传失败，请检查B站图床配置")
        except Exception as e:
            event.reply(f"❌ 上传出错: {str(e)}")

    @staticmethod
    def upload_to_qq(event):
        """上传图片到QQ频道图床示例"""
        image_url = "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png"
        
        try:
            image_data = get_binary_content(image_url)
            result = event.uploadToQQBotImageBed(image_data)
            
            if result:
                event.reply(f"✅ 上传成功！\n📎 图片URL: {result}")
            else:
                event.reply("❌ 上传失败，请检查QQ图床配置")
        except Exception as e:
            event.reply(f"❌ 上传出错: {str(e)}")

    @staticmethod
    def test_recall(event):
        """测试消息撤回功能"""
        message_id = event.reply("⏰ 这条消息将在3秒后被撤回...")
        
        if message_id:
            def recall_after_delay():
                time.sleep(3)
                event.recall_message(message_id)
            threading.Thread(target=recall_after_delay, daemon=True).start()

    @staticmethod
    def test_auto_recall(event):
        """测试自动撤回功能（使用 auto_delete_time 参数）"""
        event.reply("⏰ 这条消息将在5秒后自动撤回", auto_delete_time=5)
        event.reply_image(
            "https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png",
            "🖼️ 这张图片将在10秒后自动撤回",
            auto_delete_time=10
        )

    @staticmethod
    def test_buttons(event):
        """测试按钮功能"""
        buttons = event.rows([
            {'text': '✅ 确认', 'data': '确认操作', 'enter': True},
            {'text': '❌ 取消', 'data': '取消操作', 'style': 1}
        ])
        
        button_rows = event.button([buttons])
        event.reply("请选择操作：", buttons=button_rows)

    @staticmethod
    def test_database(event):
        """测试数据库操作（使用 event.db）"""
        user_count = event.db.get_user_count()
        group_count = event.db.get_group_count()
        
        info = f"📊 数据库统计信息：\n👥 用户总数：{user_count}\n🏠 群组总数：{group_count}"
        
        if event.is_group and event.group_id:
            member_count = event.db.get_group_member_count(event.group_id)
            info += f"\n👨‍👩‍👧‍👦 本群成员数：{member_count}"
        
        event.reply(info)

    @staticmethod
    def test_db_pool(event):
        """测试数据库连接池（直接使用连接池操作）"""
        try:
            # 方式1：使用 ConnectionManager 上下文管理器
            with ConnectionManager() as manager:
                manager.execute("SELECT VERSION() as version")
                result = manager.fetchone()
                version = result.get('version', '未知') if result else '未知'
            
            # 方式2：使用快捷函数 execute_query
            user_count_result = execute_query("SELECT COUNT(*) as count FROM M_users")
            user_count = user_count_result.get('count', 0) if user_count_result else 0
            
            # 方式3：使用事务
            operations = [
                {'sql': "SELECT 1 as test", 'params': None}
            ]
            transaction_success = execute_transaction(operations)
            
            event.reply(f"💾 数据库连接池测试：\n🔢 MySQL版本：{version}\n👥 用户总数：{user_count}\n✅ 事务测试：{'成功' if transaction_success else '失败'}")
        except Exception as e:
            event.reply(f"❌ 数据库连接池测试失败：{str(e)}")

    @staticmethod
    def test_http_pool(event):
        """测试HTTP连接池"""
        try:
            # 方式1：使用 sync_get 获取响应
            response = sync_get("https://api.gitee.com/zen")
            zen_text = response.text
            
            # 方式2：使用 get_json 直接获取JSON
            github_api = get_json("https://api.gitee.com")
            
            # 方式3：使用 get_binary_content 获取二进制内容
            image_data = get_binary_content("https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png")
            image_size = len(image_data) / 1024
            
            # 方式4：使用 sync_post 发送POST请求
            post_response = sync_post("https://httpbin.org/post", json={"test": "data"})
            
            event.reply(f"🌐 HTTP连接池测试：\n📝 GitHub Zen：{zen_text}\n📊 API响应：{len(github_api)} 个端点\n🖼️ 图片大小：{image_size:.2f}KB\n✅ POST测试：{post_response.status_code}")
        except Exception as e:
            event.reply(f"❌ HTTP连接池测试失败：{str(e)}")

    @staticmethod
    def send_markdown_template(event):
        """发送markdown模板示例"""
        image_url = "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"
        size_info = event.get_image_size(image_url)
        px = size_info['px'] if size_info else "#1200px #2133px"

        # 例如你的模板是{{.text}}![{{.size}}]({{.url}})![{{.size2}}]({{.url2}})![{{.size3}}]({{.url3}})![{{.size4}}]({{.url4}}){{.text2}}
        # 第一个值是你在markdown_templates中映射的id
        # 第二个括号内用,分割，第一个值对应着传入第一个参数，以此类推
        # 第三个参数是按钮模板ID，不传入则只发送单markdown模板

        event.reply_markdown("1", (    
            "✨ 这是文本1",           # text
            px,               # size  
            image_url,              # url
            px,               # size2
            image_url,              # url2
            px,               # size3
            image_url,              # url3
            px,               # size4
            image_url,              # url4
            "🎉 这是文本2"           # text2
        ),
        "102321943_1752737844"               # keyboard_id - 按钮模板ID
    )  # 参数：模板名称, (参数列表)


    @staticmethod
    def send_ark23(event):
        """发送ark23列表卡片示例"""
        list_items = [
            ['功能1: 图片发送'],                                            # 第1项：只有描述
            ['功能2: 语音发送'],                                            # 第2项：只有描述  
            ['功能3: 视频发送', 'https://i.elaina.vin/api/']                # 第3项：描述+链接，可以无限增加数组
        ]
        
        event.reply_ark(23, ("这是一个列表卡片示例", "ElainaBot卡片测试", list_items))  
        # 参数：arkid 23, (描述, 提示, [描述, 链接(可选)]，可以无限增加数组

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
        ))  # 参数：arkid 24, (描述, 提示, 标题, 元描述, 图片, 链接, 子标题）

    @staticmethod
    def send_ark37(event):
        """发送ark37通知卡片示例"""
        event.reply_ark(37, (
            "系统通知",
            "ElainaBot状态更新",
            "新功能上线通知",
            "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0",
            "https://i.elaina.vin/api/"
        ))  # 参数：arkid 37, (提示, 标题, 子标题, 封面, 链接）

    @staticmethod
    def get_message_info(event):
        """获取消息详细信息示例"""
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
        
        event.reply(info_text)  # 参数：获取当前消息的详细信息

    @staticmethod
    def get_raw_server_data(event):
        """获取原始服务器数据"""
        msg = "原始服务器数据\n\n"
        
        # 关键请求头
        msg += "关键请求头:\n"
        msg += f"X-Bot-Appid: {event.get_header('X-Bot-Appid', '(未找到)')}\n"
        msg += f"User-Agent: {event.get_header('User-Agent', '(未找到)')}\n"
        msg += f"X-Signature-Timestamp: {event.get_header('X-Signature-Timestamp', '(未找到)')}\n\n"
        
        # 完整请求头（过滤敏感字段）
        msg += "请求头:\n"
        if event.request_headers:
            safe = {}
            hide = ['host', 'x-host', 'x-real-ip', 'remote-host', 
                   'x-forwarded-for', 'x-forwarded-host', 'referer', 'origin', 'location']
            
            for k, v in event.request_headers.items():
                safe[k] = "(已隐藏)" if k.lower() in hide else v
            
            msg += json.dumps(safe, ensure_ascii=False, indent=2)
        else:
            msg += "(无请求头)"
        
        # 原始事件数据
        msg += "\n\n原始事件:\n"
        if event.raw_data:
            raw = event.raw_data if isinstance(event.raw_data, str) else json.dumps(event.raw_data, ensure_ascii=False, indent=2)
            msg += raw
        else:
            msg += "(无数据)"
        
        event.reply(msg)