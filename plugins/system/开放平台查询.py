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

# 禁用SSL警告
import warnings
warnings.filterwarnings('ignore', message='Unverified HTTPS request')

BASE = 'https://api.elaina.vin/api/bot'
LOGIN_URL = f"{BASE}/get_login.php"
GET_LOGIN = f"{BASE}/robot.php"
MESSAGE = f"{BASE}/message.php"
BOTLIST = f"{BASE}/bot_list.php"
BOTDATA = f"{BASE}/bot_data.php"
MSGTPL = f"{BASE}/md.php"
# 修改数据存储目录到当前目录下的data/bot文件夹
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'bot')
FILE = os.path.join(DATA_DIR, 'robot.json')
# 添加openapi.json文件路径 - 修正为项目根目录下的data/openapi.json
OPENAPI_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'openapi.json')
# 从config获取主人ID
SPECIAL_USER_ID = config.OWNER_IDS[0] if config.OWNER_IDS else ""

_user_data = {}
_login_tasks = {}
_last_login_success = {}
# 确保目录存在
os.makedirs(DATA_DIR, exist_ok=True)

def load_user_data():
    global _user_data
    # 重新初始化
    _user_data = {}
    
    # 加载普通用户数据
    if os.path.exists(FILE):
        try:
            with open(FILE, 'r', encoding='utf-8') as f:
                _user_data = json.load(f)
        except Exception as e:
            print(f"加载robot.json失败: {str(e)}")
            _user_data = {}
    
    # 为特定用户加载openapi.json数据
    print(f"检查openapi.json路径: {OPENAPI_FILE}")
    if os.path.exists(OPENAPI_FILE):
        try:
            with open(OPENAPI_FILE, 'r', encoding='utf-8') as f:
                openapi_data = json.load(f)
                print(f"成功读取openapi.json: {openapi_data}")
                web_user_data = openapi_data.get('web_user', {})
                if web_user_data and web_user_data.get('type') == 'ok':
                    _user_data[SPECIAL_USER_ID] = web_user_data
                    print(f"成功加载特定用户数据: {SPECIAL_USER_ID}")
                    print(f"加载的数据: {web_user_data}")
                else:
                    print(f"openapi.json中没有有效的web_user数据: {web_user_data}")
        except Exception as e:
            print(f"加载openapi.json失败: {str(e)}")
    else:
        print(f"openapi.json文件不存在: {OPENAPI_FILE}")
    
    print(f"最终_user_data: {list(_user_data.keys())}")

def ensure_user_data_loaded():
    """确保用户数据已加载，如果没有则加载"""
    global _user_data
    if not _user_data:  # 如果数据为空，则加载
        load_user_data()

