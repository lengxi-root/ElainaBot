#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
import time
import requests
import re
import io
from PIL import Image, ImageDraw, ImageFont
from core.plugin.PluginManager import Plugin
import config

import warnings
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

BASE = 'https://api.elaina.vin/api/bot'
LOGIN_URL = f"{BASE}/get_login.php"
GET_LOGIN = f"{BASE}/robot.php"
MESSAGE = f"{BASE}/message.php"
BOTLIST = f"{BASE}/bot_list.php"
BOTDATA = f"{BASE}/bot_data.php"
MSGTPL = f"{BASE}/md.php"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'bot')
FILE = os.path.join(DATA_DIR, 'robot.json')
OPENAPI_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'openapi.json')
SPECIAL_USER_ID = config.OWNER_IDS[0] if config.OWNER_IDS else ""

_user_data = {}
_login_tasks = {}
_last_login_success = {}
os.makedirs(DATA_DIR, exist_ok=True)

def load_user_data():
    global _user_data
    _user_data = {}
    
    if os.path.exists(FILE):
        try:
            with open(FILE, 'r', encoding='utf-8') as f:
                _user_data = json.load(f)
        except:
            _user_data = {}
    
    if os.path.exists(OPENAPI_FILE):
        try:
            with open(OPENAPI_FILE, 'r', encoding='utf-8') as f:
                openapi_data = json.load(f)
                web_user_data = openapi_data.get('web_user', {})
                
                if web_user_data and (web_user_data.get('type') == 'ok' or web_user_data.get('uin')):
                    if 'type' not in web_user_data:
                        web_user_data['type'] = 'ok'
                    _user_data[SPECIAL_USER_ID] = web_user_data
        except:
            pass

def ensure_user_data_loaded():
    global _user_data
    if not _user_data:
        load_user_data()

def reload_user_data():
    global _user_data
    load_user_data()

def ensure_special_user_data(user_id):
    if user_id == SPECIAL_USER_ID:
        reload_user_data()
        return user_id in _user_data
    else:
        ensure_user_data_loaded()
        return user_id in _user_data

def save_user_data(user_id, data):
    global _user_data
    _user_data[user_id] = data
    
    if user_id == SPECIAL_USER_ID:
        try:
            openapi_data = {}
            if os.path.exists(OPENAPI_FILE):
                with open(OPENAPI_FILE, 'r', encoding='utf-8') as f:
                    openapi_data = json.load(f)
            
            openapi_data['web_user'] = data
            with open(OPENAPI_FILE, 'w', encoding='utf-8') as f:
                json.dump(openapi_data, f, indent=2, ensure_ascii=False)
        except:
            pass
    else:
        with open(FILE, 'w', encoding='utf-8') as f:
            json.dump(_user_data, f, indent=2)

def create_ssl_session():
    return requests.Session()

