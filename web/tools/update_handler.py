import threading, requests, os, re, ast
from datetime import datetime
from flask import request, jsonify

_API_URL = "https://i.elaina.vin/api/elainabot/"
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_PATH = os.path.join(_BASE_DIR, 'config.py')

def handle_get_changelog():
    """获取更新日志 - 统一使用 GitHub API 格式"""
    try:
        from web.tools.updater import get_updater, DOWNLOAD_SOURCES
        updater = get_updater()
        api_url = DOWNLOAD_SOURCES.get(updater.download_source, DOWNLOAD_SOURCES['proxy'])['api_url']
        
        resp = requests.get(api_url, timeout=15, headers={'User-Agent': 'ElainaBot/1.0', 'Accept': 'application/json'})
        commits = resp.json() if isinstance(resp.json(), list) else []
        
        result = []
        for c in commits:
            info = c.get('commit')
            if not info:
                continue
            author = info.get('author', {})
            date_str = author.get('date', '')
            try:
                fmt_date = datetime.fromisoformat(date_str.replace('Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S') if date_str else '未知时间'
            except:
                fmt_date = '未知时间'
            result.append({
                'sha': c.get('sha', '')[:8],
                'message': info.get('message', '').strip(),
                'author': author.get('name', '未知作者'),
                'date': fmt_date,
                'url': c.get('html_url', ''),
                'full_sha': c.get('sha', '')
            })
        
        return jsonify({'success': True, 'data': result, 'total': len(result), 'source': updater.download_source})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取更新日志失败: {e}'}), 500

def handle_get_current_version():
    from web.tools.updater import get_updater
    updater = get_updater()
    info = updater.get_version_info()
    info['download_source'] = updater.download_source
    return jsonify({'success': True, 'data': info})

def handle_check_update():
    from web.tools.updater import get_updater
    return jsonify({'success': True, 'data': get_updater().check_for_updates()})

def handle_start_update():
    data = request.get_json() or {}
    from web.tools.updater import get_updater
    updater = get_updater()
    if data.get('source'):
        updater.set_download_source(data['source'])
    
    def do_update():
        try:
            if data.get('force'):
                updater.force_update()
            elif data.get('version'):
                updater.update_to_version(data['version'])
            else:
                updater.update_to_latest()
        except Exception as e:
            updater._report_progress('failed', f'更新出错: {e}', 0)
    
    threading.Thread(target=do_update, daemon=True).start()
    return jsonify({'success': True, 'message': '更新已开始'})

def handle_get_update_status():
    from web.tools.updater import get_updater
    return jsonify({'success': True, 'data': {'download_source': get_updater().download_source}})

def handle_get_update_progress():
    from web.tools.updater import get_updater
    return jsonify({'success': True, 'data': get_updater().get_progress()})

def handle_get_download_sources():
    from web.tools.updater import get_updater
    updater = get_updater()
    return jsonify({'success': True, 'data': {'sources': updater.get_available_sources(), 'current': updater.download_source}})

def handle_set_download_source():
    data = request.get_json() or {}
    if not data.get('source'):
        return jsonify({'success': False, 'message': '请指定下载源'}), 400
    from web.tools.updater import get_updater
    updater = get_updater()
    if updater.set_download_source(data['source']):
        return jsonify({'success': True, 'message': f'已切换到 {data["source"]}', 'current': data['source']})
    return jsonify({'success': False, 'message': '无效的下载源'}), 400

def handle_test_download_source():
    data = request.get_json() or {}
    from web.tools.updater import get_updater
    return jsonify({'success': True, 'data': get_updater().test_source_connection(data.get('source'))})

# ===== 配置文件解析相关 =====

def _parse_config_file(content):
    """解析配置文件，提取变量和字典"""
    config_vars, config_dicts = {}, {}
    lines = content.split('\n')
    current_dict, dict_content, brace_count = None, [], 0
    
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            if current_dict:
                dict_content.append(line)
            continue
        
        if current_dict:
            dict_content.append(line)
            brace_count += line.count('{') - line.count('}')
            if brace_count == 0:
                try:
                    match = re.match(r'^[A-Z_][A-Z0-9_]*\s*=\s*(\{.*\})', '\n'.join(dict_content), re.DOTALL)
                    if match:
                        config_dicts[current_dict] = ast.literal_eval(match.group(1))
                except:
                    pass
                current_dict, dict_content = None, []
            continue
        
        dict_match = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*\{', stripped)
        if dict_match:
            current_dict = dict_match.group(1)
            dict_content = [line]
            brace_count = line.count('{') - line.count('}')
            if brace_count == 0:
                try:
                    match = re.match(r'^[A-Z_][A-Z0-9_]*\s*=\s*(\{.*\})', stripped)
                    if match:
                        config_dicts[current_dict] = ast.literal_eval(match.group(1))
                except:
                    pass
                current_dict, dict_content = None, []
            continue
        
        var_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+?)(?:\s*#.*)?$', stripped)
        if var_match:
            name, value = var_match.group(1), var_match.group(2).strip()
            if not value.startswith(('{', '[')):
                try:
                    config_vars[name] = ast.literal_eval(value)
                except:
                    config_vars[name] = value
    
    return config_vars, config_dicts

def _get_default_config():
    """获取默认配置"""
    try:
        resp = requests.get(_API_URL + "?file=config.py", timeout=10)
        if resp.status_code == 200:
            return resp.text
    except:
        pass
    template = os.path.join(_BASE_DIR, 'config.py.template')
    return open(template, 'r', encoding='utf-8').read() if os.path.exists(template) else None


def handle_check_config_diff():
    """检查配置文件差异"""
    if not os.path.exists(_CONFIG_PATH):
        return jsonify({'success': False, 'message': '配置文件不存在'})
    
    with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
        current_vars, current_dicts = _parse_config_file(f.read())
    
    default_content = _get_default_config()
    if not default_content:
        return jsonify({'success': True, 'has_diff': False, 'message': '无法获取默认配置模板'})
    
    default_vars, default_dicts = _parse_config_file(default_content)
    
    missing_vars = [{'name': k, 'value': v, 'type': type(v).__name__} 
                    for k, v in default_vars.items() if k not in current_vars]
    missing_vars += [{'name': k, 'value': v, 'type': 'dict'} 
                     for k, v in default_dicts.items() if k not in current_dicts]
    
    missing_keys = []
    for dict_name, dict_value in default_dicts.items():
        if dict_name in current_dicts:
            for key, value in dict_value.items():
                if key not in current_dicts[dict_name]:
                    missing_keys.append({'dict_name': dict_name, 'key': key, 'value': value, 'type': type(value).__name__})
    
    return jsonify({
        'success': True, 'has_diff': bool(missing_vars or missing_keys),
        'missing_vars': missing_vars, 'missing_dict_keys': missing_keys,
        'total_missing': len(missing_vars) + len(missing_keys)
    })


def handle_auto_fill_config():
    """自动补全缺失的配置项"""
    if not os.path.exists(_CONFIG_PATH):
        return jsonify({'success': False, 'message': '配置文件不存在'})
    
    with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
        current_content = f.read()
    
    default_content = _get_default_config()
    if not default_content:
        return jsonify({'success': False, 'message': '无法获取默认配置模板'})
    
    current_vars, current_dicts = _parse_config_file(current_content)
    default_vars, default_dicts = _parse_config_file(default_content)
    
    additions = []
    
    # 补全缺失的变量
    for name, value in default_vars.items():
        if name not in current_vars:
            additions.append(f"{name} = {repr(value)}")
    
    # 补全缺失的字典
    for name, value in default_dicts.items():
        if name not in current_dicts:
            additions.append(f"{name} = {repr(value)}")
    
    # 补全字典内缺失的键（简化处理：追加到文件末尾作为注释提示）
    dict_key_hints = []
    for dict_name, dict_value in default_dicts.items():
        if dict_name in current_dicts:
            for key, value in dict_value.items():
                if key not in current_dicts[dict_name]:
                    dict_key_hints.append(f"# {dict_name}['{key}'] = {repr(value)}")
    
    if not additions and not dict_key_hints:
        return jsonify({'success': True, 'message': '配置已是最新，无需补全'})
    
    # 备份
    backup_path = _CONFIG_PATH + '.backup'
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(current_content)
    
    # 追加内容
    new_content = current_content.rstrip() + '\n'
    if additions:
        new_content += '\n# ===== 自动补全的配置项 =====\n'
        new_content += '\n'.join(additions) + '\n'
    if dict_key_hints:
        new_content += '\n# ===== 字典内缺失的键（请手动添加到对应字典中）=====\n'
        new_content += '\n'.join(dict_key_hints) + '\n'
    
    try:
        compile(new_content, '<string>', 'exec')
    except SyntaxError as e:
        return jsonify({'success': False, 'message': f'生成的配置文件语法错误: {e}'})
    
    with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    return jsonify({
        'success': True,
        'message': f'成功补全 {len(additions)} 个配置项，{len(dict_key_hints)} 个字典键提示',
        'backup_path': backup_path
    })