def save_user_data(user_id, data):
    """保存用户数据，特定用户保存到openapi.json"""
    global _user_data
    _user_data[user_id] = data
    
    if user_id == SPECIAL_USER_ID:
        # 特定用户保存到openapi.json
        try:
            openapi_data = {}
            if os.path.exists(OPENAPI_FILE):
                with open(OPENAPI_FILE, 'r', encoding='utf-8') as f:
                    openapi_data = json.load(f)
            
            openapi_data['web_user'] = data
            with open(OPENAPI_FILE, 'w', encoding='utf-8') as f:
                json.dump(openapi_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存到openapi.json失败: {str(e)}")
    else:
        # 普通用户保存到robot.json
        with open(FILE, 'w', encoding='utf-8') as f:
            json.dump(_user_data, f, indent=2)

def create_ssl_session():
    return requests.Session()

# 获取字体文件路径
def get_font_path(font_name="msyh.ttc"):
    # 中文字体优先级列表
    chinese_fonts = [
        "msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc", "simkai.ttf", 
        "simfang.ttf", "STHeiti-Light.ttc", "STHeiti-Medium.ttc", "STFangsong.ttf"
    ]
    
    # 常见的字体路径
    font_dirs = [
        os.path.join(os.environ.get('WINDIR', ''), 'Fonts'),  # Windows
        "/usr/share/fonts/truetype/", "/usr/share/fonts/", # Linux
        "/Library/Fonts/", "/System/Library/Fonts/",  # macOS
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")  # 当前目录下的fonts文件夹
    ]
    
    # 首先尝试提供的字体名
    for font_dir in font_dirs:
        path = os.path.join(font_dir, font_name)
        if os.path.exists(path):
            return path
    
    # 然后尝试其他中文字体
    for font in chinese_fonts:
        for font_dir in font_dirs:
            path = os.path.join(font_dir, font)
            if os.path.exists(path):
                return path
    
    # 尝试查找系统上可能存在的其他中文字体
    for font_dir in font_dirs:
        if os.path.exists(font_dir):
            try:
                for file in os.listdir(font_dir):
                    if file.lower().endswith(('.ttc', '.ttf', '.otf')):
                        return os.path.join(font_dir, file)
            except:
                pass
    
    # 如果所有尝试都失败，返回None
    return None

# 绘制圆角矩形
def draw_rounded_rectangle(draw, xy, radius, fill=None, outline=None, width=1):
    x1, y1, x2, y2 = xy
    diameter = radius * 2
    
    # 绘制矩形主体
    draw.rectangle((x1 + radius, y1, x2 - radius, y2), fill=fill, outline=None)
    draw.rectangle((x1, y1 + radius, x2, y2 - radius), fill=fill, outline=None)
    
    # 绘制四个角落
    draw.pieslice((x1, y1, x1 + diameter, y1 + diameter), 180, 270, fill=fill, outline=None)
    draw.pieslice((x2 - diameter, y1, x2, y1 + diameter), 270, 360, fill=fill, outline=None)
    draw.pieslice((x1, y2 - diameter, x1 + diameter, y2), 90, 180, fill=fill, outline=None)
    draw.pieslice((x2 - diameter, y2 - diameter, x2, y2), 0, 90, fill=fill, outline=None)
    
    # 如果需要边框
    if outline:
        # 绘制边框的直线部分
        draw.line((x1 + radius, y1, x2 - radius, y1), fill=outline, width=width)  # 上
        draw.line((x1 + radius, y2, x2 - radius, y2), fill=outline, width=width)  # 下
        draw.line((x1, y1 + radius, x1, y2 - radius), fill=outline, width=width)  # 左
        draw.line((x2, y1 + radius, x2, y2 - radius), fill=outline, width=width)  # 右
        
        # 绘制边框的圆弧部分
        draw.arc((x1, y1, x1 + diameter, y1 + diameter), 180, 270, fill=outline, width=width)
        draw.arc((x2 - diameter, y1, x2, y1 + diameter), 270, 360, fill=outline, width=width)
        draw.arc((x1, y2 - diameter, x1 + diameter, y2), 90, 180, fill=outline, width=width)
        draw.arc((x2 - diameter, y2 - diameter, x2, y2), 0, 90, fill=outline, width=width)

# 获取状态颜色
def get_status_color(status):
    status_colors = {
        "审核通过": (82, 196, 26),  # 绿色
        "未通过": (245, 34, 45),    # 红色
        "审核中": (24, 144, 255),   # 蓝色
        "未提审": (250, 173, 20)    # 橙色
    }
    return status_colors.get(status, (51, 51, 51))  # 默认深灰色

# 文本换行函数
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

# 添加按钮样式渲染函数
def draw_button(draw, x, y, width, height, label, style, action_type, font):
    """
    绘制模板按钮
    
    参数:
    - draw: ImageDraw对象
    - x, y: 按钮左上角坐标
    - width, height: 按钮宽高
    - label: 按钮文本
    - style: 按钮样式(0-4)
    - action_type: 按钮类型(0=跳转,1=回调,2=回车)
    - font: 字体
    """
    # 设置不同样式的颜色
    style_colors = {
        0: {"bg": (255, 255, 255, 0), "border": (0, 0, 0, 80), "text": (0, 0, 0)},     # 透明背景浅黑框黑字
        1: {"bg": (255, 255, 255, 0), "border": (24, 144, 255), "text": (24, 144, 255)},  # 蓝框蓝字透明背景
        2: {"bg": (255, 255, 255, 0), "border": (0, 0, 0, 80), "text": (0, 0, 0)},     # 浅黑透明背景黑字
        3: {"bg": (255, 255, 255, 0), "border": (0, 0, 0, 80), "text": (245, 34, 45)}, # 透明背景浅黑框红字
        4: {"bg": (24, 144, 255), "border": (24, 144, 255), "text": (255, 255, 255)}, # 蓝框蓝背景白字
    }
    
    # 获取当前样式的颜色，如果style超过4，则使用style=0的样式
    colors = style_colors.get(style if 0 <= style <= 4 else 0)
    
    # 绘制按钮背景
    draw_rounded_rectangle(draw, (x, y, x+width, y+height), 6, fill=colors["bg"], outline=colors["border"], width=1)
    
    # 计算文本位置，使其居中
    text_width = font.getbbox(label)[2]
    text_height = font.getbbox(label)[3]
    text_x = x + (width - text_width) // 2
    text_y = y + (height - text_height) // 2 - 2  # 稍微上移一点以视觉居中
    
    # 绘制文本
    draw.text((text_x, text_y), label, fill=colors["text"], font=font)
    
    # 绘制按钮类型图标
    icon_size = 18  # 增大图标尺寸
    if action_type == 0:  # 跳转网站
        # 绘制简单的链接图标
        link_x = x + width - icon_size - 5
        link_y = y + height - icon_size - 5
        draw.rectangle((link_x, link_y, link_x+icon_size, link_y+icon_size), fill=None, outline=colors["text"], width=1)
        draw.line((link_x+icon_size//2, link_y+icon_size//2, link_x+icon_size, link_y), fill=colors["text"], width=1)
    elif action_type == 1:  # 回调按钮
        # 绘制简单的加载图标
        load_x = x + width - icon_size - 5
        load_y = y + height - icon_size - 5
        draw.arc((load_x, load_y, load_x+icon_size, load_y+icon_size), 0, 270, fill=colors["text"], width=1)

# 修改render_template_detail函数，添加按钮模板的渲染逻辑
def render_template_detail(data):
    # 定义颜色方案
    colors = {
        "background": (245, 246, 250),       # 背景色
        "card": (255, 255, 255),             # 卡片背景
        "primary": (24, 144, 255),           # 主色调
        "secondary": (67, 74, 100),          # 次要文本
        "text": (51, 51, 51),                # 文本颜色
        "light_text": (102, 102, 102),       # 浅色文本
        "border": (240, 240, 240),           # 边框颜色
        "info": (24, 144, 255),              # 信息蓝
        "success": (82, 196, 26),            # 成功绿
        "warning": (250, 173, 20),           # 警告黄
        "error": (245, 34, 45),              # 错误红
        "header_bg": (240, 248, 255)         # 标题背景
    }
    
    # 检查是否为按钮模板
    template = data.get('template', {})
    is_button_template = template.get('模板类型') == '按钮模板'
    
    # 根据模板类型设置尺寸
    if is_button_template:
        # 按钮模板使用更大的尺寸
        width = 1800  # 增加50%
        height = 1400  # 增加100%，给按钮预览更多空间
        padding = 40
    else:
        # 普通模板使用原来的尺寸
        width = 1200
        height = 900
        padding = 30
        
    border_radius = 20
    inner_padding = 25
    
    # 创建画布
    image = Image.new('RGB', (width, height), colors["background"])
    draw = ImageDraw.Draw(image)
    
    # 创建顶部条纹
    stripe_height = 8
    draw.rectangle((0, 0, width, stripe_height), fill=colors["primary"])
    
    # 尝试使用微软雅黑字体
    msyh_font_paths = [
        "/usr/share/fonts/msyh/msyh.ttc",      # 微软雅黑 Regular
        "/usr/share/fonts/msyh/msyhbd.ttc",    # 微软雅黑 Bold
        "/usr/share/fonts/msyh/msyhl.ttc"      # 微软雅黑 Light
    ]
    
    # 尝试加载字体，如果失败则使用备选字体
    try:
        # 尝试加载微软雅黑字体
        regular_font = bold_font = light_font = None
        
        # 检查每个路径是否存在
        for path in msyh_font_paths:
            if os.path.exists(path):
                if "msyh.ttc" in path and not regular_font:
                    regular_font = path
                elif "msyhbd.ttc" in path and not bold_font:
                    bold_font = path
                elif "msyhl.ttc" in path and not light_font:
                    light_font = path
        
        # 根据可用字体设置字体对象
        if regular_font:
            title_font = ImageFont.truetype(bold_font or regular_font, 48)
            header_font = ImageFont.truetype(bold_font or regular_font, 36)
            normal_font = ImageFont.truetype(regular_font, 30)
            bold_font = ImageFont.truetype(bold_font or regular_font, 32)
            small_font = ImageFont.truetype(regular_font, 24)
            button_font = ImageFont.truetype(regular_font, is_button_template and 28 or 22)  # 按钮模板时使用更大字号
        else:
            # 如果找不到微软雅黑，尝试使用系统默认字体
            title_font = ImageFont.load_default().font_variant(size=48)
            header_font = ImageFont.load_default().font_variant(size=36)
            normal_font = ImageFont.load_default().font_variant(size=30)
            bold_font = ImageFont.load_default().font_variant(size=32)
            small_font = ImageFont.load_default().font_variant(size=24)
            button_font = ImageFont.load_default().font_variant(size=22)
    except Exception as e:
        print(f"字体加载错误: {str(e)}")
        title_font = ImageFont.load_default().font_variant(size=48)
        header_font = ImageFont.load_default().font_variant(size=36)
        normal_font = ImageFont.load_default().font_variant(size=30)
        bold_font = ImageFont.load_default().font_variant(size=32)
        small_font = ImageFont.load_default().font_variant(size=24)
        button_font = ImageFont.load_default().font_variant(size=22)
    
    # 安全绘制函数，确保坐标有效
    def safe_draw_rectangle(draw, xy, radius=0, **kwargs):
        x1, y1, x2, y2 = xy
        # 确保坐标正确，x2 > x1, y2 > y1
        if x2 <= x1:
            x1, x2 = x2, x1
        if y2 <= y1:
            y1, y2 = y2, y1
            
        if radius > 0:
            draw_rounded_rectangle(draw, (x1, y1, x2, y2), radius, **kwargs)
        else:
            draw.rectangle((x1, y1, x2, y2), **kwargs)
    
    # 绘制主卡片背景
    main_card = (padding, padding, width-padding, height-padding)
    safe_draw_rectangle(draw, main_card, border_radius, fill=colors["card"])
    
    # 绘制标题区域
    title_y = padding + 20
    title_bg = (padding+20, title_y, width-padding-20, title_y+80)
    safe_draw_rectangle(draw, title_bg, border_radius//2, fill=colors["header_bg"])
    
    # 绘制标题文本
    title_text = "Bot模板详情"
    title_width = title_font.getbbox(title_text)[2]
    title_x = (width - title_width) // 2
    draw.text((title_x, title_y+12), title_text, fill=colors["primary"], font=title_font)
    
    # 计算下一个元素的起始位置
    current_y = title_y + 120
    
    # 绘制账号和AppId信息栏
    info_bg = (padding+20, current_y, width-padding-20, current_y+80)
    safe_draw_rectangle(draw, info_bg, border_radius//2, fill=colors["background"])
    
    # 绘制左侧账号信息
    draw.text((padding+40, current_y+10), "账号", fill=colors["secondary"], font=small_font)
    draw.text((padding+40, current_y+40), data.get('uin', ''), fill=colors["text"], font=normal_font)
    
    # 绘制右侧AppId信息
    draw.text((width//2, current_y+10), "AppID", fill=colors["secondary"], font=small_font)
    draw.text((width//2, current_y+40), data.get('appid', ''), fill=colors["text"], font=normal_font)
    
    # 更新当前Y坐标
    current_y += 100
    
    # 绘制模板信息部分
    template = data.get('template', {})
    info_items = [
        {"label": "模板ID", "value": template.get('模板id', '')},
        {"label": "模板名称", "value": template.get('模板名称', '')},
        {"label": "模板类型", "value": template.get('模板类型', '')},
        {"label": "审核状态", "value": template.get('模板状态', '')}
    ]
    
    # 绘制模板信息标题
    draw.text((padding+20, current_y), "模板信息", fill=colors["primary"], font=header_font)
    current_y += 50
    
    # 绘制每个模板信息项
    for item in info_items:
        # 设置每一行的高度
        row_height = 60
        
        # 绘制信息项背景
        info_bg = (padding+20, current_y, width-padding-20, current_y+row_height)
        safe_draw_rectangle(draw, info_bg, border_radius//2, fill=colors["background"])
        
        # 绘制标签和值
        label_width = 160
        
        # 绘制标签文本
        text_y = current_y + (row_height - bold_font.getbbox(item["label"])[3]) // 2
        draw.text((padding+40, text_y), item["label"], fill=colors["secondary"], font=bold_font)
        
        # 绘制值文本
        value_x = padding + label_width + 40
        value_y = current_y + (row_height - normal_font.getbbox(item["value"])[3]) // 2
        
        # 为审核状态设置特殊颜色
        if item["label"] == "审核状态":
            status_color = get_status_color(item["value"])
            draw.text((value_x, value_y), item["value"], fill=status_color, font=normal_font)
        else:
            draw.text((value_x, value_y), item["value"], fill=colors["text"], font=normal_font)
        
        current_y += row_height + 10
    
    # 绘制模板内容区域
    current_y += 20
    draw.text((padding+20, current_y), "模板内容", fill=colors["primary"], font=header_font)
    current_y += 50
    
    # 计算剩余空间
    remaining_height = height - current_y - padding - 40
    
    # 内容背景
    content_bg = (padding+20, current_y, width-padding-20, height-padding-20)
    safe_draw_rectangle(draw, content_bg, border_radius//2, fill=colors["background"])
    
    # 模板内容文本处理
    content_text = template.get('模板内容', '')
    
    # 检查模板类型是否为按钮模板
    is_button_template = template.get('模板类型') == '按钮模板'
    button_data = None
    
    if is_button_template:
        try:
            # 尝试直接解析按钮模板的JSON数据
            button_data = json.loads(content_text)
        except Exception as e:
            print(f"按钮模板解析失败: {str(e)}")
            is_button_template = False
            
    # 如果是从@开头的文本解析（兼容之前的方式）
    elif content_text.startswith('@'):
        try:
            is_button_template = True
            # 去掉@前缀
            json_text = content_text[1:].strip()
            button_data = json.loads(json_text)
        except Exception as e:
            print(f"按钮模板解析失败: {str(e)}")
            is_button_template = False
    
    if is_button_template and button_data:
        # 绘制按钮模板预览
        draw.text((padding+40, current_y+20), "按钮模板预览:", fill=colors["secondary"], font=bold_font)
        btn_start_y = current_y + 70
        
        try:
            # 获取按钮行数据
            rows = button_data.get('rows', [])
            
            # 每行按钮的最大数量和大小
            max_buttons_per_row = 5
            button_width = 300  # 增大按钮宽度
            button_height = 80  # 增大按钮高度
            button_margin = 20  # 增大按钮间距
            
            # 绘制每一行的按钮
            current_btn_y = btn_start_y
            for row_idx, row in enumerate(rows):
                if row_idx >= 5:  # 最多显示5行
                    break
                
                buttons = row.get('buttons', [])
                btn_count = min(len(buttons), max_buttons_per_row)
                
                if btn_count == 0:
                    continue
                
                # 计算可用的总宽度
                available_width = width - padding * 2 - 80  # 左右各减去padding和一些额外边距
                
                # 计算每个按钮的宽度（均分整行宽度）
                each_button_width = (available_width - (btn_count - 1) * button_margin) // btn_count
                
                # 计算这一行的起始x坐标
                start_x = padding + 40
                
                # 绘制这一行的按钮
                for i in range(btn_count):
                    btn = buttons[i]
                    btn_x = start_x + i * (each_button_width + button_margin)
                    
                    # 获取按钮属性
                    render_data = btn.get('render_data', {})
                    action = btn.get('action', {})
                    
                    label = render_data.get('label', 'Button')
                    style = render_data.get('style', 0)
                    action_type = action.get('type', 2)
                    
                    # 绘制按钮
                    draw_button(draw, btn_x, current_btn_y, each_button_width, button_height, 
                                label, style, action_type, button_font)
                
                # 更新下一行的y坐标
                current_btn_y += button_height + 20
                
            # 绘制按钮模板说明
            descriptions = [
                "按钮仅供参考，实际效果以实际为准",
                "类型: 0=跳转链接(链接图标), 1=回调(加载图标), 2=回车命令"
            ]
            
            note_y = current_btn_y + 20
            for desc in descriptions:
                draw.text((padding+40, note_y), desc, fill=colors["light_text"], font=small_font)
                note_y += 30
        
        except Exception as e:
            # 如果渲染按钮出错，显示错误信息
            draw.text((padding+40, btn_start_y), f"按钮渲染失败: {str(e)}", 
                      fill=colors["error"], font=normal_font)
            
            # 显示原始JSON内容
            lines = wrap_text(json_text, normal_font, width-padding*3)
            line_height = 40
            max_lines = min((height - btn_start_y - 100) // line_height, 10)
            
            for i, line in enumerate(lines[:max_lines]):
                draw.text((padding+40, btn_start_y + 40 + i * line_height), 
                          line, fill=colors["text"], font=normal_font)
    else:
        # 渲染普通文本内容
        lines = wrap_text(content_text, normal_font, width-padding*3)
        
        # 计算内容文本的行高和可显示行数
        line_height = 40  # 固定行高，避免计算错误
        max_lines = remaining_height // line_height
        if max_lines <= 0:  # 确保至少显示一行
            max_lines = 1
        display_lines = lines[:max_lines]
        
        # 绘制内容文本
        for i, line in enumerate(display_lines):
            line_y = current_y + 20 + i * line_height
            draw.text((padding+40, line_y), line, fill=colors["text"], font=normal_font)
        
        # 添加底部装饰线和提示文本
        if len(lines) > max_lines:
            note = f"(内容过长，仅显示前{max_lines}行)"
            draw.text((padding+20, height-padding-30), note, fill=colors["light_text"], font=small_font)
    
    # 直接保存原始画质的图片
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG', quality=100, optimize=True)
    img_byte_arr.seek(0)
    
    return img_byte_arr

# 延迟加载用户数据，避免启动时的循环导入问题
# load_user_data() 将在首次使用时调用

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
        """验证登录凭证是否仍然有效"""
        try:
            print(f"开始验证凭证: uin={login_data.get('uin')}, ticket={login_data.get('ticket')[:10]}...")
            session = create_ssl_session()
            # 使用获取机器人列表的API来验证凭证
            url = f"{BOTLIST}?uin={login_data.get('uin')}&ticket={login_data.get('ticket')}&developerId={login_data.get('developerId')}"
            response = session.get(url, verify=False, timeout=10)
            res = response.json()
            print(f"验证凭证API响应: {res}")
            # 如果返回code为0，说明凭证有效
            is_valid = res.get('code') == 0
            print(f"凭证验证结果: {is_valid}")
            return is_valid
        except Exception as e:
            print(f"验证凭证时出错: {str(e)}")
            return False

    @staticmethod
    def login(event):
        if event.event_type == "INTERACTION_CREATE" and not event.content.strip() == "管理登录":
            return
        global _login_tasks, _last_login_success
        user_id = event.user_id
        current_time = time.time()
        
        # 检查是否为特定用户
        if user_id == SPECIAL_USER_ID:
            # 特定用户先验证现有凭证是否有效
            ensure_user_data_loaded()
            if user_id in _user_data and _user_data[user_id].get('type') == 'ok':
                login_data = _user_data[user_id]
                
                # 验证凭证是否有效
                if robot_data_plugin._verify_credentials(login_data):
                    # 凭证有效，直接显示登录成功
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
                    # 凭证失效，显示提示并继续正常登录流程
                    event.reply("管理员凭证已失效，正在重新获取登录二维码...")
                    # 不return，继续执行下面的正常登录流程
            else:
                event.reply("管理员数据未找到，正在获取登录二维码...")
                # 不return，继续执行下面的正常登录流程
        
        # 普通用户的登录逻辑
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
        ensure_user_data_loaded()
        if user not in _user_data:
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
        response = session.get(url, verify=False)
        res = response.json()
        if res.get('code') != 0:
            content = f'<@{user}>登录状态失效'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
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
        ensure_user_data_loaded()
        if user not in _user_data:
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
        ensure_user_data_loaded()
        if user not in _user_data:
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
        ensure_user_data_loaded()
        if user not in _user_data:
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
        ensure_user_data_loaded()
        if user not in _user_data:
            content = f'<@{user}> 未查询到你的登录信息'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
            
        # 提取模板ID
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
        
        # 查找指定ID的模板
        target_template = None
        current_index = -1  # 记录当前模板的索引
        
        # 检查是否是简化指令（纯数字，表示索引）
        if tpl_id.isdigit() and 1 <= int(tpl_id) <= len(templates):
            # 简化指令模式，直接按索引获取模板
            current_index = int(tpl_id) - 1  # 转为0-based索引
            target_template = templates[current_index]
        else:
            # 传统模式，按模板ID查找
            for i, template in enumerate(templates):
                if template.get('模板id') == tpl_id:
                    target_template = template
                    current_index = i  # 记录找到的模板索引
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
            # 渲染模板详情图片
            img_bytes = render_template_detail(render_data)
            
            # 改用图床上传图片
            uploaded_url = event.uploadToQQImageBed(img_bytes.getvalue())
            if uploaded_url:
                # 准备消息内容，包含图片
                content = f"![模板详情 #1200px #900px]({uploaded_url})\n模板详情 ({current_index + 1}/{len(templates)})"
                
                # 准备按钮，分两行
                first_row_buttons = [
                    {'text': '模板列表', 'data': 'bot模板', 'type': 1, 'style': 1},
                    {'text': '查询', 'data': 'bot模板', 'type': 2, 'style': 1}
                ]
                
                second_row_buttons = []
                # 上一个按钮（如果不是第一个模板）
                if current_index > 0:
                    prev_index = current_index
                    second_row_buttons.append({'text': '上一个', 'data': f'bot模板{prev_index}', 'type': 1, 'style': 1})
                
                # 下一个按钮（如果不是最后一个模板）
                if current_index < len(templates) - 1:
                    next_index = current_index + 2  # 转为1-based索引并指向下一个
                    second_row_buttons.append({'text': '下一个', 'data': f'bot模板{next_index}', 'type': 1, 'style': 1})
                
                # 如果第二行有按钮，则添加第二行
                button_rows = [event.rows(first_row_buttons)]
                if second_row_buttons:
                    button_rows.append(event.rows(second_row_buttons))
                
                # 构造按钮并发送消息
                buttons = event.button(button_rows)
                event.reply(content, buttons)
            else:
                event.reply("上传模板详情图片失败，请稍后重试")
        except Exception as e:
            event.reply(f"生成模板详情图片时出错: {str(e)}")

    @staticmethod
    def switch_appid(event):
        user = event.user_id
        ensure_user_data_loaded()
        if user not in _user_data:
            content = f'<@{user}> 未查询到你的登录信息'
            buttons = event.button([
                event.rows([{'text': '登录', 'data': '管理登录', 'type': 1, 'style': 1}])
            ])
            event.reply(content, buttons)
            return
            
        # 提取用户输入的appId
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
        
        # 检查输入的AppID是否在列表中
        for app in apps:
            if app.get('app_id') == new_appid:
                valid_appid = True
                app_name = app.get('app_name', '未命名机器人')
                break
        
        if not valid_appid:
            # 显示可用的AppID列表
            available_apps = []
            for i, app in enumerate(apps, 1):
                available_apps.append(f"{i}. {app.get('app_name', '未命名')}: {app.get('app_id')}")
            
            available_text = "\n".join(available_apps)
            event.reply(f"提供的AppID无效，请从以下可用AppID中选择：\n\n```python\n{available_text}\n```\n")
            return
        
        # AppID验证通过，更新配置
        old_appid = data.get('appId')
        data['appId'] = new_appid
        
        # 保存到相应的JSON文件
        save_user_data(user, data)
        
        # 返回切换成功的消息
        content = f"AppID已切换成功\n\n```python\n原AppID: {old_appid}\n新AppID: {new_appid}\n机器人: {app_name}\n```\n"
        buttons = event.button([
            event.rows([
                {'text': '通知', 'data': 'bot通知', 'type': 1, 'style': 1},
                {'text': '数据', 'data': 'bot数据4', 'type': 2, 'style': 1},
                {'text': '模板', 'data': 'bot模板', 'type': 1, 'style': 1}
            ])
        ])
        event.reply(content, buttons) 