import os, re, ast
from datetime import datetime
from flask import request, jsonify

def get_base_dir():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def get_config_paths():
    base_dir = get_base_dir()
    config_new_path = os.path.join(base_dir, 'web', 'config_new.py')
    config_path = os.path.join(base_dir, 'config.py')
    return config_new_path, config_path

def get_target_config_path():
    config_new_path, config_path = get_config_paths()
    
    if os.path.exists(config_new_path):
        return config_new_path, True
    elif os.path.exists(config_path):
        return config_path, False
    else:
        return None, False

def handle_get_config():
    target_path, is_new = get_target_config_path()
    
    if target_path is None:
        return jsonify({'success': False, 'message': '配置文件不存在'}), 404
    
    with open(target_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    return jsonify({
        'success': True,
        'content': content,
        'is_new': is_new,
        'source': 'config_new.py' if is_new else 'config.py'
    })

def handle_parse_config():
    try:
        target_path, is_new = get_target_config_path()
        
        if target_path is None:
            return jsonify({
                'success': False,
                'message': '配置文件不存在'
            }), 404
        
        with open(target_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        config_items = []
        group_display_names = {}
        lines = content.split('\n')
        current_dict = None
        dict_indent = 0
        last_comment = None
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            if not stripped or stripped.startswith('"""') or stripped.startswith("'''") or stripped.startswith('import ') or stripped.startswith('from '):
                continue
            
            if stripped.startswith('#'):
                last_comment = stripped.lstrip('#').strip()
                continue
            

            dict_start_pattern = r'^([A-Z_][A-Z0-9_]*)\s*=\s*\{(.*)$'
            dict_match = re.match(dict_start_pattern, stripped)
            if dict_match:
                current_dict = dict_match.group(1)
                dict_indent = len(line) - len(line.lstrip())
                
                if last_comment and '-' in last_comment:
                    display_name = last_comment.split('-')[0].strip()
                    group_display_names[current_dict] = display_name
                
                last_comment = None
                
                if dict_match.group(2).strip() == '}':
                    current_dict = None
                continue
            
            if current_dict and stripped == '}':
                current_dict = None
                continue
            
            if current_dict:
                dict_item_pattern = r"^['\"]?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?\s*:\s*(.+?)(?:,\s*)?(?:#\s*(.+))?$"
                dict_item_match = re.match(dict_item_pattern, stripped)
                if dict_item_match:
                    key_name = dict_item_match.group(1)
                    value_str = dict_item_match.group(2).strip().rstrip(',').strip()
                    inline_comment = dict_item_match.group(3).strip() if dict_item_match.group(3) else ''
                    
                    try:
                        parsed_value = ast.literal_eval(value_str)
                        
                        if parsed_value is None:
                            value_type = 'string'
                            value = ''
                        elif isinstance(parsed_value, bool):
                            value_type = 'boolean'
                            value = parsed_value
                        elif isinstance(parsed_value, (int, float)):
                            value_type = 'number'
                            value = parsed_value
                        elif isinstance(parsed_value, str):
                            value_type = 'string'
                            value = parsed_value
                        elif isinstance(parsed_value, list):
                            if all(isinstance(item, str) for item in parsed_value):
                                value_type = 'list'
                                value = parsed_value
                            else:
                                continue
                        else:
                            continue
                        
                        config_items.append({
                            'name': f"{current_dict}.{key_name}",
                            'dict_name': current_dict,
                            'key_name': key_name,
                            'value': value,
                            'type': value_type,
                            'comment': inline_comment,
                            'line': i,
                            'is_dict_item': True
                        })
                    except (ValueError, SyntaxError):
                        continue
                continue
            

            simple_pattern = r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+?)(?:\s*#\s*(.+))?$'
            match = re.match(simple_pattern, stripped)
            
            if match:
                var_name = match.group(1)
                value_str = match.group(2).strip()
                inline_comment = match.group(3).strip() if match.group(3) else ''
                
                if value_str.endswith('{') or value_str.endswith('[') or value_str == '{' or value_str == '[':
                    continue
                
                try:
                    parsed_value = ast.literal_eval(value_str)
                    
                    if parsed_value is None:
                        value_type = 'string'
                        value = ''
                    elif isinstance(parsed_value, bool):
                        value_type = 'boolean'
                        value = parsed_value
                    elif isinstance(parsed_value, (int, float)):
                        value_type = 'number'
                        value = parsed_value
                    elif isinstance(parsed_value, str):
                        value_type = 'string'
                        value = parsed_value
                    elif isinstance(parsed_value, list):
                        if all(isinstance(item, str) for item in parsed_value):
                            value_type = 'list'
                            value = parsed_value
                        else:
                            continue
                    else:
                        continue
                    
                    config_items.append({
                        'name': var_name,
                        'value': value,
                        'type': value_type,
                        'comment': inline_comment,
                        'line': i,
                        'is_dict_item': False
                    })
                except (ValueError, SyntaxError):
                    continue
        
        config_new_path, _ = get_config_paths()
        source = 'config_new.py' if is_new else 'config.py'
        
        return jsonify({
            'success': True,
            'items': config_items,
            'is_new': is_new,
            'source': source,
            'group_names': group_display_names
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'解析配置文件失败: {str(e)}'
        }), 500

def handle_update_config_items():
    try:
        data = request.get_json()
        if not data or 'items' not in data:
            return jsonify({
                'success': False,
                'message': '缺少配置项数据'
            }), 400
        
        items = data['items']
        
        target_path, _ = get_target_config_path()
        
        if target_path is None:
            return jsonify({
                'success': False,
                'message': '配置文件不存在'
            }), 404
        
        with open(target_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for item in items:
            var_name = item['name']
            new_value = item['value']
            value_type = item['type']
            is_dict_item = item.get('is_dict_item', False)
            
            if value_type == 'string':
                formatted_value = 'None' if new_value == '' else f'"{new_value}"'
            elif value_type == 'boolean':
                formatted_value = 'True' if new_value else 'False'
            elif value_type == 'number':
                formatted_value = str(new_value)
            elif value_type == 'list':
                formatted_value = '[' + ', '.join([f'"{item}"' for item in new_value]) + ']' if isinstance(new_value, list) else '[]'
            else:
                formatted_value = str(new_value)
            
            if is_dict_item:
                dict_name = item.get('dict_name', '')
                key_name = item.get('key_name', '')
                
                dict_start_pattern = rf'^({re.escape(dict_name)})\s*=\s*\{{'
                in_target_dict = False
                dict_depth = 0
                
                for i, line in enumerate(lines):
                    if re.match(dict_start_pattern, line.strip()):
                        in_target_dict = True
                        dict_depth = 1
                        continue
                    
                    if in_target_dict:
                        dict_depth += line.count('{')
                        dict_depth -= line.count('}')
                        
                        if dict_depth == 0:
                            in_target_dict = False
                            break
                        
                        pattern = rf"^(\s*)['\"]?({re.escape(key_name)})['\"]?\s*:\s*(.+?)(?:,\s*)?(\s*#.+)?$"
                        match = re.match(pattern, line)
                        if match:
                            indent = match.group(1)
                            comment = match.group(4) if match.group(4) else ''
                            
                            if comment:
                                value_part = f"'{key_name}': {formatted_value},"
                                original_value_part = f"'{match.group(2)}': {match.group(3)},"
                                spaces_count = len(original_value_part) - len(value_part)
                                if spaces_count < 2:
                                    spaces_count = 2
                                spacing = ' ' * spaces_count
                                clean_comment = comment.strip()
                                if not clean_comment.startswith('#'):
                                    clean_comment = '# ' + clean_comment
                                lines[i] = f'{indent}{value_part}{spacing}{clean_comment}\n'
                            else:
                                lines[i] = f"{indent}'{key_name}': {formatted_value},\n"
                            break
            else:
                pattern = rf'^(\s*)({re.escape(var_name)})\s*=\s*(.+?)(\s*#.+)?$'
                for i, line in enumerate(lines):
                    match = re.match(pattern, line)
                    if match:
                        indent = match.group(1)
                        comment = match.group(4) if match.group(4) else ''
                        
                        if comment:
                            value_part = f'{var_name} = {formatted_value}'
                            original_value_part = match.group(2) + ' = ' + match.group(3)
                            spaces_count = len(original_value_part) - len(value_part)
                            if spaces_count < 2:
                                spaces_count = 2
                            spacing = ' ' * spaces_count
                            clean_comment = comment.strip()
                            if not clean_comment.startswith('#'):
                                clean_comment = '# ' + clean_comment
                            lines[i] = f'{indent}{value_part}{spacing}{clean_comment}\n'
                        else:
                            lines[i] = f'{indent}{var_name} = {formatted_value}\n'
                        break
        
        new_content = ''.join(lines)
        
        try:
            compile(new_content, '<string>', 'exec')
        except SyntaxError as e:
            return jsonify({
                'success': False,
                'message': f'配置文件语法错误: 第{e.lineno}行 - {e.msg}'
            }), 400
        
        web_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_new_path = os.path.join(web_dir, 'config_new.py')
        
        with open(config_new_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        return jsonify({
            'success': True,
            'message': '配置已保存，请重启框架以应用更改',
            'file_path': config_new_path
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'更新配置失败: {str(e)}'
        }), 500

def handle_save_config():
    data = request.get_json()
    
    if not data or 'content' not in data:
        return jsonify({'success': False, 'message': '缺少配置内容'}), 400
    
    try:
        compile(data['content'], '<string>', 'exec')
    except SyntaxError as e:
        return jsonify({'success': False, 'message': f'配置文件语法错误: 第{e.lineno}行 - {e.msg}'}), 400
    
    web_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_new_path = os.path.join(web_dir, 'config_new.py')
    
    with open(config_new_path, 'w', encoding='utf-8') as f:
        f.write(data['content'])
    
    return jsonify({
        'success': True,
        'message': '配置文件已保存，请重启框架以应用更改',
        'file_path': config_new_path
    })

def handle_check_pending_config():
    web_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_new_path = os.path.join(web_dir, 'config_new.py')
    
    exists = os.path.exists(config_new_path)
    modified_time = None
    
    if exists:
        modified_time = datetime.fromtimestamp(os.path.getmtime(config_new_path)).strftime('%Y-%m-%d %H:%M:%S')
    
    return jsonify({
        'success': True,
        'pending': exists,
        'modified_time': modified_time
    })

def handle_cancel_pending_config():
    web_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_new_path = os.path.join(web_dir, 'config_new.py')
    
    if os.path.exists(config_new_path):
        os.remove(config_new_path)
        return jsonify({'success': True, 'message': '已取消待应用的配置'})
    
    return jsonify({'success': False, 'message': '没有待应用的配置文件'}), 404

