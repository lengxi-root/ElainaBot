import os, sys, json, time, re, threading
from flask import request, jsonify

openapi_user_data = {}
openapi_login_tasks = {}

OPENAPI_DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'openapi.json')
try:
    from web.tools.bot_api import get_bot_api
    _bot_api = get_bot_api()
except ImportError:
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from web.tools.bot_api import get_bot_api
    _bot_api = get_bot_api()

def save_openapi_data():
    try:
        os.makedirs(os.path.dirname(OPENAPI_DATA_FILE), exist_ok=True)
        with open(OPENAPI_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(openapi_user_data, f, indent=2, ensure_ascii=False)
    except:
        pass

def load_openapi_data():
    global openapi_user_data
    try:
        openapi_user_data = json.load(open(OPENAPI_DATA_FILE, 'r', encoding='utf-8')) if os.path.exists(OPENAPI_DATA_FILE) else {}
    except:
        openapi_user_data = {}

def verify_openapi_login(user_data):
    try:
        return user_data and user_data.get('type') == 'ok' and _bot_api.get_bot_list(
            uin=user_data.get('uin'), quid=user_data.get('developerId'), ticket=user_data.get('ticket')).get('code') == 0
    except:
        return False

def _extract_template_params(template_content):
    seen = set()
    return [p for p in re.findall(r'\{\{\.(\w+)\}\}', template_content) if p not in seen and not seen.add(p)]

def _write_templates_to_file(templates, button_templates):
    from core.event.markdown_templates import MARKDOWN_TEMPLATES
    
    template_file_path = os.path.join(os.getcwd(), 'core', 'event', 'markdown_templates.py')
    
    with open(template_file_path, 'r', encoding='utf-8') as f:
        current_content = f.read()
    
    existing_ids = set()
    for template_config in MARKDOWN_TEMPLATES.values():
        existing_ids.add(template_config['id'])
    
    new_templates = []
    skipped_count = 0
    
    for template in templates:
        template_id = template.get('id', '')
        template_name = template.get('name', '未命名')
        template_content = template.get('content', '')
        
        if template_id in existing_ids:
            skipped_count += 1
            continue
            
        params = _extract_template_params(template_content)
        
        new_templates.append({
            'id': template_id,
            'name': template_name,
            'content': template_content,
            'params': params,
            'raw_data': template
        })
    
    existing_template_names = set(MARKDOWN_TEMPLATES.keys())
    template_counter = 1
    
    new_template_entries = []
    
    for template in new_templates:
        while str(template_counter) in existing_template_names:
            template_counter += 1
        
        template_name = str(template_counter)
        existing_template_names.add(template_name)
        
        escaped_content = template['content'].replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
        
        template_entry = f'''    "{template_name}": {{
        "id": "{template['id']}",
        "params": {template['params']}
    }},
    # 原始模板内容: {escaped_content}
'''
        new_template_entries.append(template_entry)
        template_counter += 1
    
    button_entries = []
    for button in button_templates:
        button_id = button.get('id', '')
        button_name = button.get('name', '未命名按钮')
        
        button_entry = f'''    # 按钮ID: {button_id} - {button_name}
'''
        button_entries.append(button_entry)
    
    if new_template_entries or button_entries:
        pattern = r'(MARKDOWN_TEMPLATES\s*=\s*\{.*?)(\n\})'
        match = re.search(pattern, current_content, re.DOTALL)
        
        if match:
            before_end = match.group(1)
            
            new_content = before_end
            
            if new_template_entries:
                new_content += '\n'
                for entry in new_template_entries:
                    new_content += entry
                    
            if button_entries:
                new_content += '\n    # 按钮模板ID\n'
                for entry in button_entries:
                    new_content += entry
                    
            new_content += match.group(2)
            
            updated_file_content = current_content.replace(match.group(0), new_content)
            
            with open(template_file_path, 'w', encoding='utf-8') as f:
                f.write(updated_file_content)
        else:
            raise Exception("无法找到MARKDOWN_TEMPLATES字典结构")
    
    return {
        'imported_count': len(new_templates),
        'skipped_count': skipped_count,
        'button_count': len(button_templates),
        'message': f'成功导入{len(new_templates)}个模板，跳过{skipped_count}个已存在模板'
    }

# ===== 清理任务 =====

def cleanup_openapi_tasks():
    current_time = time.time()
    for user_id in [uid for uid, (start_time, _) in openapi_login_tasks.items() if current_time - start_time > 300]:
        openapi_login_tasks.pop(user_id, None)

def start_openapi_cleanup_thread():
    def cleanup_loop():
        while True:
            try:
                cleanup_openapi_tasks()
            except:
                pass
            time.sleep(60)
    threading.Thread(target=cleanup_loop, daemon=True).start()

# ===== 路由处理函数 =====

def handle_start_login():
    try:
        user_id = request.get_json().get('user_id', 'web_user')
        if (login_data := _bot_api.create_login_qr()).get('status') != 'success' or not (url := login_data.get('url')) or not (qr := login_data.get('qr')):
            return jsonify({'success': False, 'message': '获取登录二维码失败，请稍后重试'})
        openapi_login_tasks[user_id] = (time.time(), {'qr': qr})
        return jsonify({'success': True, 'login_url': url, 'qr_code': qr, 'message': '请扫描二维码登录'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'启动登录失败: {str(e)}'})

def handle_check_login():
    try:
        user_id = request.get_json().get('user_id', 'web_user')
        if user_id not in openapi_login_tasks:
            return jsonify({'success': False, 'status': 'not_started', 'message': '未找到登录任务'})
        if (res := _bot_api.get_qr_login_info(qrcode=openapi_login_tasks[user_id][1]['qr'])).get('code') == 0:
            login_data = res.get('data', {}).get('data', {})
            openapi_user_data[user_id] = {'type': 'ok', **login_data}
            openapi_login_tasks.pop(user_id, None)
            save_openapi_data()
            return jsonify({'success': True, 'status': 'logged_in', 'data': {'uin': login_data.get('uin'), 
                'appId': login_data.get('appId'), 'developerId': login_data.get('developerId')}, 'message': '登录成功'})
        return jsonify({'success': True, 'status': 'waiting', 'message': '等待扫码登录'})
    except Exception as e:
        return jsonify({'success': False, 'status': 'error', 'message': f'检查登录状态失败: {str(e)}'})

def handle_get_botlist(check_openapi_login_func):
    try:
        user_id = request.get_json().get('user_id', 'web_user')
        if user_id not in openapi_user_data:
            return jsonify({'success': False, 'message': '未登录，请先登录开放平台'})
        user_data = openapi_user_data[user_id]
        if (res := _bot_api.get_bot_list(uin=user_data.get('uin'), quid=user_data.get('developerId'), ticket=user_data.get('ticket'))).get('code') != 0:
            return jsonify({'success': False, 'message': '登录状态失效，请重新登录'})
        return jsonify({'success': True, 'data': {'uin': user_data.get('uin'), 'apps': res.get('data', {}).get('apps', [])}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取机器人列表失败: {str(e)}'})

def handle_get_botdata(check_openapi_login_func, openapi_error_response, openapi_success_response):
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    target_appid = data.get('appid')
    days = data.get('days', 30)
    
    user_data = check_openapi_login_func(user_id)
    if not user_data:
        return openapi_error_response('未登录，请先登录开放平台')
    
    appid_to_use = target_appid if target_appid else user_data.get('appId')
    
    # 使用本地bot_api模块获取三种类型的数据
    try:
        data1_json = _bot_api.get_bot_data(
            uin=user_data.get('uin'),
            quid=user_data.get('developerId'),
            ticket=user_data.get('ticket'),
            appid=appid_to_use,
            data_type=1
        )
        
        data2_json = _bot_api.get_bot_data(
            uin=user_data.get('uin'),
            quid=user_data.get('developerId'),
            ticket=user_data.get('ticket'),
            appid=appid_to_use,
            data_type=2
        )
        
        data3_json = _bot_api.get_bot_data(
            uin=user_data.get('uin'),
            quid=user_data.get('developerId'),
            ticket=user_data.get('ticket'),
            appid=appid_to_use,
            data_type=3
        )
        
        # 检查API返回状态，兼容不同的错误字段
        def is_api_error(result):
            # 检查多种可能的错误状态
            return (result.get('retcode', 0) != 0 or 
                   result.get('code', 0) not in [0, 200] or
                   result.get('error') is not None)
        
        if any(is_api_error(x) for x in [data1_json, data2_json, data3_json]):
            # 提取具体的错误信息
            error_msgs = []
            for result in [data1_json, data2_json, data3_json]:
                if is_api_error(result):
                    error_msg = (result.get('msg') or 
                               result.get('message') or 
                               result.get('error') or 
                               f"API错误，code: {result.get('code', 'unknown')}")
                    error_msgs.append(error_msg)
            
            # 如果错误信息包含请求失败或者是认证相关错误，提示重新登录
            combined_error = ', '.join(set(error_msgs[:3]))  # 只显示前3个不同的错误
            if any(keyword in combined_error.lower() for keyword in ['登录', 'login', 'auth', '认证', '权限']):
                return openapi_error_response('登录状态失效，请重新登录')
            else:
                return openapi_error_response(f'获取数据失败: {combined_error}')
        
        msg_data = data1_json.get('data', {}).get('msg_data', [])
        group_data = data2_json.get('data', {}).get('group_data', [])
        friend_data = data3_json.get('data', {}).get('friend_data', [])
        
        max_days = min(len(msg_data), len(group_data), len(friend_data))
        actual_days = min(days, max_days)
        
        processed_data = []
        total_up_msg_people = 0
        
        for i in range(actual_days):
            msg_item = msg_data[i] if i < len(msg_data) else {}
            group_item = group_data[i] if i < len(group_data) else {}
            friend_item = friend_data[i] if i < len(friend_data) else {}
            
            day_data = {
                "date": msg_item.get('报告日期', '0'),
                "up_messages": msg_item.get('上行消息量', '0'),
                "up_users": msg_item.get('上行消息人数', '0'),
                "down_messages": msg_item.get('下行消息量', '0'),
                "total_messages": msg_item.get('总消息量', '0'),
                "current_groups": group_item.get('现有群组', '0'),
                "used_groups": group_item.get('已使用群组', '0'),
                "new_groups": group_item.get('新增群组', '0'),
                "removed_groups": group_item.get('移除群组', '0'),
                "current_friends": friend_item.get('现有好友数', '0'),
                "used_friends": friend_item.get('已使用好友数', '0'),
                "new_friends": friend_item.get('新增好友数', '0'),
                "removed_friends": friend_item.get('移除好友数', '0')
            }
            processed_data.append(day_data)
            total_up_msg_people += int(day_data['up_users'])
        
        avg_dau = round(total_up_msg_people / 30, 2) if len(msg_data) > 0 else 0
        
        return openapi_success_response({
            'uin': user_data.get('uin'),
            'appid': appid_to_use,
            'avg_dau': avg_dau,
            'days_data': processed_data
        })
        
    except Exception as e:
        return openapi_error_response(f'获取机器人数据失败: {str(e)}')

def handle_get_notifications(check_openapi_login_func, openapi_error_response, openapi_success_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    res = _bot_api.get_private_messages(uin=user_data.get('uin'), quid=user_data.get('developerId'), ticket=user_data.get('ticket'))
    if res.get('code', 0) != 0 or res.get('error'):
        return openapi_error_response(res.get('error', '获取通知消息失败'))
    processed_messages = [{'content': msg.get('content', ''), 'send_time': msg.get('send_time', ''),
        'type': msg.get('type', ''), 'title': msg.get('title', '')} for msg in res.get('messages', [])[:20]]
    return openapi_success_response({'uin': user_data.get('uin'), 'appid': user_data.get('appId'), 'messages': processed_messages})

def handle_logout(openapi_success_response):
    user_id = request.get_json().get('user_id', 'web_user')
    openapi_user_data.pop(user_id, None)
    save_openapi_data()
    return openapi_success_response(message='登出成功')

def handle_get_login_status(check_openapi_login_func, openapi_success_response):
    data = request.get_json() or {}
    user_data = check_openapi_login_func(data.get('user_id', 'web_user'))
    if user_data:
        return openapi_success_response(logged_in=True, uin=user_data.get('uin', ''), appid=user_data.get('appId', ''))
    return openapi_success_response(logged_in=False)

def handle_import_templates(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use = data.get('appid') or user_data.get('appId')
    res = _bot_api.get_message_templates(uin=user_data.get('uin'), quid=user_data.get('developerId'),
        ticket=user_data.get('ticket'), appid=appid_to_use)
    if res.get('retcode', 0) != 0 or res.get('code', 0) != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    templates = [{'id': t.get('模板id', ''), 'name': t.get('模板名称', '未命名'), 'type': t.get('模板类型', '未知类型'),
        'status': t.get('模板状态', '未知状态'), 'content': t.get('模板内容', ''), 'create_time': t.get('创建时间', ''),
        'update_time': t.get('更新时间', ''), 'raw_data': t} for t in res.get('data', {}).get('list', [])]
    button_templates = [t for t in templates if t.get('type') == '按钮模板']
    markdown_templates = [t for t in templates if t.get('type') == 'markdown模板']
    import_result = _write_templates_to_file(markdown_templates, button_templates)
    return jsonify({'success': True, 'data': {'imported_count': import_result['imported_count'],
        'skipped_count': import_result['skipped_count'], 'button_count': import_result['button_count'], 'message': import_result['message']}})

def handle_verify_saved_login():
    data = request.get_json()
    user_id = data.get('user_id', 'web_user')
    if user_id not in openapi_user_data:
        return jsonify({'success': True, 'valid': False, 'message': '没有保存的登录信息'})
    user_data = openapi_user_data[user_id]
    if verify_openapi_login(user_data):
        return jsonify({'success': True, 'valid': True, 'data': {'uin': user_data.get('uin'),
            'appId': user_data.get('appId'), 'developerId': user_data.get('developerId')}, 'message': '登录状态有效'})
    openapi_user_data.pop(user_id, None)
    save_openapi_data()
    return jsonify({'success': True, 'valid': False, 'message': '登录状态已失效'})

def handle_get_templates(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use = data.get('appid') or user_data.get('appId')
    res = _bot_api.get_message_templates(uin=user_data.get('uin'), quid=user_data.get('developerId'), 
        ticket=user_data.get('ticket'), appid=appid_to_use)
    if res.get('retcode', 0) != 0 or res.get('code', 0) not in [0, 200]:
        return openapi_error_response(res.get('msg') or res.get('message') or '获取模板失败，请重新登录')
    processed_templates = [{'id': t.get('模板id', ''), 'name': t.get('模板名称', '未命名'), 'type': t.get('模板类型', '未知类型'),
        'status': t.get('模板状态', '未知状态'), 'content': t.get('模板内容', ''), 'create_time': t.get('创建时间', ''),
        'update_time': t.get('更新时间', ''), 'raw_data': t} for t in res.get('data', {}).get('list', [])]
    return jsonify({'success': True, 'data': {'uin': user_data.get('uin'), 'appid': appid_to_use, 
        'templates': processed_templates, 'total': len(processed_templates)}})

def handle_get_template_detail(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (template_id := data.get('id')):
        return openapi_error_response('缺少模板ID参数')
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use = data.get('appid') or user_data.get('appId')
    res = _bot_api.get_message_templates(uin=user_data.get('uin'), quid=user_data.get('developerId'),
        ticket=user_data.get('ticket'), appid=appid_to_use)
    if res.get('retcode') != 0 and res.get('code') != 0:
        return openapi_error_response('登录状态失效，请重新登录')
    template_detail = next((t for t in res.get('data', {}).get('list', []) if t.get('模板id') == template_id), None)
    if not template_detail:
        return openapi_error_response('未找到指定的模板')
    return jsonify({'success': True, 'data': {'uin': user_data.get('uin'), 'appid': appid_to_use,
        'template': {'id': template_detail.get('模板id', ''), 'name': template_detail.get('模板名称', '未命名'),
            'type': template_detail.get('模板类型', '未知类型'), 'status': template_detail.get('模板状态', '未知状态'),
            'content': template_detail.get('模板内容', ''), 'create_time': template_detail.get('创建时间', ''),
            'update_time': template_detail.get('更新时间', ''), 'raw_data': template_detail}}})

def handle_render_button_template():
    data = request.get_json()
    if not (button_data := data.get('button_data')):
        return jsonify({'success': False, 'message': '缺少按钮数据'})
    rendered_rows = []
    for row_idx, row in enumerate(button_data.get('rows', [])[:5]):
        rendered_buttons = []
        for btn in row.get('buttons', [])[:5]:
            render_data, action = btn.get('render_data', {}), btn.get('action', {})
            rendered_buttons.append({'label': render_data.get('label', 'Button'), 'style': render_data.get('style', 0),
                'action_type': action.get('type', 2), 'action_data': action.get('data', ''),
                'permission': action.get('permission', {}), 'unsupport_tips': action.get('unsupport_tips', ''), 'reply': action.get('reply', '')})
        if rendered_buttons:
            rendered_rows.append({'row_index': row_idx, 'buttons': rendered_buttons})
    return jsonify({'success': True, 'data': {'rendered_rows': rendered_rows, 'total_rows': len(rendered_rows), 'max_buttons_per_row': 5}})

def handle_get_whitelist(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    if not (appid_to_use := data.get('appid') or user_data.get('appId')):
        return openapi_error_response('缺少AppID参数')
    res = _bot_api.get_white_list(appid=appid_to_use, uin=user_data.get('uin'),
        uid=user_data.get('developerId'), ticket=user_data.get('ticket'))
    if res.get('code', 0) != 0:
        return openapi_error_response(res.get('msg') or '获取白名单失败，请检查登录状态')
    formatted_ips = [{'ip': ip.get('ip', '') if isinstance(ip, dict) else ip, 'description': ip.get('desc', '') if isinstance(ip, dict) else '',
        'create_time': ip.get('create_time', '') if isinstance(ip, dict) else '', 'status': ip.get('status', 'active') if isinstance(ip, dict) else 'active'}
        for ip in res.get('data', [])]
    return jsonify({'success': True, 'data': {'uin': user_data.get('uin'), 'appid': appid_to_use,
        'ip_list': formatted_ips, 'total': len(formatted_ips)}})

def handle_update_whitelist(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    user_id, target_appid = data.get('user_id', 'web_user'), data.get('appid')
    ip_address, action = data.get('ip', '').strip(), data.get('action', '').lower()
    if not (user_data := check_openapi_login_func(user_id)):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use = target_appid or user_data.get('appId')
    if not appid_to_use:
        return openapi_error_response('缺少AppID参数')
    if not ip_address:
        return openapi_error_response('缺少IP地址参数')
    if action not in ['add', 'del']:
        return openapi_error_response('无效的操作类型，只支持add或del')
    if not re.match(r'^(\d{1,3}\.){3}\d{1,3}$', ip_address):
        return openapi_error_response('IP地址格式无效')
    qr_result = _bot_api.create_white_login_qr(appid=appid_to_use, uin=user_data.get('uin'),
        uid=user_data.get('developerId'), ticket=user_data.get('ticket'))
    if qr_result.get('code', 0) != 0 or not (qrcode := qr_result.get('qrcode', '')):
        return openapi_error_response('创建白名单授权失败')
    res = _bot_api.update_white_list(appid=appid_to_use, uin=user_data.get('uin'), uid=user_data.get('developerId'),
        ticket=user_data.get('ticket'), qrcode=qrcode, ip=ip_address, action=action)
    if res.get('code', 0) != 0:
        return openapi_error_response(res.get('msg') or f'{"添加" if action == "add" else "删除"}IP失败')
    return jsonify({'success': True, 'message': f'IP{"添加" if action == "add" else "删除"}成功',
        'data': {'ip': ip_address, 'action': action, 'appid': appid_to_use}})

def handle_get_delete_qr(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    if not (appid_to_use := data.get('appid') or user_data.get('appId')):
        return openapi_error_response('缺少AppID参数')
    qr_result = _bot_api.create_white_login_qr(appid=appid_to_use, uin=user_data.get('uin'),
        uid=user_data.get('developerId'), ticket=user_data.get('ticket'))
    if qr_result.get('code', 0) != 0:
        return openapi_error_response('创建授权二维码失败')
    qrcode, qr_url = qr_result.get('qrcode', ''), qr_result.get('url', '')
    if not qrcode or not qr_url:
        return openapi_error_response('获取授权二维码失败')
    return jsonify({'success': True, 'qrcode': qrcode, 'url': qr_url, 'message': '获取授权二维码成功'})

def handle_check_delete_auth(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use, qrcode = data.get('appid') or user_data.get('appId'), data.get('qrcode', '')
    if not appid_to_use or not qrcode:
        return openapi_error_response('缺少必要参数')
    auth_result = _bot_api.verify_qr_auth(appid=appid_to_use, uin=user_data.get('uin'),
        uid=user_data.get('developerId'), ticket=user_data.get('ticket'), qrcode=qrcode)
    return jsonify({'success': True, 'authorized': auth_result.get('code', 0) == 0,
        'message': '授权成功' if auth_result.get('code', 0) == 0 else '等待授权中'})

def handle_execute_delete_ip(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    user_id, target_appid = data.get('user_id', 'web_user'), data.get('appid')
    ip_address, qrcode = data.get('ip', '').strip(), data.get('qrcode', '')
    if not (user_data := check_openapi_login_func(user_id)):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use = target_appid or user_data.get('appId')
    if not all([appid_to_use, ip_address, qrcode]):
        return openapi_error_response('缺少必要参数')
    res = _bot_api.update_white_list(appid=appid_to_use, uin=user_data.get('uin'), uid=user_data.get('developerId'),
        ticket=user_data.get('ticket'), qrcode=qrcode, ip=ip_address, action='del')
    if res.get('code', 0) != 0:
        return openapi_error_response(res.get('msg') or '删除IP失败')
    return jsonify({'success': True, 'message': 'IP删除成功', 'data': {'ip': ip_address, 'appid': appid_to_use}})

def handle_batch_add_whitelist(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    user_id, target_appid = data.get('user_id', 'web_user'), data.get('appid')
    ip_list, qrcode = data.get('ip_list', []), data.get('qrcode', '')
    if not (user_data := check_openapi_login_func(user_id)):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use = target_appid or user_data.get('appId')
    if not all([appid_to_use, ip_list, qrcode]):
        return openapi_error_response('缺少必要参数')
    success_count, failed_ips = 0, []
    for ip in ip_list:
        res = _bot_api.update_white_list(appid=appid_to_use, uin=user_data.get('uin'), uid=user_data.get('developerId'),
            ticket=user_data.get('ticket'), qrcode=qrcode, ip=ip, action='add')
        if res.get('code', 0) == 0:
            success_count += 1
        else:
            failed_ips.append(ip)
    return jsonify({'success': True, 'message': f'批量添加完成：成功{success_count}个，失败{len(failed_ips)}个',
        'data': {'success_count': success_count, 'failed_count': len(failed_ips), 'failed_ips': failed_ips}})

def handle_create_template_qr(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    qr_result = _bot_api.create_template_qr(uin=user_data.get('uin'), quid=user_data.get('developerId'), ticket=user_data.get('ticket'))
    if qr_result.get('code', 0) != 0 or not (qrcode := qr_result.get('data', {}).get('QrCode', '')):
        return openapi_error_response(f'创建二维码失败: {qr_result.get("msg", "请稍后重试")}')
    from urllib.parse import quote
    qq_auth_url = f"https://q.qq.com/qrcode/check?client=qq&code={qrcode}&ticket={user_data.get('ticket')}"
    return jsonify({'success': True, 'qrcode': qrcode, 'url': f"https://api.2dcode.biz/v1/create-qr-code?data={quote(qq_auth_url)}", 'message': '二维码创建成功，请扫码授权'})

def handle_check_template_qr(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    if not (qrcode := data.get('qrcode', '')):
        return openapi_error_response('缺少二维码参数')
    auth_result = _bot_api.verify_qr_auth(uin=user_data.get('uin'), uid=user_data.get('developerId'), ticket=user_data.get('ticket'), qrcode=qrcode)
    return jsonify({'success': True, 'authorized': auth_result.get('code', 0) == 0,
        'message': '授权成功' if auth_result.get('code', 0) == 0 else '等待授权中'})

def handle_preview_template(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use, template_data = data.get('appid') or user_data.get('appId'), data.get('template_data', {})
    if not appid_to_use or not template_data:
        return openapi_error_response('缺少必要参数')
    preview_result = _bot_api.preview_template(bot_appid=appid_to_use, template_data=template_data,
        uin=user_data.get('uin'), uid=user_data.get('developerId'), ticket=user_data.get('ticket'))
    if preview_result.get('retcode', 0) != 0:
        return openapi_error_response(preview_result.get('msg', '预览失败'))
    return jsonify({'success': True, 'data': {'preview_text': preview_result.get('data', {}).get('tpl_text', '')}, 'message': '预览成功'})

def handle_submit_template(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use, template_data, qrcode = data.get('appid') or user_data.get('appId'), data.get('template_data', {}), data.get('qrcode', '')
    if not all([appid_to_use, template_data, qrcode]):
        return openapi_error_response('缺少必要参数')
    submit_result = _bot_api.submit_template(bot_appid=appid_to_use, template_data=template_data, qrcode=qrcode,
        uin=user_data.get('uin'), uid=user_data.get('developerId'), ticket=user_data.get('ticket'))
    if submit_result.get('retcode', 0) != 0:
        return openapi_error_response(submit_result.get('msg', '提交失败'))
    return jsonify({'success': True, 'data': {'template_id': submit_result.get('data', {}).get('tpl_id', '')}, 'message': '模板提交成功'})

def handle_audit_templates(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use, tpl_ids, qrcode = data.get('appid') or user_data.get('appId'), data.get('tpl_ids', []), data.get('qrcode', '')
    if not all([appid_to_use, tpl_ids, qrcode]):
        return openapi_error_response('缺少必要参数')
    audit_result = _bot_api.audit_templates(bot_appid=appid_to_use, tpl_ids=tpl_ids, qrcode=qrcode,
        uin=user_data.get('uin'), uid=user_data.get('developerId'), ticket=user_data.get('ticket'))
    if audit_result.get('retcode', 0) != 0:
        return openapi_error_response(audit_result.get('msg', '提审失败'))
    return jsonify({'success': True, 'message': f'成功提审 {len(tpl_ids)} 个模板'})

def handle_delete_templates(check_openapi_login_func, openapi_error_response):
    data = request.get_json()
    if not (user_data := check_openapi_login_func(data.get('user_id', 'web_user'))):
        return openapi_error_response('未登录，请先登录开放平台')
    appid_to_use, tpl_ids, qrcode = data.get('appid') or user_data.get('appId'), data.get('tpl_ids', []), data.get('qrcode', '')
    if not all([appid_to_use, tpl_ids, qrcode]):
        return openapi_error_response('缺少必要参数')
    delete_result = _bot_api.delete_templates(bot_appid=appid_to_use, tpl_ids=tpl_ids, qrcode=qrcode,
        uin=user_data.get('uin'), uid=user_data.get('developerId'), ticket=user_data.get('ticket'))
    if delete_result.get('retcode', 0) != 0:
        return openapi_error_response(delete_result.get('msg', '删除失败'))
    return jsonify({'success': True, 'message': f'成功删除 {len(tpl_ids)} 个模板'})

load_openapi_data()
start_openapi_cleanup_thread()

