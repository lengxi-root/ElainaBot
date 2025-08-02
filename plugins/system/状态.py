#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import platform
import time
import datetime
import tempfile
import psutil
import asyncio
import subprocess
import re
from core.plugin.PluginManager import Plugin
from function.db_pool import DatabaseService
from function.log_db import LogDatabasePool
from function.httpx_pool import sync_get
from PIL import Image

# 主题模式配置（auto/day/night）
THEME_MODE = 'auto'  # 可选: 'auto', 'day', 'night'

# 背景图片
BACKGROUND_IMAGES = {
    'day': 'https://gd-hbimg.huaban.com/2a2d0eb13a3fa113d12272e08fdcc088a3fd70bb4b37c-JSzKNz',
    'night': 'https://gd-hbimg.huaban.com/2a2d0eb13a3fa113d12272e08fdcc088a3fd70bb4b37c-JSzKNz'
}

# 网络测速目标
SPEED_TEST_URLS = {
    "Edgeone": "https://i.elaina.vin",
    "腾讯云": "https://cloud.tencent.com",
    "百度": "https://www.baidu.com",
    "网易": "https://www.163.com",
}

# 添加从专属测试工具插件复制的render_website_to_image函数
async def render_website_to_image(website_url, output_path):
    """使用Playwright将网站渲染为图片"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError("未安装playwright库，请先安装: pip install playwright")
    
    # 注意：此函数保持异步，因为Playwright API是异步的
    # 网络请求已在test_network_speed函数中改为使用httpx_pool的sync_get
    
    async with async_playwright() as p:
        # 启动浏览器，增加超时设置
        browser = await p.chromium.launch(timeout=60000)  # 60秒超时
        
        # 创建新页面
        page = await browser.new_page(viewport={"width": 1200, "height": 800})
        
        # 导航到网站URL
        await page.goto(website_url, wait_until="networkidle")
        
        # 等待JavaScript执行完毕
        await page.wait_for_timeout(2000)  # 额外等待2秒确保JS执行完成
        
        # 截图
        await page.screenshot(path=output_path, full_page=True)
        
        # 关闭浏览器
        await browser.close()

class system_plugin(Plugin):
    priority = 10

    @staticmethod
    def get_regex_handlers():
        return {
            r'^状态$': {
                'handler': 'status',
                'owner_only': True
            }
        }

    @staticmethod
    def status(event):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            # 1. 获取DAU数据
            dau_data = loop.run_until_complete(get_dau_stats())
            # 2. 获取用户统计
            user_stats = get_user_stats()
            # 3. 获取系统信息
            sys_info = get_system_info()
            # 4. 网络测速
            speed_test_html = loop.run_until_complete(test_network_speed())
            # 5. 进程、磁盘、内存、CPU详细
            left_html = generate_left_column(sys_info, speed_test_html)
            # 6. 中间列（DAU/消息统计）
            middle_html = generate_middle_column(dau_data)
            # 7. 右侧（用户统计/插件加载用时）
            right_html = generate_right_column(user_stats)
            # 8. 主题
            theme = get_current_theme()
            # 9. 顶部卡片
            header_html = generate_header_card(theme)
            # 10. 组装HTML
            html = render_full_html(header_html, left_html, middle_html, right_html, theme)
            # 11. 渲染为图片并上传
            img_url = render_and_upload(event, html, loop)
            event.reply(img_url, hide_avatar_and_center=True)
        except Exception as e:
            event.reply(f"状态统计生成失败：{str(e)}")

# 主题切换
def get_current_theme():
    if THEME_MODE == 'day':
        return 'day'
    if THEME_MODE == 'night':
        return 'night'
    # 自动模式：北京时间6-18点为白天
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    hour = now.hour
    return 'day' if 6 <= hour < 18 else 'night'

def generate_header_card(theme):
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    version = 'v1.0.0'  # 可自定义
    return f'''
    <div class="header-card">
      <div class="header">
        <h1 class="title">Mbot 状态统计</h1>
        <div class="version">版本 {version}</div>
        <div class="version">生成时间: {now}</div>
      </div>
    </div>
    '''

async def test_network_speed():
    results = []
    for name, url in SPEED_TEST_URLS.items():
        try:
            start = time.time()
            sync_get(url, timeout=3)
            latency = int((time.time() - start) * 1000)
            if latency > 500:
                ping_class = 'ping-bad'
            elif latency > 200:
                ping_class = 'ping-medium'
            else:
                ping_class = 'ping-good'
            results.append(f'<div class="speed-test-item"><div class="speed-test-name">{name}</div><div class="speed-test-ping {ping_class}">{latency}ms</div></div>')
        except Exception:
            results.append(f'<div class="speed-test-item"><div class="speed-test-name">{name}</div><div class="speed-test-ping ping-bad">超时</div></div>')
    return ''.join(results)

def generate_left_column(sys_info, speed_test_html):
    # CPU详细
    cpu_percent = sys_info['cpu_percent']
    cpu_cores = psutil.cpu_percent(percpu=True)
    cpu_cores_html = ''
    for idx, percent in enumerate(cpu_cores):
        bar_count = int(percent // 2)
        cpu_cores_html += f'''<div class="cpu-core-item"><div class="cpu-core-label">核心 {idx+1}:</div><div class="cpu-core-bars">{''.join(['<div class=\"cpu-core-bar active\"></div>' if i < bar_count else '<div class=\"cpu-core-bar\"></div>' for i in range(50)])}</div><div class="cpu-core-percent">{percent}%</div></div>'''
    # 内存
    mem = sys_info['mem']
    mem_html = f'''<div class="usage-item"><span class="usage-label">内存使用:</span><span class="usage-value">{format_bytes(mem.used)} / {format_bytes(mem.total)} ({mem.percent}%)</span></div><div class="usage-progress"><div class="usage-progress-bar mem-progress" style="width: {mem.percent}%"></div></div>'''
    # 磁盘
    disk = sys_info['disk']
    disk_html = f'''<div class="usage-item"><span class="usage-label">磁盘:</span><span class="usage-value">{format_bytes(disk.used)} / {format_bytes(disk.total)} ({disk.percent}%)</span></div><div class="usage-progress"><div class="usage-progress-bar disk-progress" style="width: {disk.percent}%"></div></div>'''
    # 进程
    process_count = sys_info['process_count']
    left = f'''
    <div class="card"><h2 class="card-title">系统基本信息</h2><div class="sysinfo-card"><div class="sysinfo-item"><span class="sysinfo-label">主机名:</span><span class="sysinfo-value">{sys_info['hostname']}</span></div><div class="sysinfo-item"><span class="sysinfo-label">系统版本:</span><span class="sysinfo-value">{sys_info['os']}</span></div><div class="sysinfo-item"><span class="sysinfo-label">内核版本:</span><span class="sysinfo-value">{sys_info['kernel']}</span></div><div class="sysinfo-item"><span class="sysinfo-label">运行时间:</span><span class="sysinfo-value">{sys_info['uptime']}</span></div><div class="sysinfo-item"><span class="sysinfo-label">当前用户:</span><span class="sysinfo-value">{sys_info['user']}</span></div></div></div>
    <div class="card"><h2 class="card-title">CPU 使用率</h2><div class="cpu-core-container">{cpu_cores_html}</div><div class="usage-item"><span class="usage-label">平均使用率:</span><span class="usage-value">{cpu_percent}%</span></div></div>
    <div class="card"><h2 class="card-title">内存使用</h2>{mem_html}</div>
    <div class="card"><h2 class="card-title">磁盘使用</h2>{disk_html}</div>
    <div class="card"><h2 class="card-title">进程数</h2><div class="usage-item"><span class="usage-label">进程数:</span><span class="usage-value">{process_count}</span></div></div>
    <div class="card"><h2 class="card-title">网络延迟测速</h2><div class="speed-test-container">{speed_test_html}</div></div>
    '''
    return left

def generate_middle_column(dau_data_list):
    html = ''
    for dau_data in dau_data_list:
        # 只取前4个且过滤id为null的
        top_groups = [g for g in dau_data['top_groups'] if g['id'] and g['id'] != 'null'][:4]
        top_users = [u for u in dau_data['top_users'] if u['id'] and u['id'] != 'null'][:4]
        # 合并活跃用户/群聊、消息总数
        html += f'''
        <div class="card">
            <h2 class="card-title">📊 {dau_data['date']} <span style='font-size:13px;color:#888;'>🕒{dau_data['query_time']}ms</span> 活跃统计</h2>
            <div class="dau-stats">
                <div class="dau-stat-item">👤 活跃用户: {dau_data['active_users']}　｜　👥 活跃群聊: {dau_data['active_groups']}</div>
                <div class="dau-stat-item">💬 群聊消息: {dau_data['total_messages']}　｜　📱 私聊消息: {dau_data['private_messages']}</div>
                <div class="dau-stat-item">⏰ 最活跃时段: {dau_data['peak_hour']}点 ({dau_data['peak_hour_count']}条消息)</div>
                <div class="dau-section">
                    <div class="dau-section-title">🔝 最活跃群组:</div>
                    <div class="dau-list">
                        <div style='display:flex;gap:18px;'>
                            <div class="dau-top-item" style='flex:1'>{top_groups[0]['id']} ({top_groups[0]['count']}条)</div>
                            <div class="dau-top-item" style='flex:1'>{top_groups[1]['id']} ({top_groups[1]['count']}条)</div>
                        </div>
                        <div style='display:flex;gap:18px;'>
                            <div class="dau-top-item" style='flex:1'>{top_groups[2]['id']} ({top_groups[2]['count']}条)</div>
                            <div class="dau-top-item" style='flex:1'>{top_groups[3]['id']} ({top_groups[3]['count']}条)</div>
                        </div>
                    </div>
                </div>
                <div class="dau-section">
                    <div class="dau-section-title">👑 最活跃用户:</div>
                    <div class="dau-list">
                        <div style='display:flex;gap:18px;'>
                            <div class="dau-top-item" style='flex:1'>{top_users[0]['id']} ({top_users[0]['count']}条)</div>
                            <div class="dau-top-item" style='flex:1'>{top_users[1]['id']} ({top_users[1]['count']}条)</div>
                        </div>
                        <div style='display:flex;gap:18px;'>
                            <div class="dau-top-item" style='flex:1'>{top_users[2]['id']} ({top_users[2]['count']}条)</div>
                            <div class="dau-top-item" style='flex:1'>{top_users[3]['id']} ({top_users[3]['count']}条)</div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        ''' if len(top_groups) >= 4 and len(top_users) >= 4 else ''
    return html

def get_command_stats():
    # 获取最近3天的日期
    today = datetime.datetime.now()
    dates = [today - datetime.timedelta(days=i) for i in range(3)]
    date_strs = [d.strftime('%Y%m%d') for d in dates]
    
    # 常见非指令词过滤列表
    common_words = set(['你好', '谢谢', '感谢', '好的', '是的', '不是', '什么', '怎么', '为什么', 
                        '如何', '可以', '不可以', '行', '不行', '嗯', '啊', '哦', '呵呵', '哈哈',
                        '666', '？', '?', '。', '，', '.', ',', '!', '！'])
    
    log_db_pool = LogDatabasePool()
    connection = log_db_pool.get_connection()
    if not connection:
        return []
    
    cursor = None
    try:
        cursor = connection.cursor()
        
        # 统计指令使用情况
        command_counts = {}
        
        # 遍历最近3天的日志表
        for date_str in date_strs:
            table_name = f"Mlog_{date_str}_message"
            
            # 检查表是否存在
            check_query = f"""
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """
            cursor.execute(check_query, (table_name,))
            check = cursor.fetchone()
            if not check or check.get('count', 0) == 0:
                continue
            
            # 查询所有消息内容
            content_query = f"""
                SELECT content 
                FROM {table_name} 
                WHERE content IS NOT NULL AND content != ''
            """
            cursor.execute(content_query)
            contents = cursor.fetchall()
            
            # 统计每个指令的使用次数
            for row in contents:
                content = row.get('content', '')
                if not content:
                    continue
                
                # 去除开头的斜杠，并提取第一个词作为指令
                content = content.strip()
                if content.startswith('/'):
                    content = content[1:].strip()
                
                # 提取指令部分（第一个词）
                match = re.match(r'^(\S+)', content)
                if match:
                    command = match.group(1).lower()
                    # 过滤掉常见非指令词和太短的词
                    if command and command not in common_words and len(command) > 1:
                        command_counts[command] = command_counts.get(command, 0) + 1
        
        # 转换为列表并排序
        command_stats = [{'command': cmd, 'count': count} for cmd, count in command_counts.items()]
        command_stats.sort(key=lambda x: x['count'], reverse=True)
        
        return command_stats[:10]  # 返回前10个最常用的指令
    finally:
        if cursor:
            cursor.close()
        if connection:
            log_db_pool.release_connection(connection)

def generate_right_column(user_stats):
    # 用户统计卡片
    user_stats_html = f'''
    <div class="card"><h2 class="card-title">用户统计</h2>
    <div class="item">好友总数：{user_stats['private_users_count']}</div>
    <div class="item">群组总数：{user_stats['group_count']}</div>
    <div class="item">所有用户总数：{user_stats['user_count']}</div>
    <div class="item">最大群：{user_stats['most_active_group']}（{user_stats['most_active_group_members']}人）</div>
    </div>
    '''
    
    # 获取指令使用统计
    command_stats = get_command_stats()
    
    # 指令使用排行卡片
    command_items = ""
    for idx, stat in enumerate(command_stats):
        medal = ""
        if idx == 0:
            medal = "🥇"
        elif idx == 1:
            medal = "🥈"
        elif idx == 2:
            medal = "🥉"
        else:
            medal = f"{idx+1}."
            
        command_items += f'<div class="command-item">{medal} <span class="command-name">{stat["command"]}</span> <span class="command-count">({stat["count"]}次)</span></div>\n'
    
    command_stats_html = f'''
    <div class="card">
        <h2 class="card-title">📊 最近3天指令使用排行</h2>
        <div class="command-stats">
            {command_items if command_items else '<div class="no-data">暂无数据</div>'}
        </div>
    </div>
    '''
    
    # 组合所有卡片
    return user_stats_html + command_stats_html

def render_full_html(header_html, left_html, middle_html, right_html, theme):
    theme_styles = {
        'day': {
            'background': f"url('{BACKGROUND_IMAGES['day']}') center center / cover no-repeat",
            'overlay': 'rgba(255, 255, 255, 0.3)',
            'cardBg': 'rgba(255, 255, 255, 0.65)',
            'cardHoverBg': 'rgba(255, 255, 255, 0.2)',
            'textColor': '#5F5F5F',
            'secondaryText': 'rgba(80,80,80,0.7)',
            'borderColor': 'rgba(255,255,255,0.2)',
            'shadowColor': 'rgba(0, 0, 0, 0.36)',
            'titleShadow': '0 2px 4px rgba(0,0,0,0.5)',
            'progressBg': 'rgba(255,255,255,0.1)',
            'footerBg': 'rgba(255,255,255,0.3)'
        },
        'night': {
            'background': f"url('{BACKGROUND_IMAGES['night']}') center center / cover no-repeat",
            'overlay': 'rgba(0, 0, 0, 0.5)',
            'cardBg': 'rgba(0, 0, 0, 0.4)',
            'cardHoverBg': 'rgba(0, 0, 0, 0.5)',
            'textColor': '#e0e0e0',
            'secondaryText': 'rgba(224,224,224,0.7)',
            'borderColor': 'rgba(224,224,224,0.1)',
            'shadowColor': 'rgba(0, 0, 0, 0.5)',
            'titleShadow': '0 2px 4px rgba(0,0,0,0.7)',
            'progressBg': 'rgba(224,224,224,0.05)',
            'footerBg': 'rgba(0,0,0,0.5)'
        }
    }
    t = theme_styles[theme]
    html = f'''
    <!DOCTYPE html>
    <html><head><meta charset="UTF-8">
    <style>
    html, body {{ width: 100%; margin: 0; padding: 0; }}
    body {{ font-family: "Microsoft YaHei", sans-serif; color: {t['textColor']}; background: {t['background']}; min-height: 100vh; width: 100vw; position: relative; overflow-x: hidden; }}
    body::before {{ content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 100%; background: {t['overlay']}; z-index: -1; pointer-events: none; }}
    .header-card {{ background: {t['cardBg']}; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); padding: 18px; margin-bottom: 20px; max-width: 1400px; margin-left: auto; margin-right: auto; display: flex; align-items: center; justify-content: center; min-height: 80px; }}
    .title {{ color: {t['textColor']}; font-size: 24px; text-shadow: {t['titleShadow']}; }}
    .version {{ color: {t['secondaryText']}; font-size: 14px; text-shadow: 0 1px 2px rgba(0,0,0,0.5); }}
    .container {{ max-width: 1400px; margin: 0 auto; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; padding: 0; overflow: visible; }}
    .column {{ display: flex; flex-direction: column; gap: 15px; }}
    .card {{ background: {t['cardBg']}; border-radius: 10px; padding: 18px; transition: all 0.3s ease; border: 1px solid {t['borderColor']}; box-shadow: 0 8px 32px 0 {t['shadowColor']}; }}
    .card:hover {{ transform: translateY(-3px); box-shadow: 0 12px 40px 0 rgba(0, 0, 0, 0.45); background: {t['cardHoverBg']}; }}
    .card-title {{ color: {t['textColor']}; margin-bottom: 15px; font-size: 17px; border-bottom: 1px solid {t['borderColor']}; padding-bottom: 8px; }}
    .dau-stats {{ display: flex; flex-direction: column; gap: 8px; }}
    .dau-stat-item {{ font-size: 15px; margin-bottom: 6px; color: {t['textColor']}; }}
    .dau-section {{ margin-top: 12px; }}
    .dau-section-title {{ font-weight: bold; margin-bottom: 8px; color: {t['textColor']}; }}
    .dau-list {{ display: flex; flex-direction: column; gap: 6px; }}
    .dau-top-item {{ padding: 6px 0; border-bottom: 1px dashed {t['borderColor']}; color: {t['secondaryText']}; }}
    .dau-query-time {{ margin-top: 12px; font-size: 13px; color: {t['secondaryText']}; }}
    .cpu-core-item {{ display: flex; align-items: center; margin-bottom: 8px; }}
    .cpu-core-label {{ width: 80px; font-weight: bold; color: {t['secondaryText']}; font-size: 14px; }}
    .cpu-core-bars {{ display: flex; flex: 1; gap: 2px; }}
    .cpu-core-bar {{ height: 16px; width: 3px; background-color: rgba(255,255,255,0.2); position: relative; }}
    .cpu-core-bar.active {{ background-color: #64b5f6; }}
    .cpu-core-percent {{ margin-left: 10px; min-width: 40px; color: #ff8a65; font-size: 14px; }}
    .usage-item {{ margin-bottom: 10px; }}
    .usage-label {{ font-weight: bold; color: {t['secondaryText']}; font-size: 14px; }}
    .usage-value {{ color: {t['textColor']}; font-size: 14px; }}
    .usage-progress {{ display: flex; height: 8px; background-color: {t['progressBg']}; border-radius: 4px; margin: 6px 0 12px; overflow: hidden; }}
    .usage-progress-bar {{ height: 100%; }}
    .mem-progress {{ background: linear-gradient(90deg, #81c784, #388e3c); }}
    .disk-progress {{ background: linear-gradient(90deg, #9c27b0, #673ab7); }}
    .stat-label {{ color: #888; }}
    .stat-value {{ color: #333; font-weight: bold; }}
    .speed-test-container {{ margin-top: 15px; }}
    .speed-test-item {{ display: flex; align-items: center; margin-bottom: 8px; }}
    .speed-test-name {{ flex: 1; font-size: 14px; color: {t['textColor']}; }}
    .speed-test-ping {{ width: 80px; text-align: right; font-weight: bold; padding: 2px 8px; border-radius: 4px; font-size: 13px; }}
    .ping-good {{ background-color: rgba(56, 158, 13, 0.2); color: #5cb85a; font-weight: bold; text-align: center; }}
    .ping-medium {{ background-color: rgba(212, 107, 8, 0.2); color: #cc8a3d; font-weight: bold; text-align: center; }}
    .ping-bad {{ background-color: rgba(207, 19, 34, 0.2); color: #cc5a57; font-weight: bold; text-align: center; }}
    html, body, .container {{ min-height: 100vh; min-width: 100vw; }}
    .item {{ margin-bottom: 10px; color: {t['textColor']}; }}
    .command-stats {{ display: flex; flex-direction: column; gap: 6px; }}
    .command-item {{ padding: 6px 0; border-bottom: 1px dashed {t['borderColor']}; color: {t['textColor']}; display: flex; align-items: center; }}
    .command-name {{ font-weight: bold; margin-right: 8px; color: {t['textColor']}; }}
    .command-count {{ color: {t['secondaryText']}; }}
    .no-data {{ color: {t['secondaryText']}; font-style: italic; text-align: center; padding: 20px 0; }}
    </style></head><body>
    {header_html}
    <div class="container">
      <div class="column">{left_html}</div>
      <div class="column">{middle_html}</div>
      <div class="column">{right_html}</div>
    </div></body></html>
    '''
    return html

def render_and_upload(event, html, loop):
    temp_html = tempfile.NamedTemporaryFile('w', delete=False, suffix='.html', encoding='utf-8')
    temp_html.write(html)
    temp_html.close()
    img_path = temp_html.name.replace('.html', '.png')
    loop.run_until_complete(render_website_to_image(f'file://{temp_html.name}', img_path))
    with open(img_path, 'rb') as f:
        img_data = f.read()
    img = Image.open(img_path)
    width, height = img.size
    img_url = event.uploadToQQImageBed(img_data)
    os.remove(temp_html.name)
    os.remove(img_path)
    # 修正markdown格式，px前加#号
    return f"![状态 #{width}px #{height}px]({img_url})"

def format_bytes(bytes_num):
    if bytes_num == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while bytes_num >= 1024 and idx < len(units) - 1:
        bytes_num /= 1024.0
        idx += 1
    return f"{bytes_num:.2f} {units[idx]}"

# DAU数据、用户统计、系统信息等函数与前版一致，可复用
async def get_dau_stats():
    # 获取今天、昨天、前天日期
    today = datetime.datetime.now()
    dates = [today - datetime.timedelta(days=i) for i in range(3)]
    date_strs = [d.strftime('%Y%m%d') for d in dates]
    display_dates = [d.strftime('%Y-%m-%d') for d in dates]
    start_time = time.time()
    
    from function.log_db import LogDatabasePool
    def query_one(date_str, display_date):
        table_name = f"Mlog_{date_str}_message"
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        if not connection:
            return None
        cursor = None
        try:
            cursor = connection.cursor()
            # 检查表是否存在
            check_query = f"""
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """
            cursor.execute(check_query, (table_name,))
            check = cursor.fetchone()
            if not check or check.get('count', 0) == 0:
                return {
                    'date': display_date,
                    'active_users': 0,
                    'active_groups': 0,
                    'total_messages': 0,
                    'private_messages': 0,
                    'peak_hour': 0,
                    'peak_hour_count': 0,
                    'top_groups': [],
                    'top_users': [],
                    'query_time': 0
                }
            # 查询总消息数
            total_messages_query = f"SELECT COUNT(*) as count FROM {table_name}"
            cursor.execute(total_messages_query)
            total_messages = cursor.fetchone()
            # 查询不同用户数量
            unique_users_query = f"SELECT COUNT(DISTINCT user_id) as count FROM {table_name} WHERE user_id IS NOT NULL AND user_id != ''"
            cursor.execute(unique_users_query)
            unique_users = cursor.fetchone()
            # 查询不同群组数量
            unique_groups_query = f"SELECT COUNT(DISTINCT group_id) as count FROM {table_name} WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''"
            cursor.execute(unique_groups_query)
            unique_groups = cursor.fetchone()
            # 查询私聊消息数量
            private_messages_query = f"SELECT COUNT(*) as count FROM {table_name} WHERE group_id = 'c2c'"
            cursor.execute(private_messages_query)
            private_messages = cursor.fetchone()
            # 获取最活跃的5个群组
            active_groups_query = f"""
                SELECT group_id, COUNT(*) as msg_count 
                FROM {table_name} 
                WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''
                GROUP BY group_id 
                ORDER BY msg_count DESC 
                LIMIT 5
            """
            cursor.execute(active_groups_query)
            active_groups = cursor.fetchall()
            # 获取最活跃的5个用户
            active_users_query = f"""
                SELECT user_id, COUNT(*) as msg_count 
                FROM {table_name} 
                WHERE user_id IS NOT NULL AND user_id != ''
                GROUP BY user_id 
                ORDER BY msg_count DESC 
                LIMIT 5
            """
            cursor.execute(active_users_query)
            active_users = cursor.fetchall()
            # 按小时统计消息数量
            hourly_stats_query = f"""
                SELECT HOUR(timestamp) as hour, COUNT(*) as count 
                FROM {table_name} 
                GROUP BY HOUR(timestamp) 
                ORDER BY hour
            """
            cursor.execute(hourly_stats_query)
            hourly_stats = cursor.fetchall()
            hours_data = {i: 0 for i in range(24)}
            for row in hourly_stats:
                hour = row['hour']
                count = row['count']
                hours_data[hour] = count
            most_active_hour = max(hours_data.items(), key=lambda x: x[1]) if hours_data else (0, 0)
            # 处理最活跃群组
            top_groups = []
            for group in active_groups:
                group_id = group['group_id']
                if group_id and len(group_id) > 6:
                    masked_group_id = group_id[:3] + "****" + group_id[-3:]
                else:
                    masked_group_id = group_id
                top_groups.append({'id': masked_group_id, 'count': group['msg_count']})
            # 处理最活跃用户
            top_users = []
            for user in active_users:
                user_id = user['user_id']
                if user_id and len(user_id) > 6:
                    masked_user_id = user_id[:3] + "****" + user_id[-3:]
                else:
                    masked_user_id = user_id or "null"
                top_users.append({'id': masked_user_id, 'count': user['msg_count']})
            return {
                'date': display_date,
                'active_users': unique_users['count'] if unique_users else 0,
                'active_groups': unique_groups['count'] if unique_groups else 0,
                'total_messages': total_messages['count'] if total_messages else 0,
                'private_messages': private_messages['count'] if private_messages else 0,
                'peak_hour': most_active_hour[0],
                'peak_hour_count': most_active_hour[1],
                'top_groups': top_groups,
                'top_users': top_users,
                'query_time': 0  # 单条不计耗时
            }
        finally:
            if cursor:
                cursor.close()
            if connection:
                log_db_pool.release_connection(connection)
    
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(None, query_one, date_strs[i], display_dates[i]) for i in range(3)]
    results = await asyncio.gather(*tasks)
    # 统计总耗时
    query_time = int((time.time() - start_time) * 1000)
    for r in results:
        if r:
            r['query_time'] = query_time
    return results

def get_user_stats():
    stats = DatabaseService.execute_concurrent_queries([
        ("SELECT COUNT(*) as count FROM M_users", None, False),
        ("SELECT COUNT(*) as count FROM M_groups", None, False),
        ("SELECT COUNT(*) as count FROM M_members", None, False),
        ("""
            SELECT group_id, JSON_LENGTH(users) as member_count
            FROM M_groups_users
            ORDER BY member_count DESC
            LIMIT 1
        """, None, False)
    ])
    user_count = stats[0]['count'] if stats[0] else 0
    group_count = stats[1]['count'] if stats[1] else 0
    private_users_count = stats[2]['count'] if stats[2] else 0
    most_active_group = stats[3]
    group_id = most_active_group.get('group_id', "无数据") if most_active_group else "无数据"
    # 群id脱敏处理
    if group_id != "无数据" and group_id and len(group_id) > 6:
        group_id = group_id[:3] + "****" + group_id[-3:]
    member_count = most_active_group.get('member_count', 0) if most_active_group else 0
    return {
        'user_count': user_count,
        'group_count': group_count,
        'private_users_count': private_users_count,
        'most_active_group': group_id,
        'most_active_group_members': member_count
    }

def get_system_info():
    try:
        uptime_seconds = time.time() - psutil.boot_time()
        uptime_str = f"{int(uptime_seconds // 86400)}天{int((uptime_seconds % 86400) // 3600)}小时{int((uptime_seconds % 3600) // 60)}分钟"
    except:
        uptime_str = "N/A"
    try:
        user = os.getlogin()
    except:
        user = "N/A"
    return {
        'hostname': platform.node(),
        'os': platform.platform(),
        'kernel': platform.release(),
        'uptime': uptime_str,
        'user': user,
        'cpu_percent': psutil.cpu_percent(interval=1),
        'mem': psutil.virtual_memory(),
        'disk': psutil.disk_usage('/'),
        'process_count': len(psutil.pids())
    } 