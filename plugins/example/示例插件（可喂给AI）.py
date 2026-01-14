#!/usr/bin/env python
# -*- coding: utf-8 -*-

from core.plugin.PluginManager import Plugin
from function.cos_uploader import upload_image
from function.httpx_pool import sync_get, sync_post, get_json, get_binary_content
from function.db_pool import ConnectionManager, execute_query, execute_transaction
import json, time, threading, os, re

class media_plugin(Plugin):
    priority = 10
    counter_value = 0
    
    # ==================== Web面板示例 ====================
    @classmethod
    def get_web_routes(cls):
        return {'path': 'web-example', 'menu_name': 'Web示例', 'menu_icon': 'bi-star', 'handler': 'render_page', 'priority': 50,
                'api_routes': [{'path': '/api/web_example/counter', 'methods': ['GET', 'POST'], 'handler': 'api_counter', 'require_auth': True}]}
    
    @classmethod
    def api_counter(cls, data):
        if data.get('value') is not None:
            cls.counter_value = int(data.get('value', 0))
            return {'success': True, 'message': f'已保存: {cls.counter_value}'}
        return {'success': True, 'data': {'counter': cls.counter_value}}
    
    @staticmethod
    def render_page():
        return {'html': '''<div class="card"><div class="card-header bg-primary text-white"><h5 class="mb-0">插件Web面板示例</h5></div>
            <div class="card-body text-center"><h2 id="counter" class="text-primary my-4">0</h2>
            <button class="btn btn-primary" onclick="increment()">+1</button>
            <button class="btn btn-success" onclick="save()">保存</button>
            <button class="btn btn-secondary" onclick="load()">加载</button>
            <p class="mt-3 text-muted" id="status">点击加载获取服务器数据</p></div></div>''',
            'script': '''let counter=0;const token=new URLSearchParams(location.search).get('token')||'',api='/web/api/plugin/web_example/counter?token='+token;
            function increment(){counter++;document.getElementById('counter').textContent=counter}
            function load(){fetch(api).then(r=>r.json()).then(d=>{if(d.success){counter=d.data.counter;document.getElementById('counter').textContent=counter}document.getElementById('status').textContent=d.success?'已加载':'加载失败'})}
            function save(){fetch(api,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({value:counter})}).then(r=>r.json()).then(d=>document.getElementById('status').textContent=d.message||'保存失败')}load();'''}
    
    @staticmethod
    def get_regex_handlers():
        return {
            # ==================== 分享链接功能 ====================
            r'^申请邀请链接$': 'get_share_link',
            r'^申请邀请链接\s+(.+)$': {'handler': 'get_share_link_custom', 'owner_only': True},
            r'^查询邀请数量$': 'query_share_count',
            r'^查询我被谁邀请$': 'query_my_sharer',
            # ==================== 基础媒体发送示例（仅主人可用） ====================
            r'^图片$': {'handler': 'send_force_image', 'owner_only': True},
            r'^本地图片$': {'handler': 'send_local_image', 'owner_only': True},
            r'^语音$': {'handler': 'send_voice', 'owner_only': True},
            r'^视频$': {'handler': 'send_video', 'owner_only': True},
            r'^图片尺寸$': {'handler': 'get_image_dimensions', 'owner_only': True},
            # 图床上传示例（仅主人可用）
            r'^cos上传$': {'handler': 'upload_to_cos', 'owner_only': True},
            r'^b站图床$': {'handler': 'upload_to_bilibili', 'owner_only': True},
            r'^qq频道图床$': {'handler': 'upload_to_qq', 'owner_only': True},
            # 消息撤回示例（仅主人可用）
            r'^撤回测试$': {'handler': 'test_recall', 'owner_only': True},
            r'^自动撤回$': {'handler': 'test_auto_recall', 'owner_only': True},
            # 数据库操作示例（仅主人可用）
            r'^数据库测试$': {'handler': 'test_database', 'owner_only': True},
            r'^数据库连接池$': {'handler': 'test_db_pool', 'owner_only': True},
            # 消息和调试信息（仅主人可用）
            r'^消息信息$': {'handler': 'get_message_info', 'owner_only': True},
            r'^原始数据$': {'handler': 'get_raw_server_data', 'owner_only': True},
            # HTTP连接池示例（仅主人可用）
            r'^http测试$': {'handler': 'test_http_pool', 'owner_only': True},
            # markdown模板示例（仅主人可用）
            r'^md图片$': {'handler': 'send_advanced_image', 'owner_only': True},
            r'^md模板$': {'handler': 'send_markdown_template', 'owner_only': True},
            r'^aj模板$': {'handler': 'test_markdown_aj', 'owner_only': True},
            r'^按钮测试$': {'handler': 'test_buttons', 'owner_only': True},
            # ajdm和mddm功能
            r'^ajdm\s+(.+)$': {'handler': 'send_ajdm', 'owner_only': True},
            r'^mddm\s+(\d+)\s+(.+)$': {'handler': 'send_mddm', 'owner_only': True},
            # ark卡片示例（仅主人可用）
            r'^ark23$': {'handler': 'send_ark23', 'owner_only': True},
            r'^ark24$': {'handler': 'send_ark24', 'owner_only': True},
            r'^ark37$': {'handler': 'send_ark37', 'owner_only': True},
            # ==================== 召回功能（仅主人可用） ====================
            r'^指定召回\s+(.+)$': {'handler': 'wakeup_user', 'owner_only': True},
            r'^强制召回\s+(.+)$': {'handler': 'force_wakeup_user', 'owner_only': True},
            r'^智能召回$': {'handler': 'smart_wakeup', 'owner_only': True},
        }

    # ==================== 分享链接功能 ====================
    #可能需要链接白名单，才可发送成功
    @staticmethod
    def get_share_link(e):
        link = e.get_share_link()
        e.reply(f"🔗 你的专属邀请链接：\n{link}\n\n📌 当其他用户通过此链接添加机器人时，将记录为你的邀请" if link else "❌ 生成邀请链接失败")

    @staticmethod
    def get_share_link_custom(e):
        if not e.matches: return e.reply("❌ 用法：申请邀请链接 自定义内容")
        data = e.matches[0].strip()
        link = e.get_share_link(data) if data else None
        e.reply(f"🔗 自定义邀请链接：\n{link}\n\n📌 回调数据：{data}" if link else "❌ 生成失败")

    @staticmethod
    def query_share_count(e):
        from function.log_db import get_share_referrals_with_scene_name
        refs = get_share_referrals_with_scene_name(e.user_id)
        if not refs: return e.reply("📊 你还没有邀请任何用户\n\n💡 发送「申请邀请链接」获取链接")
        msg = f"📊 你已成功邀请 {len(refs)} 位用户：\n\n"
        for i, (oid, scene) in enumerate(refs.items(), 1):
            msg += f"{i}. {oid[:8]}****{oid[-4:] if len(oid)>12 else ''}\n   来源：{scene}\n"
        e.reply(msg)

    @staticmethod
    def query_my_sharer(e):
        from function.log_db import get_sharer_by_referral
        sid = get_sharer_by_referral(e.user_id)
        e.reply(f"📌 分享者：{sid[:8]}****{sid[-4:] if len(sid)>12 else ''}" if sid else "📌 你不是通过邀请链接添加的")

    # ==================== 媒体发送示例 ====================
    @staticmethod
    def send_advanced_image(e):
        e.reply({"custom_template_id": "102321943_1747061997", "params": [
            {"key": "px", "values": ["珊瑚宫心海 #1200px #2133px"]},
            {"key": "url", "values": ["https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0"]},
            {"key": "text", "values": ["\r\r>ElainaBot Markdown图片例子"]}]})

    #发送图片
    @staticmethod
    def send_force_image(e):
        e.reply_image("https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png", "reply_image方法发送")

    #发送本地图片
    @staticmethod
    def send_local_image(e):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "1.png")
        if not os.path.exists(path): return e.reply(f"❌ 图片不存在: {path}")
        try:
            with open(path, 'rb') as f: data = f.read()
            e.reply_image(data, f"📸 本地图片 ({len(data)/1024/1024:.2f}MB)")
        except Exception as ex: e.reply(f"❌ 读取失败: {ex}")

    #发送语音
    @staticmethod
    def send_voice(e): e.reply_voice("https://act-upload.mihoyo.com/sr-wiki/2025/06/03/160045374/420e9ac5c0c9d2b2c44b91f453b65061_2267222992827173477.wav")

    #发送语音
    @staticmethod
    def send_video(e): e.reply_video("https://i.elaina.vin/1.mp4")

    #获取图片尺寸
    @staticmethod
    def get_image_dimensions(e):
        info = e.get_image_size("https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0")
        e.reply(f"📐 宽：{info['width']}px 高：{info['height']}px\n🎯 {info['px']}" if info else "❌ 获取失败")

    #将图片上传cos桶
    @staticmethod
    def upload_to_cos(e):
        try:
            data = get_binary_content("https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png")
            r = upload_image(data, "test.png", user_id=e.user_id, return_url_only=False)
            e.reply(f"✅ URL: {r['file_url']}\n📏 {r.get('width','?')}x{r.get('height','?')}" if r else "❌ 上传失败")
        except Exception as ex: e.reply(f"❌ {ex}")

    #将图片上传到cos桶
    @staticmethod
    def upload_to_bilibili(e):
        try:
            r = e.uploadToBilibiliImageBed(get_binary_content("https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png"))
            e.reply(f"✅ URL: {r}" if r else "❌ 上传失败")
        except Exception as ex: e.reply(f"❌ {ex}")

    #将图片上传到频道图床
    @staticmethod
    def upload_to_qq(e):
        try:
            r = e.uploadToQQBotImageBed(get_binary_content("https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png"))
            e.reply(f"✅ URL: {r}" if r else "❌ 上传失败")
        except Exception as ex: e.reply(f"❌ {ex}")

    #延迟撤回
    @staticmethod
    def test_recall(e):
        mid = e.reply("⏰ 3秒后撤回...")
        if mid: threading.Thread(target=lambda: (time.sleep(3), e.recall_message(mid)), daemon=True).start()

    #利用内置定时器倒计时撤回
    @staticmethod
    def test_auto_recall(e):
        e.reply("⏰ 5秒后自动撤回", auto_delete_time=5)
        e.reply_image("https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png", "🖼️ 10秒后撤回", auto_delete_time=10)

    #自定义按钮
    @staticmethod
    def test_buttons(e):
        e.reply("请选择：", buttons=e.button([e.rows([{'text': '✅ 确认', 'data': '确认', 'enter': True}, {'text': '❌ 取消', 'data': '取消', 'style': 1}])]))
    
    @staticmethod
    def test_database(e):
        info = f"📊 用户：{e.db.get_user_count()} 群组：{e.db.get_group_count()}"
        if e.is_group and e.group_id: info += f" 本群：{e.db.get_group_member_count(e.group_id)}"
        e.reply(info)

    #mysql连接池
    @staticmethod
    def test_db_pool(e):
        try:
            with ConnectionManager() as m:
                m.execute("SELECT VERSION() as v")
                ver = (m.fetchone() or {}).get('v', '?')
            cnt = (execute_query("SELECT COUNT(*) as c FROM M_users") or {}).get('c', 0)
            e.reply(f"💾 MySQL:{ver} 用户:{cnt} 事务:{'成功' if execute_transaction([{'sql':'SELECT 1','params':None}]) else '失败'}")
        except Exception as ex: e.reply(f"❌ {ex}")

    #http连接池
    @staticmethod
    def test_http_pool(e):
        r = []
        try: r.append(f"一言：{sync_get('https://v1.hitokoto.cn/?encode=text').text[:50]}...")
        except Exception as ex: r.append(f"📝 失败({str(ex)[:30]})")
        try: r.append(f"JSON：{get_json('https://v1.hitokoto.cn/?encode=json').get('hitokoto','?')[:20]}...")
        except Exception as ex: r.append(f"📊 失败({str(ex)[:30]})")
        try: r.append(f"图片：{len(get_binary_content('https://i0.hdslb.com/bfs/openplatform/559162218f455ea859c783dceeda65cb1c724f4c.png'))/1024:.1f}KB")
        except Exception as ex: r.append(f"🖼️ 失败({str(ex)[:30]})")
        try: r.append(f"POST：{sync_post('https://httpbin.org/post',json={'t':'1'},timeout=10).status_code}")
        except Exception as ex: r.append(f"✅ 失败({str(ex)[:30]})")
        e.reply("🌐 HTTP测试：\n" + "\n".join(r))

    #自动拆分markdown模板与正常发送方式，和多参混拼
    @staticmethod
    def send_markdown_template(e):
    # 例如你的模板是 {{.text}}
    # 方式1：单个值自动拆分（使用 AJ 模板拆分逻辑）
    # 当数组只有一个元素时，会自动按 markdown 语法拆分
        event.reply_markdown("1", (
            [
                "[你好](mqqapi://aio/inlinecmd?command=你好&enter=false&reply=false)\r[你好](mqqapi://aio/inlinecmd?command=你好&enter=false&reply=false)\r[你好](mqqapi://aio/inlinecmd?command=你好&enter=false&reply=false)\r[你好](mqqapi://aio/inlinecmd?command=你好&enter=false&reply=false)\r[你好](mqqapi://aio/inlinecmd?command=你好&enter=false&reply=false)"
            ],
        ))
        #混合拼参和常规
        event.reply_markdown("4", (
            "你好啊",
            [
                            "正常文本\" /><qqbot-cmd-input text=\"按钮2\" show=\"按钮2",
                            "\" reference=\"false\" />正常文本<qqbot-cmd-input text=\"按钮3\" show=\"按钮3",
                            "\" reference=\"false\" />正常文本<qqbot-cmd-input text=\"按钮3\" show=\"按钮3",
                            "正常文本\" />"
            ]
            
        ))
        
        # 方式2：多个值不拆分
        # event.reply_markdown("1", (
        #     [
        #         "文本1",
        #         "文本2",
        #         "文本3",
        #     ],
        # ))
        
    
    #支持单参列表传入，第二参
     #event.reply_markdown("1", (
     #      [
     #         "你好啊](mqqapi://aio/inlinecmd?command=你好&enter=false&reply=false)[",
     #          "你好](mqqapi://aio/inlinecmd?command=你好&enter=false&reply=false)[",
     #          "你好啊",
     #      ],
     #        "✨ 这是文本1", 
     #  ))

     #双参普通发送模式
     #event.reply_markdown("1", (    
      #      "✨ 这是文本1",           # text
      #      "✨ 这是文本1",               # size  
      #  ),
      #  "102321943_1752737844"               # keyboard_id - 按钮模板ID
      #)  # 参数：模板名称, (参数列表)


    @staticmethod
    def test_markdown_aj(e):
        e.reply_markdown_aj("![伊蕾娜 #30px #30px](https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0)ElainaBot 测试")

    @staticmethod
    def send_ark23(e): e.reply_ark(23, ("列表卡片示例", "ElainaBot", [['功能1: 图片'], ['功能2: 语音'], ['功能3: 视频', 'https://i.elaina.vin/api/']]))
    @staticmethod
    def send_ark24(e): e.reply_ark(24, ("功能强大的QQ机器人", "机器人信息", "ElainaBot", "支持插件化开发", "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0", "https://i.elaina.vin/api/", "QQ Bot"))
    @staticmethod
    def send_ark37(e): e.reply_ark(37, ("系统通知", "状态更新", "新功能上线", "https://gchat.qpic.cn/qmeetpic/0/0-0-52C851D5FB926BC645528EB4AB462B3D/0", "https://i.elaina.vin/api/"))

    @staticmethod
    def send_ajdm(e):
        if not e.matches: return e.reply("❌ 用法：ajdm 内容")
        try: e.reply_markdown_aj(e.matches[0]); e.reply(f"✅ 长度：{len(e.matches[0])}")
        except Exception as ex: e.reply(f"❌ {ex}")

    @staticmethod
    def send_mddm(e):
        if not e.matches or len(e.matches) < 2: return e.reply("❌ 用法：mddm 模板ID 1参内容")
        tid, ps = e.matches[0], e.matches[1].strip()
        try:
            m = re.findall(r'(\d+)参([^0-9参]*(?:[0-9]+(?!参)[^0-9参]*)*)', ps)
            if not m: return e.reply("❌ 格式错误")
            d = {int(n): ([v.strip() for v in s.split(',') if v.strip()] if ',' in s else [s.strip()]) for n, s in m if s.strip()}
            if not d: return e.reply("❌ 无有效参数")
            e.reply_markdown(tid, tuple(d.get(i, ['']) for i in range(1, max(d)+1)))
            e.reply(f"✅ 模板:{tid} 参数:{len(d)}")
        except Exception as ex: e.reply(f"❌ {ex}")

    @staticmethod
    def get_message_info(e):
        info = f"📋 类型:{e.message_type}\n📝 内容:{e.content}\n🆔 ID:{e.message_id}\n⏰ {e.timestamp}\n👤 用户:{e.user_id}"
        if e.is_group: info += f"\n👥 群:{e.group_id}"
        e.reply(info)

    @staticmethod
    def get_raw_server_data(e):
        hide = ['host','x-host','x-real-ip','remote-host','x-forwarded-for','x-forwarded-host','referer','origin','location']
        hdrs = {k: "(隐藏)" if k.lower() in hide else v for k,v in (e.request_headers or {}).items()}
        raw = e.raw_data if isinstance(e.raw_data, str) else json.dumps(e.raw_data, ensure_ascii=False, indent=2) if e.raw_data else "(无)"
        e.reply(f"请求头:\n{json.dumps(hdrs, ensure_ascii=False, indent=2)}\n\n原始事件:\n{raw}")

    # ==================== 召回功能 ====================
    @staticmethod
    def wakeup_user(e):
        if not e.matches: return e.reply("❌ 用法：指定召回 用户ID")
        uid = e.matches[0].strip()
        ok, r = e.send_wakeup(uid, "📢 召回消息测试")
        e.reply(f"✅ 召回成功 {uid[:8]}**** ID:{r}" if ok else f"❌ {r}")

    @staticmethod
    def force_wakeup_user(e):
        if not e.matches: return e.reply("❌ 用法：强制召回 用户ID")
        uid = e.matches[0].strip()
        ok, r = e.force_wakeup(uid, "📢 强制召回测试")
        e.reply(f"✅ 强制召回成功 {uid[:8]}**** ID:{r}" if ok else f"❌ {r}")

    @staticmethod
    def smart_wakeup(e):
        from function.log_db import get_wakeup_users, get_wakeup_stage_name
        users = get_wakeup_users()
        if not users: return e.reply("📊 当前没有可召回用户")
        e.reply(f"� 开始召回 {len(users)} 位用户...")
        ok, fail, res = 0, 0, []
        for u in users:
            s, r = e.send_wakeup(u['openid'], f"📢 好久不见！已 {u['days']} 天了")
            mid = u['openid'][:8] + "****"
            if s: ok += 1; res.append(f"✅ {mid} ({get_wakeup_stage_name(u['stage'])})")
            else: fail += 1; res.append(f"❌ {mid}: {r}")
        msg = f"📊 完成 ✅{ok} ❌{fail}\n\n" + "\n".join(res[:20])
        if len(res) > 20: msg += f"\n... 还有 {len(res)-20} 条"
        e.reply(msg)
