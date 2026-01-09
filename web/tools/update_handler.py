import threading, requests, os, re, ast
from datetime import datetime
from flask import request, jsonify

_API_URL = "https://i.elaina.vin/api/elainabot/"
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_PATH = os.path.join(_BASE_DIR, 'config.py')

def handle_get_changelog():
    try:
        commits = requests.get(_API_URL, timeout=10).json()
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
            result.append({'sha': c.get('sha', '')[:8], 'message': info.get('message', '').strip(),
                           'author': author.get('name', '未知作者'), 'date': fmt_date,
                           'url': c.get('html_url', ''), 'full_sha': c.get('sha', '')})
        return jsonify({'success': True, 'data': result, 'total': len(result)})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取更新日志失败: {e}'}), 500

def handle_get_current_version():
    try:
        from web.tools.updater import get_updater
        return jsonify({'success': True, 'data': get_updater().get_version_info()})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取版本信息失败: {e}'}), 500

def handle_check_update():
    try:
        from web.tools.updater import get_updater
        return jsonify({'success': True, 'data': get_updater().check_for_updates()})
    except Exception as e:
        return jsonify({'success': False, 'message': f'检查更新失败: {e}'}), 500

def handle_start_update():
    try:
        version = (request.get_json() or {}).get('version')
        from web.tools.updater import get_updater
        updater = get_updater()
        
        def do_update():
            try:
                updater.update_to_version(version) if version else updater.update_to_latest()
            except Exception as e:
                updater._report_progress('failed', f'更新出错: {e}', 0)
        
        threading.Thread(target=do_update, daemon=True).start()
        return jsonify({'success': True, 'message': '更新已开始'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'启动更新失败: {e}'}), 500