def get_font_path(font_name="msyh.ttc"):
    chinese_fonts = ["msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc", "simkai.ttf", "simfang.ttf", "STHeiti-Light.ttc", "STHeiti-Medium.ttc", "STFangsong.ttf"]
    font_dirs = [os.path.join(os.environ.get('WINDIR', ''), 'Fonts'), "/usr/share/fonts/truetype/", "/usr/share/fonts/", "/Library/Fonts/", "/System/Library/Fonts/", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")]
    
    for font_dir in font_dirs:
        path = os.path.join(font_dir, font_name)
        if os.path.exists(path):
            return path
    
    for font in chinese_fonts:
        for font_dir in font_dirs:
            path = os.path.join(font_dir, font)
            if os.path.exists(path):
                return path
    
    for font_dir in font_dirs:
        if os.path.exists(font_dir):
            try:
                for file in os.listdir(font_dir):
                    if file.lower().endswith(('.ttc', '.ttf', '.otf')):
                        return os.path.join(font_dir, file)
            except:
                pass
    
    return None

def draw_rounded_rectangle(draw, xy, radius, fill=None, outline=None, width=1):
    x1, y1, x2, y2 = xy
    diameter = radius * 2
    
    draw.rectangle((x1 + radius, y1, x2 - radius, y2), fill=fill, outline=None)
    draw.rectangle((x1, y1 + radius, x2, y2 - radius), fill=fill, outline=None)
    
    draw.pieslice((x1, y1, x1 + diameter, y1 + diameter), 180, 270, fill=fill, outline=None)
    draw.pieslice((x2 - diameter, y1, x2, y1 + diameter), 270, 360, fill=fill, outline=None)
    draw.pieslice((x1, y2 - diameter, x1 + diameter, y2), 90, 180, fill=fill, outline=None)
    draw.pieslice((x2 - diameter, y2 - diameter, x2, y2), 0, 90, fill=fill, outline=None)
    
    if outline:
        draw.line((x1 + radius, y1, x2 - radius, y1), fill=outline, width=width)
        draw.line((x1 + radius, y2, x2 - radius, y2), fill=outline, width=width)
        draw.line((x1, y1 + radius, x1, y2 - radius), fill=outline, width=width)
        draw.line((x2, y1 + radius, x2, y2 - radius), fill=outline, width=width)
        
        draw.arc((x1, y1, x1 + diameter, y1 + diameter), 180, 270, fill=outline, width=width)
        draw.arc((x2 - diameter, y1, x2, y1 + diameter), 270, 360, fill=outline, width=width)
        draw.arc((x1, y2 - diameter, x1 + diameter, y2), 90, 180, fill=outline, width=width)
        draw.arc((x2 - diameter, y2 - diameter, x2, y2), 0, 90, fill=outline, width=width)

def get_status_color(status):
    status_colors = {
        "审核通过": (82, 196, 26),
        "未通过": (245, 34, 45),
        "审核中": (24, 144, 255),
        "未提审": (250, 173, 20)
    }
    return status_colors.get(status, (51, 51, 51))

def wrap_text(text, font, max_width):
    lines = []
    current_line = ""
    
    for char in text:
        test_line = current_line + char
        width = font.getbbox(test_line)[2] - font.getbbox(test_line)[0]
        
        if width <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = char
    
    if current_line:
        lines.append(current_line)
    
    return lines

def draw_button(draw, x, y, width, height, label, style, action_type, font):
    style_colors = {
        0: {"bg": (255, 255, 255, 0), "border": (0, 0, 0, 80), "text": (0, 0, 0)},
        1: {"bg": (255, 255, 255, 0), "border": (24, 144, 255), "text": (24, 144, 255)},
        2: {"bg": (255, 255, 255, 0), "border": (0, 0, 0, 80), "text": (0, 0, 0)},
        3: {"bg": (255, 255, 255, 0), "border": (0, 0, 0, 80), "text": (245, 34, 45)},
        4: {"bg": (24, 144, 255), "border": (24, 144, 255), "text": (255, 255, 255)},
    }
    
    colors = style_colors.get(style if 0 <= style <= 4 else 0)
    
    draw_rounded_rectangle(draw, (x, y, x+width, y+height), 6, fill=colors["bg"], outline=colors["border"], width=1)
    
    text_width = font.getbbox(label)[2]
    text_height = font.getbbox(label)[3]
    text_x = x + (width - text_width) // 2
    text_y = y + (height - text_height) // 2 - 2
    
    draw.text((text_x, text_y), label, fill=colors["text"], font=font)
    
    icon_size = 18
    if action_type == 0:
        link_x = x + width - icon_size - 5
        link_y = y + height - icon_size - 5
        draw.rectangle((link_x, link_y, link_x+icon_size, link_y+icon_size), fill=None, outline=colors["text"], width=1)
        draw.line((link_x+icon_size//2, link_y+icon_size//2, link_x+icon_size, link_y), fill=colors["text"], width=1)
    elif action_type == 1:
        load_x = x + width - icon_size - 5
        load_y = y + height - icon_size - 5
        draw.arc((load_x, load_y, load_x+icon_size, load_y+icon_size), 0, 270, fill=colors["text"], width=1)

def render_template_detail(data):
    colors = {
        "background": (245, 246, 250),
        "card": (255, 255, 255),
        "primary": (24, 144, 255),
        "secondary": (67, 74, 100),
        "text": (51, 51, 51),
        "light_text": (102, 102, 102),
        "border": (240, 240, 240),
        "info": (24, 144, 255),
        "success": (82, 196, 26),
        "warning": (250, 173, 20),
        "error": (245, 34, 45),
        "header_bg": (240, 248, 255)
    }
    
    template = data.get('template', {})
    is_button_template = template.get('模板类型') == '按钮模板'
    
    if is_button_template:
        width = 1800
        height = 1400
        padding = 40
    else:
        width = 1200
        height = 900
        padding = 30
        
    border_radius = 20
    inner_padding = 25
    
    image = Image.new('RGB', (width, height), colors["background"])
    draw = ImageDraw.Draw(image)
    
    stripe_height = 8
    draw.rectangle((0, 0, width, stripe_height), fill=colors["primary"])
    
    msyh_font_paths = ["/usr/share/fonts/msyh/msyh.ttc", "/usr/share/fonts/msyh/msyhbd.ttc", "/usr/share/fonts/msyh/msyhl.ttc"]
    
    try:
        regular_font = bold_font = light_font = None
        
        for path in msyh_font_paths:
            if os.path.exists(path):
                if "msyh.ttc" in path and not regular_font:
                    regular_font = path
                elif "msyhbd.ttc" in path and not bold_font:
                    bold_font = path
                elif "msyhl.ttc" in path and not light_font:
                    light_font = path
        
        if regular_font:
            title_font = ImageFont.truetype(bold_font or regular_font, 48)
            header_font = ImageFont.truetype(bold_font or regular_font, 36)
            normal_font = ImageFont.truetype(regular_font, 30)
            bold_font = ImageFont.truetype(bold_font or regular_font, 32)
            small_font = ImageFont.truetype(regular_font, 24)
            button_font = ImageFont.truetype(regular_font, is_button_template and 28 or 22)
        else:
            title_font = ImageFont.load_default().font_variant(size=48)
            header_font = ImageFont.load_default().font_variant(size=36)
            normal_font = ImageFont.load_default().font_variant(size=30)
            bold_font = ImageFont.load_default().font_variant(size=32)
            small_font = ImageFont.load_default().font_variant(size=24)
            button_font = ImageFont.load_default().font_variant(size=22)
    except:
        title_font = ImageFont.load_default().font_variant(size=48)
        header_font = ImageFont.load_default().font_variant(size=36)
        normal_font = ImageFont.load_default().font_variant(size=30)
        bold_font = ImageFont.load_default().font_variant(size=32)
        small_font = ImageFont.load_default().font_variant(size=24)
        button_font = ImageFont.load_default().font_variant(size=22)
    
    def safe_draw_rectangle(draw, xy, radius=0, **kwargs):
        x1, y1, x2, y2 = xy
        if x2 <= x1:
            x1, x2 = x2, x1
        if y2 <= y1:
            y1, y2 = y2, y1
            
        if radius > 0:
            draw_rounded_rectangle(draw, (x1, y1, x2, y2), radius, **kwargs)
        else:
            draw.rectangle((x1, y1, x2, y2), **kwargs)
    
    main_card = (padding, padding, width-padding, height-padding)
    safe_draw_rectangle(draw, main_card, border_radius, fill=colors["card"])
    
    title_y = padding + 20
    title_bg = (padding+20, title_y, width-padding-20, title_y+80)
    safe_draw_rectangle(draw, title_bg, border_radius//2, fill=colors["header_bg"])
    
    title_text = "Bot模板详情"
    title_width = title_font.getbbox(title_text)[2]
    title_x = (width - title_width) // 2
    draw.text((title_x, title_y+12), title_text, fill=colors["primary"], font=title_font)
    
    current_y = title_y + 120
    
    info_bg = (padding+20, current_y, width-padding-20, current_y+80)
    safe_draw_rectangle(draw, info_bg, border_radius//2, fill=colors["background"])
    
    draw.text((padding+40, current_y+10), "账号", fill=colors["secondary"], font=small_font)
    draw.text((padding+40, current_y+40), data.get('uin', ''), fill=colors["text"], font=normal_font)
    
    draw.text((width//2, current_y+10), "AppID", fill=colors["secondary"], font=small_font)
    draw.text((width//2, current_y+40), data.get('appid', ''), fill=colors["text"], font=normal_font)
    
    current_y += 100
    
    template = data.get('template', {})
    info_items = [
        {"label": "模板ID", "value": template.get('模板id', '')},
        {"label": "模板名称", "value": template.get('模板名称', '')},
        {"label": "模板类型", "value": template.get('模板类型', '')},
        {"label": "审核状态", "value": template.get('模板状态', '')}
    ]
    
    draw.text((padding+20, current_y), "模板信息", fill=colors["primary"], font=header_font)
    current_y += 50
    
    for item in info_items:
        row_height = 60
        
        info_bg = (padding+20, current_y, width-padding-20, current_y+row_height)
        safe_draw_rectangle(draw, info_bg, border_radius//2, fill=colors["background"])
        
        label_width = 160
        
        text_y = current_y + (row_height - bold_font.getbbox(item["label"])[3]) // 2
        draw.text((padding+40, text_y), item["label"], fill=colors["secondary"], font=bold_font)
        
        value_x = padding + label_width + 40
        value_y = current_y + (row_height - normal_font.getbbox(item["value"])[3]) // 2
        
        if item["label"] == "审核状态":
            status_color = get_status_color(item["value"])
            draw.text((value_x, value_y), item["value"], fill=status_color, font=normal_font)
        else:
            draw.text((value_x, value_y), item["value"], fill=colors["text"], font=normal_font)
        
        current_y += row_height + 10
    
    current_y += 20
    draw.text((padding+20, current_y), "模板内容", fill=colors["primary"], font=header_font)
    current_y += 50
    
    remaining_height = height - current_y - padding - 40
    
    content_bg = (padding+20, current_y, width-padding-20, height-padding-20)
    safe_draw_rectangle(draw, content_bg, border_radius//2, fill=colors["background"])
    
    content_text = template.get('模板内容', '')
    
    is_button_template = template.get('模板类型') == '按钮模板'
    button_data = None
    
    if is_button_template:
        try:
            button_data = json.loads(content_text)
        except:
            is_button_template = False
            
    elif content_text.startswith('@'):
        try:
            is_button_template = True
            json_text = content_text[1:].strip()
            button_data = json.loads(json_text)
        except:
            is_button_template = False
    
    if is_button_template and button_data:
        draw.text((padding+40, current_y+20), "按钮模板预览:", fill=colors["secondary"], font=bold_font)
        btn_start_y = current_y + 70
        
        try:
            rows = button_data.get('rows', [])
            
            max_buttons_per_row = 5
            button_width = 300
            button_height = 80
            button_margin = 20
            
            current_btn_y = btn_start_y
            for row_idx, row in enumerate(rows):
                if row_idx >= 5:
                    break
                
                buttons = row.get('buttons', [])
                btn_count = min(len(buttons), max_buttons_per_row)
                
                if btn_count == 0:
                    continue
                
                available_width = width - padding * 2 - 80
                each_button_width = (available_width - (btn_count - 1) * button_margin) // btn_count
                start_x = padding + 40
                
                for i in range(btn_count):
                    btn = buttons[i]
                    btn_x = start_x + i * (each_button_width + button_margin)
                    
                    render_data = btn.get('render_data', {})
                    action = btn.get('action', {})
                    
                    label = render_data.get('label', 'Button')
                    style = render_data.get('style', 0)
                    action_type = action.get('type', 2)
                    
                    draw_button(draw, btn_x, current_btn_y, each_button_width, button_height, 
                                label, style, action_type, button_font)
                
                current_btn_y += button_height + 20
                
            descriptions = [
                "按钮仅供参考，实际效果以实际为准",
                "类型: 0=跳转链接(链接图标), 1=回调(加载图标), 2=回车命令"
            ]
            
            note_y = current_btn_y + 20
            for desc in descriptions:
                draw.text((padding+40, note_y), desc, fill=colors["light_text"], font=small_font)
                note_y += 30
        
        except Exception as e:
            draw.text((padding+40, btn_start_y), f"按钮渲染失败: {str(e)}", 
                      fill=colors["error"], font=normal_font)
            
            lines = wrap_text(json_text, normal_font, width-padding*3)
            line_height = 40
            max_lines = min((height - btn_start_y - 100) // line_height, 10)
            
            for i, line in enumerate(lines[:max_lines]):
                draw.text((padding+40, btn_start_y + 40 + i * line_height), 
                          line, fill=colors["text"], font=normal_font)
    else:
        lines = wrap_text(content_text, normal_font, width-padding*3)
        
        line_height = 40
        max_lines = remaining_height // line_height
        if max_lines <= 0:
            max_lines = 1
        display_lines = lines[:max_lines]
        
        for i, line in enumerate(display_lines):
            line_y = current_y + 20 + i * line_height
            draw.text((padding+40, line_y), line, fill=colors["text"], font=normal_font)
        
        if len(lines) > max_lines:
            note = f"(内容过长，仅显示前{max_lines}行)"
            draw.text((padding+20, height-padding-30), note, fill=colors["light_text"], font=small_font)
    
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG', quality=100, optimize=True)
    img_byte_arr.seek(0)
    
    return img_byte_arr

class robot_data_plugin(Plugin):
    priority = 5000

    @staticmethod
    def get_regex_handlers():
        return {
            r"^管理登录$": {'handler': 'login', 'owner_only': False},
            r"^bot通知$": {'handler': 'get_message', 'owner_only': False},
            r"^bot列表$": {'handler': 'get_botlist', 'owner_only': False},
            r"^bot数据(\d+|max)$": {'handler': 'get_botdata', 'owner_only': False},
            r"^bot模板$": {'handler': 'get_msgtpl', 'owner_only': False},
            r"^bot模板\s*([^\s]+)$": {'handler': 'get_msgtpl_detail', 'owner_only': False},
            r"^切换appid\s*(.+)$": {'handler': 'switch_appid', 'owner_only': False}
        }

    @staticmethod
    def _verify_credentials(login_data):
        try:
            uin = login_data.get('uin')
            ticket = login_data.get('ticket')
            developer_id = login_data.get('developerId')
            
            if not all([uin, ticket, developer_id]):
                return False
                
            session = create_ssl_session()
            url = f"{BOTLIST}?uin={uin}&ticket={ticket}&developerId={developer_id}"
            response = session.get(url, verify=False, timeout=10)
            res = response.json()
            
            return res.get('code') == 0
        except:
            return False

    @staticmethod
    def login(event):
        if event.event_type == "INTERACTION_CREATE" and not event.content.strip() == "管理登录":
            return
        global _login_tasks, _last_login_success
        user_id = event.user_id
        current_time = time.time()
        
        if user_id == SPECIAL_USER_ID:
            ensure_user_data_loaded()
            
            if user_id in _user_data:
                login_data = _user_data[user_id]
                
                if login_data.get('type') == 'ok':
                    if robot_data_plugin._verify_credentials(login_data):
                        app_type = login_data.get('appType')
                        app_type_str = '小程序' if app_type == '0' else '机器人' if app_type == '2' else '未知'
                        content = f"[{login_data.get('uin')}]\n管理员登录成功\n\n>登录类型：{app_type_str}\nAppId：{login_data.get('appId')}\n切换+appid可以切换机器人"
                        buttons = event.button([
                            event.rows([
                                {'text': '通知', 'data': 'bot通知', 'type': 1, 'style': 1},
                                {'text': '数据', 'data': 'bot数据4', 'type': 2, 'style': 1},
                                {'text': '列表', 'data': 'bot列表', 'type': 1, 'style': 1},
                                {'text': '模板', 'data': 'bot模板', 'type': 1, 'style': 1}
                            ])
                        ])
                        _last_login_success[user_id] = time.time()
                        event.reply(content, buttons)
                        return
                    else:
                        event.reply("管理员凭证已失效，正在重新获取登录二维码...")
                else:
                    event.reply("管理员数据无效，正在获取登录二维码...")
            else:
                event.reply("管理员数据未找到，正在获取登录二维码...")
        
        if user_id in _last_login_success and current_time - _last_login_success[user_id] < 20:
            return
        if user_id in _login_tasks and time.time() - _login_tasks[user_id][0] < 15:
            event.reply("15秒内你已经申请一次登录了，请稍后重试。")
            return
        _login_tasks[user_id] = (time.time(), None)
        robot_data_plugin._sync_login(event, user_id)
        if user_id in _login_tasks:
            del _login_tasks[user_id]

    @staticmethod
    def _sync_login(event, user_id):
        global _user_data, _last_login_success
        ensure_user_data_loaded()
        _user_data[user_id] = {'type': 'login'}
        session = create_ssl_session()
        response = session.get(LOGIN_URL, verify=False)
        data = response.json()
        url = data.get('url')
        qr = data.get('qr')
        if not url or not qr:
            event.reply("获取登录二维码失败，请稍后重试")
            return
        content = f"<@{user_id}>\n[QQ开发平台管理端登录]\n登录具有时效性，请尽快登录\n---\n>当你选择登录，代表你已经同意将数据托管给伊蕾娜Bot。"
        buttons = event.button([
            event.rows([{'text': '点击登录', 'data': url, 'type': 0, 'list': [user_id], 'style': 4}])
        ])
        event.reply(content, buttons)
        max_time = time.time() + 60
        while time.time() < max_time:
            time.sleep(3)
            response = session.get(f"{GET_LOGIN}?qrcode={qr}", verify=False)
            res = response.json()
            if res.get('code') == 0:
                login_data = res.get('data', {}).get('data', {})
                ensure_user_data_loaded()
                login_data['type'] = 'ok'
                save_user_data(user_id, login_data)
                app_type = login_data.get('appType')
                app_type_str = '小程序' if app_type == '0' else '机器人' if app_type == '2' else '未知'
                content = f"[{login_data.get('uin')}]\n登录成功\n\n>登录类型：{app_type_str}\nAppId：{login_data.get('appId')}\n切换+appid可以切换机器人"
                buttons = event.button([
                    event.rows([
                        {'text': '通知', 'data': 'bot通知', 'type': 1, 'style': 1},
                        {'text': '数据', 'data': 'bot数据4', 'type': 2, 'style': 1},
                        {'text': '列表', 'data': 'bot列表', 'type': 1, 'style': 1},
                        {'text': '模板', 'data': 'bot模板', 'type': 1, 'style': 1}
                    ])
                ])
                _last_login_success[user_id] = time.time()
                event.reply(content, buttons)
                if user_id in _login_tasks:
                    del _login_tasks[user_id]
                return
        event.reply(f"<@{user_id}>登录失效，请重新尝试")

    @staticmethod
    def get_message(event):
        user = event.user_id
        
        if not ensure_special_user_data(user):
            content = f'<@{user}> 未查询到你的登录信息'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
            
        robot_data_plugin._sync_get_message(event)

    @staticmethod
    def _sync_get_message(event):
        global _user_data
        user = event.user_id
        data = _user_data.get(user, {})
        
        session = create_ssl_session()
        url = f"{MESSAGE}?uin={data.get('uin')}&ticket={data.get('ticket')}&developerId={data.get('developerId')}"
        
        try:
            response = session.get(url, verify=False, timeout=10)
            res = response.json()
            
            if res.get('code') != 0:
                content = f'<@{user}>登录状态失效'
                buttons = event.button([
                    event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
                ])
                event.reply(content, buttons)
                return
        except:
            content = f'<@{user}>请求失败，请稍后重试'
            event.reply(content)
            return
        msglist = [f"Uin:{data.get('uin')}\nAppid:{data.get('appId')}\n\n```python"]
        messages = res.get('messages', [])
        for j in range(min(len(messages), 8)):
            if j > 0:
                msglist.append('——————')
            message_content = messages[j].get('content', '').split("\n\n")[0].strip()
            message_time = messages[j].get('send_time', '')
            msglist.append(message_content)
            msglist.append(message_time)
        msglist.append('\n```\n')
        content = '\n'.join(msglist)
        buttons = event.button([
            event.rows([
                {'text': '通知', 'data': 'bot通知', 'type': 1, 'style': 1},
                {'text': '数据', 'data': 'bot数据4', 'type': 2, 'style': 1},
                {'text': '列表', 'data': 'bot列表', 'type': 1, 'style': 1},
                {'text': '模板', 'data': 'bot模板', 'type': 1, 'style': 1}
            ])
        ])
        event.reply(content, buttons)

    @staticmethod
    def get_botlist(event):
        user = event.user_id
        
        if not ensure_special_user_data(user):
            content = f'<@{user}> 未查询到你的登录信息'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
        robot_data_plugin._sync_get_botlist(event)

    @staticmethod
    def _sync_get_botlist(event):
        global _user_data
        user = event.user_id
        data = _user_data.get(user, {})
        session = create_ssl_session()
        url = f"{BOTLIST}?uin={data.get('uin')}&ticket={data.get('ticket')}&developerId={data.get('developerId')}"
        response = session.get(url, verify=False)
        res = response.json()
        if res.get('code') != 0:
            content = f'<@{user}>登录状态失效'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
        msglist = [f"Uin:{data.get('uin')}\n\n```python"]
        apps = res.get('data', {}).get('apps', [])
        for j in range(len(apps)):
            if j > 0:
                msglist.append('——————')
            app_name = apps[j].get('app_name', '')
            app_id = apps[j].get('app_id', '')
            app_desc = apps[j].get('app_desc', '')
            msglist.append(f"Bot:{app_name}")
            msglist.append(f"AppId:{app_id}")
            msglist.append(f"介绍:{app_desc}")
        msglist.append('\n```\n')
        content = '\n'.join(msglist)
        buttons = event.button([
            event.rows([
                {'text': '通知', 'data': 'bot通知', 'type': 1, 'style': 1},
                {'text': '数据', 'data': 'bot数据4', 'type': 2, 'style': 1},
                {'text': '列表', 'data': 'bot列表', 'type': 1, 'style': 1},
                {'text': '模板', 'data': 'bot模板', 'type': 1, 'style': 1}
            ])
        ])
        event.reply(content, buttons)

    @staticmethod
    def get_botdata(event):
        user = event.user_id
        
        if not ensure_special_user_data(user):
            content = f'<@{user}> 未查询到你的登录信息'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
        robot_data_plugin._sync_get_botdata(event)

    @staticmethod
    def _sync_get_botdata(event):
        global _user_data
        import re
        match = re.search(r'^bot数据(\d+|max)$', event.content)
        days = match.group(1) if match else '4'
        user = event.user_id
        data = _user_data.get(user, {})
        base_url = f"{BOTDATA}?appid={data.get('appId')}&uin={data.get('uin')}&ticket={data.get('ticket')}&developerId={data.get('developerId')}"
        session = create_ssl_session()
        
        # 同步方式获取三种数据
        response1 = session.get(f"{base_url}&type=1", verify=False)
        data1_json = response1.json()
        
        response2 = session.get(f"{base_url}&type=2", verify=False)
        data2_json = response2.json()
        
        response3 = session.get(f"{base_url}&type=3", verify=False)
        data3_json = response3.json()
        
        if any(x.get('retcode', -1) != 0 for x in [data1_json, data2_json, data3_json]):
            content = f'<@{user}>登录状态失效'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
        
        msg_data = data1_json.get('data', {}).get('msg_data', [])
        group_data = data2_json.get('data', {}).get('group_data', [])
        friend_data = data3_json.get('data', {}).get('friend_data', [])
        
        def format_data(data, index):
            item = data[index] if index < len(data) else {}
            return {
                "报告日期": item.get('报告日期', '0'),
                "上行消息量": item.get('上行消息量', '0'),
                "上行消息人数": item.get('上行消息人数', '0'),
                "下行消息量": item.get('下行消息量', '0'),
                "总消息量": item.get('总消息量', '0'),
                "现有群组": item.get('现有群组', '0'),
                "已使用群组": item.get('已使用群组', '0'),
                "新增群组": item.get('新增群组', '0'),
                "移除群组": item.get('移除群组', '0'),
                "现有好友数": item.get('现有好友数', '0'),
                "已使用好友数": item.get('已使用好友数', '0'),
                "新增好友数": item.get('新增好友数', '0'),
                "移除好友数": item.get('移除好友数', '0')
            }
        
        def get_day_data(index):
            prefix = '' if index == 0 else '————————\n'
            formatted_msg = format_data(msg_data, index)
            formatted_group = format_data(group_data, index)
            formatted_friend = format_data(friend_data, index)
            return f"""{prefix}【日期：{formatted_msg['报告日期']}】\n消息统计:\n上行：{formatted_msg['上行消息量']}  人数：{formatted_msg['上行消息人数']}\n总量：{formatted_msg['总消息量']}  下行：{formatted_msg['下行消息量']}\n群组统计：\n新增：{formatted_group['新增群组']}  减少：{formatted_group['移除群组']}\n已有：{formatted_group['现有群组']}  使用：{formatted_group['已使用群组']}\n好友统计：\n新增：{formatted_friend['新增好友数']}  减少：{formatted_friend['移除好友数']}\n已有：{formatted_friend['现有好友数']}  使用：{formatted_friend['已使用好友数']}"""
        
        max_days = min(len(msg_data), len(group_data), len(friend_data))
        actual_days = max_days if days == 'max' else min(int(days), max_days)
        total_up_msg_people = 0
        for i in range(len(msg_data)):
            total_up_msg_people += int(format_data(msg_data, i)['上行消息人数'])
        avg_up_msg_people = "{:.2f}".format(total_up_msg_people / 30)
        day_data_list = [get_day_data(i) for i in range(actual_days)]
        
        msglist = [
            f"Uid：{data.get('uin')}\nappid：{data.get('appId')}\n30天平均DAU: {avg_up_msg_people}\n\n```python",
            *day_data_list,
            "\n```\n"
        ]
        content = '\n'.join(msglist)
        buttons = event.button([
            event.rows([
                {'text': '通知', 'data': 'bot通知', 'type': 1, 'style': 1},
                {'text': '数据', 'data': 'bot数据4', 'type': 2, 'style': 1},
                {'text': '列表', 'data': 'bot列表', 'type': 1, 'style': 1},
                {'text': '模板', 'data': 'bot模板', 'type': 1, 'style': 1}
            ])
        ])
        event.reply(content, buttons)
            
    @staticmethod
    def get_msgtpl(event):
        user = event.user_id
        
        if not ensure_special_user_data(user):
            content = f'<@{user}> 未查询到你的登录信息'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
        robot_data_plugin._sync_get_msgtpl(event)

    @staticmethod
    def _sync_get_msgtpl(event):
        global _user_data
        user = event.user_id
        data = _user_data.get(user, {})
        session = create_ssl_session()
        url = f"{MSGTPL}?uin={data.get('uin')}&ticket={data.get('ticket')}&developerId={data.get('developerId')}&appid={data.get('appId')}"
        response = session.get(url, verify=False)
        res = response.json()
        if res.get('retcode') != 0 and res.get('code') != 0:
            content = f'<@{user}>登录状态失效'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
        
        msglist = [f"Uin:{data.get('uin')}\nAppid:{data.get('appId')}\n\n```python"]
        templates = res.get('data', {}).get('list', [])
        if not templates:
            msglist.append("暂无消息模板")
        else:
            for j in range(min(len(templates), 8)):
                if j > 0:
                    msglist.append('——————')
                # 使用新的字段名
                tpl_name = templates[j].get('模板名称', '未命名')
                tpl_id = templates[j].get('模板id', '无ID')
                status = templates[j].get('模板状态', '未知状态')
                tpl_type = templates[j].get('模板类型', '未知类型')
                create_time = templates[j].get('创建时间', '')
                
                msglist.append(f"模板名称: {tpl_name}")
                msglist.append(f"模板ID: {tpl_id}")
                msglist.append(f"状态: {status}")
                msglist.append(f"类型: {tpl_type}")
                if create_time:
                    msglist.append(f"创建时间: {create_time}")
        msglist.append('\n```\n')
        content = '\n'.join(msglist)
        buttons = event.button([
            event.rows([
                {'text': '详细', 'data': 'bot模板1', 'type': 2, 'style': 1},
                {'text': '数据', 'data': 'bot数据4', 'type': 2, 'style': 1},
            ])
        ])
        event.reply(content, buttons)

    @staticmethod
    def get_msgtpl_detail(event):
        user = event.user_id
        
        if not ensure_special_user_data(user):
            content = f'<@{user}> 未查询到你的登录信息'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
            
        match = re.search(r'^bot模板\s*([^\s]+)$', event.content)
        if not match:
            event.reply("指令格式错误，请使用 bot模板模板ID 格式")
            return
            
        tpl_id = match.group(1).strip()
        if not tpl_id:
            event.reply("请提供有效的模板ID")
            return
            
        robot_data_plugin._sync_get_msgtpl_detail(event, tpl_id)

    @staticmethod
    def _sync_get_msgtpl_detail(event, tpl_id):
        global _user_data
        user = event.user_id
        data = _user_data.get(user, {})
        session = create_ssl_session()
        
        # 首先获取模板列表
        url = f"{MSGTPL}?uin={data.get('uin')}&ticket={data.get('ticket')}&developerId={data.get('developerId')}&appid={data.get('appId')}"
        response = session.get(url, verify=False)
        res = response.json()
        if res.get('retcode') != 0 and res.get('code') != 0:
            content = f'<@{user}>登录状态失效'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
        
        templates = res.get('data', {}).get('list', [])
        if not templates:
            event.reply("暂无消息模板")
            return
        
        target_template = None
        current_index = -1
        
        if tpl_id.isdigit() and 1 <= int(tpl_id) <= len(templates):
            current_index = int(tpl_id) - 1
            target_template = templates[current_index]
        else:
            for i, template in enumerate(templates):
                if template.get('模板id') == tpl_id:
                    target_template = template
                    current_index = i
                    break
        
        if not target_template:
            event.reply(f"未找到ID为 {tpl_id} 的模板")
            return
        
        # 准备渲染数据
        render_data = {
            "uin": data.get('uin'),
            "appid": data.get('appId'),
            "template": target_template
        }
        
        try:
            img_bytes = render_template_detail(render_data)
            
            uploaded_url = event.uploadToQQImageBed(img_bytes.getvalue())
            if uploaded_url:
                content = f"![模板详情 #1200px #900px]({uploaded_url})\n模板详情 ({current_index + 1}/{len(templates)})"
                
                first_row_buttons = [
                    {'text': '模板列表', 'data': 'bot模板', 'type': 1, 'style': 1},
                    {'text': '查询', 'data': 'bot模板', 'type': 2, 'style': 1}
                ]
                
                second_row_buttons = []
                if current_index > 0:
                    prev_index = current_index
                    second_row_buttons.append({'text': '上一个', 'data': f'bot模板{prev_index}', 'type': 1, 'style': 1})
                
                if current_index < len(templates) - 1:
                    next_index = current_index + 2
                    second_row_buttons.append({'text': '下一个', 'data': f'bot模板{next_index}', 'type': 1, 'style': 1})
                
                button_rows = [event.rows(first_row_buttons)]
                if second_row_buttons:
                    button_rows.append(event.rows(second_row_buttons))
                
                buttons = event.button(button_rows)
                event.reply(content, buttons)
            else:
                event.reply("上传模板详情图片失败，请稍后重试")
        except Exception as e:
            event.reply(f"生成模板详情图片时出错: {str(e)}")

    @staticmethod
    def switch_appid(event):
        user = event.user_id
        
        if not ensure_special_user_data(user):
            content = f'<@{user}> 未查询到你的登录信息'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
            
        match = re.search(r'^切换appid\s*(.+)$', event.content)
        if not match:
            event.reply("指令格式错误，请使用 切换appid appId 格式")
            return
            
        new_appid = match.group(1).strip()
        if not new_appid:
            event.reply("请提供有效的AppID")
            return
            
        robot_data_plugin._sync_switch_appid(event, new_appid)
        
    @staticmethod
    def _sync_switch_appid(event, new_appid):
        global _user_data
        user = event.user_id
        data = _user_data.get(user, {})
        current_appid = data.get('appId')
        
        if current_appid == new_appid:
            event.reply(f"当前已经是使用AppID: {current_appid}")
            return
            
        session = create_ssl_session()
        # 先获取机器人列表，验证AppID是否有效
        url = f"{BOTLIST}?uin={data.get('uin')}&ticket={data.get('ticket')}&developerId={data.get('developerId')}"
        response = session.get(url, verify=False)
        res = response.json()
        if res.get('code') != 0:
            content = f'<@{user}>登录状态失效'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
        
        apps = res.get('data', {}).get('apps', [])
        valid_appid = False
        app_name = ""
        
        for app in apps:
            if app.get('app_id') == new_appid:
                valid_appid = True
                app_name = app.get('app_name', '未命名机器人')
                break
        
        if not valid_appid:
            available_apps = []
            for i, app in enumerate(apps, 1):
                available_apps.append(f"{i}. {app.get('app_name', '未命名')}: {app.get('app_id')}")
            
            available_text = "\n".join(available_apps)
            event.reply(f"提供的AppID无效，请从以下可用AppID中选择：\n\n```python\n{available_text}\n```\n")
            return
        
        old_appid = data.get('appId')
        data['appId'] = new_appid
        
        save_user_data(user, data)
        content = f"AppID已切换成功\n\n```python\n原AppID: {old_appid}\n新AppID: {new_appid}\n机器人: {app_name}\n```\n"
        buttons = event.button([
            event.rows([
                {'text': '通知', 'data': 'bot通知', 'type': 1, 'style': 1},
                {'text': '数据', 'data': 'bot数据4', 'type': 2, 'style': 1},
                {'text': '模板', 'data': 'bot模板', 'type': 1, 'style': 1}
            ])
        ])
        event.reply(content, buttons) 