def handle_get_update_status():
    try:
        return jsonify({'success': True, 'data': {'auto_update_enabled': False, 'auto_update_on': False, 'check_interval': 1800, 'is_checking': False}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取更新状态失败: {e}'}), 500

def handle_get_update_progress():
    try:
        from web.tools.updater import get_updater
        return jsonify({'success': True, 'data': get_updater().get_progress()})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取更新进度失败: {e}'}), 500


def _parse_config_file(content, extract_raw_lines=False):
    """解析配置文件，提取所有变量和字典
    
    Args:
        content: 配置文件内容
        extract_raw_lines: 是否提取原始行（包括注释）
    
    Returns:
        如果 extract_raw_lines=False: (config_vars, config_dicts)
        如果 extract_raw_lines=True: (config_vars, config_dicts, dict_raw_blocks, dict_key_lines)
    """
    config_vars = {}
    config_dicts = {}
    dict_raw_blocks = {}  # 存储完整的字典块（包括注释）
    dict_key_lines = {}   # 存储字典内每个key的原始行
    
    lines = content.split('\n')
    current_dict_name = None
    current_dict_content = []
    current_dict_start_line = 0
    brace_count = 0
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or stripped.startswith('"""') or stripped.startswith("'''"):
            if current_dict_name:
                current_dict_content.append(line)
            continue
        
        if current_dict_name:
            current_dict_content.append(line)
            brace_count += line.count('{') - line.count('}')
            
            # 提取字典内的key行（包括注释）
            if extract_raw_lines:
                key_match = re.match(r"^\s*['\"]?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?\s*:", stripped)
                if key_match:
                    key_name = key_match.group(1)
                    if current_dict_name not in dict_key_lines:
                        dict_key_lines[current_dict_name] = {}
                    dict_key_lines[current_dict_name][key_name] = line
            
            if brace_count == 0:
                try:
                    dict_str = '\n'.join(current_dict_content)
                    match = re.match(r'^[A-Z_][A-Z0-9_]*\s*=\s*(\{.*\})', dict_str, re.DOTALL)
                    if match:
                        config_dicts[current_dict_name] = ast.literal_eval(match.group(1))
                    if extract_raw_lines:
                        dict_raw_blocks[current_dict_name] = '\n'.join(current_dict_content)
                except:
                    pass
                current_dict_name = None
                current_dict_content = []
            continue
        
        dict_match = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*\{', stripped)
        if dict_match:
            current_dict_name = dict_match.group(1)
            current_dict_content = [line]
            current_dict_start_line = i
            brace_count = line.count('{') - line.count('}')
            if brace_count == 0:
                try:
                    match = re.match(r'^[A-Z_][A-Z0-9_]*\s*=\s*(\{.*\})', stripped)
                    if match:
                        config_dicts[current_dict_name] = ast.literal_eval(match.group(1))
                    if extract_raw_lines:
                        dict_raw_blocks[current_dict_name] = line
                except:
                    pass
                current_dict_name = None
                current_dict_content = []
            continue
        
        var_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+?)(?:\s*#.*)?$', stripped)
        if var_match:
            var_name = var_match.group(1)
            var_value = var_match.group(2).strip()
            if not var_value.startswith('{') and not var_value.startswith('['):
                try:
                    config_vars[var_name] = ast.literal_eval(var_value)
                except:
                    config_vars[var_name] = var_value
    
    if extract_raw_lines:
        return config_vars, config_dicts, dict_raw_blocks, dict_key_lines
    return config_vars, config_dicts


def _extract_var_lines(content):
    """从配置文件中提取变量的完整行（包括注释）"""
    var_lines = {}
    lines = content.split('\n')
    in_dict = False
    brace_count = 0
    
    for line in lines:
        stripped = line.strip()
        
        if in_dict:
            brace_count += line.count('{') - line.count('}')
            if brace_count == 0:
                in_dict = False
            continue
        
        dict_match = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*\{', stripped)
        if dict_match:
            in_dict = True
            brace_count = line.count('{') - line.count('}')
            if brace_count == 0:
                in_dict = False
            continue
        
        var_match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=', stripped)
        if var_match:
            var_name = var_match.group(1)
            var_lines[var_name] = line
    
    return var_lines


def _extract_dict_blocks_with_comments(content):
    """从配置文件中提取完整的字典块（包括前面的注释）"""
    dict_blocks = {}
    lines = content.split('\n')
    current_dict_name = None
    current_block = []
    pending_comments = []
    brace_count = 0
    
    for line in lines:
        stripped = line.strip()
        
        # 收集注释行
        if stripped.startswith('#') and not current_dict_name:
            pending_comments.append(line)
            continue
        
        # 空行重置注释
        if not stripped and not current_dict_name:
            pending_comments = []
            continue
        
        if current_dict_name:
            current_block.append(line)
            brace_count += line.count('{') - line.count('}')
            if brace_count == 0:
                dict_blocks[current_dict_name] = '\n'.join(current_block)
                current_dict_name = None
                current_block = []
                pending_comments = []
            continue
        
        dict_match = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*\{', stripped)
        if dict_match:
            current_dict_name = dict_match.group(1)
            # 包含前面的注释
            current_block = pending_comments + [line]
            pending_comments = []
            brace_count = line.count('{') - line.count('}')
            if brace_count == 0:
                dict_blocks[current_dict_name] = '\n'.join(current_block)
                current_dict_name = None
                current_block = []
        else:
            pending_comments = []
    
    return dict_blocks


def _get_default_config():
    """获取默认配置（从远程或本地模板）"""
    try:
        resp = requests.get(_API_URL + "?file=config.py", timeout=10)
        if resp.status_code == 200:
            return resp.text
    except:
        pass
    
    template_path = os.path.join(_BASE_DIR, 'config.py.template')
    if os.path.exists(template_path):
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
    return None


def handle_check_config_diff():
    """检查配置文件差异，返回缺失的配置项"""
    try:
        if not os.path.exists(_CONFIG_PATH):
            return jsonify({'success': False, 'message': '配置文件不存在'})
        
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            current_content = f.read()
        
        default_content = _get_default_config()
        if not default_content:
            return jsonify({'success': True, 'has_diff': False, 'message': '无法获取默认配置模板'})
        
        current_vars, current_dicts = _parse_config_file(current_content)
        default_vars, default_dicts = _parse_config_file(default_content)
        
        missing_vars = []
        missing_dict_keys = []
        
        for var_name, var_value in default_vars.items():
            if var_name not in current_vars:
                missing_vars.append({'name': var_name, 'value': var_value, 'type': type(var_value).__name__})
        
        for dict_name, dict_value in default_dicts.items():
            if dict_name not in current_dicts:
                missing_vars.append({'name': dict_name, 'value': dict_value, 'type': 'dict'})
            else:
                current_dict = current_dicts[dict_name]
                for key, value in dict_value.items():
                    if key not in current_dict:
                        missing_dict_keys.append({'dict_name': dict_name, 'key': key, 'value': value, 'type': type(value).__name__})
        
        has_diff = len(missing_vars) > 0 or len(missing_dict_keys) > 0
        return jsonify({
            'success': True,
            'has_diff': has_diff,
            'missing_vars': missing_vars,
            'missing_dict_keys': missing_dict_keys,
            'total_missing': len(missing_vars) + len(missing_dict_keys)
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'检查配置差异失败: {e}'})


def handle_auto_fill_config():
    """自动补全缺失的配置项（保留注释）"""
    try:
        if not os.path.exists(_CONFIG_PATH):
            return jsonify({'success': False, 'message': '配置文件不存在'})
        
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            current_content = f.read()
        
        default_content = _get_default_config()
        if not default_content:
            return jsonify({'success': False, 'message': '无法获取默认配置模板'})
        
        # 解析当前配置
        current_vars, current_dicts = _parse_config_file(current_content)
        # 解析默认配置，同时提取原始行
        default_vars, default_dicts, default_dict_blocks, default_dict_key_lines = _parse_config_file(default_content, extract_raw_lines=True)
        # 提取默认配置中变量的完整行
        default_var_lines = _extract_var_lines(default_content)
        # 提取默认配置中字典的完整块（包括注释）
        default_dict_full_blocks = _extract_dict_blocks_with_comments(default_content)
        
        lines = current_content.split('\n')
        additions = []
        dict_additions = {}
        
        # 检查缺失的独立变量
        for var_name, var_value in default_vars.items():
            if var_name not in current_vars:
                # 使用原始行（包括注释）
                if var_name in default_var_lines:
                    additions.append(default_var_lines[var_name])
                else:
                    additions.append(f"{var_name} = {repr(var_value)}")
        
        # 检查缺失的字典或字典键
        for dict_name, dict_value in default_dicts.items():
            if dict_name not in current_dicts:
                # 整个字典缺失，使用完整的字典块（包括注释）
                if dict_name in default_dict_full_blocks:
                    additions.append('')
                    additions.append(default_dict_full_blocks[dict_name])
                else:
                    additions.append(f"{dict_name} = {repr(dict_value)}")
            else:
                # 字典存在，检查缺失的键
                current_dict = current_dicts[dict_name]
                for key, value in dict_value.items():
                    if key not in current_dict:
                        if dict_name not in dict_additions:
                            dict_additions[dict_name] = []
                        # 使用原始行（包括注释）
                        if dict_name in default_dict_key_lines and key in default_dict_key_lines[dict_name]:
                            dict_additions[dict_name].append(default_dict_key_lines[dict_name][key])
                        else:
                            dict_additions[dict_name].append(f"    '{key}': {repr(value)},")
        
        # 处理字典内的键补全
        if dict_additions:
            new_lines = []
            in_dict = None
            brace_count = 0
            
            for i, line in enumerate(lines):
                stripped = line.strip()
                
                if in_dict:
                    brace_count += line.count('{') - line.count('}')
                    if brace_count == 0:
                        # 在字典结束前插入缺失的键
                        if in_dict in dict_additions:
                            for key_line in dict_additions[in_dict]:
                                new_lines.append(key_line)
                        in_dict = None
                    new_lines.append(line)
                    continue
                
                dict_match = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*\{', stripped)
                if dict_match:
                    dict_name = dict_match.group(1)
                    if dict_name in dict_additions:
                        in_dict = dict_name
                        brace_count = line.count('{') - line.count('}')
                        if brace_count == 0:
                            # 单行字典，需要展开
                            if '}' in line:
                                insert_pos = line.rfind('}')
                                insert_content = '\n'.join(dict_additions[dict_name])
                                line = line[:insert_pos] + '\n' + insert_content + '\n' + line[insert_pos:]
                            in_dict = None
                
                new_lines.append(line)
            
            lines = new_lines
        
        # 添加缺失的独立变量和整个字典
        if additions:
            lines.append('')
            lines.append('# ===== 自动补全的配置项 =====')
            for add in additions:
                if isinstance(add, str):
                    lines.append(add)
                else:
                    lines.append(str(add))
        
        new_content = '\n'.join(lines)
        
        try:
            compile(new_content, '<string>', 'exec')
        except SyntaxError as e:
            return jsonify({'success': False, 'message': f'生成的配置文件语法错误: {e}'})
        
        backup_path = _CONFIG_PATH + '.backup'
        with open(backup_path, 'w', encoding='utf-8') as f:
            f.write(current_content)
        
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        total_added = len([a for a in additions if a.strip()]) + sum(len(v) for v in dict_additions.values())
        return jsonify({
            'success': True,
            'message': f'成功补全 {total_added} 个配置项',
            'added_vars': len([a for a in additions if a.strip()]),
            'added_dict_keys': sum(len(v) for v in dict_additions.values()),
            'backup_path': backup_path
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'自动补全配置失败: {e}'})
