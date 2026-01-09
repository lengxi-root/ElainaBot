import os, re, ast
from datetime import datetime
from flask import request, jsonify

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_NEW_PATH = os.path.join(_WEB_DIR, 'config_new.py')
_CONFIG_PATH = os.path.join(_BASE_DIR, 'config.py')

_DICT_START_RE = re.compile(r'^([A-Z_][A-Z0-9_]*)\s*=\s*\{(.*)')
_SIMPLE_VAR_RE = re.compile(r'^([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+?)(?:\s*#\s*(.+))?$')

_SKIP_PREFIXES = frozenset(('"""', "'''", 'import ', 'from '))
_TYPE_MAP = {bool: 'boolean', int: 'number', float: 'number', str: 'string', list: 'list'}

_ERR_NO_CONFIG = {'success': False, 'message': '配置文件不存在'}
_ERR_NO_DATA = {'success': False, 'message': '缺少配置项数据'}
_ERR_NO_CONTENT = {'success': False, 'message': '缺少配置内容'}
_ERR_NO_PENDING = {'success': False, 'message': '没有待应用的配置文件'}


def _get_target_config_path():
    if os.path.exists(_CONFIG_NEW_PATH):
        return _CONFIG_NEW_PATH, True
    if os.path.exists(_CONFIG_PATH):
        return _CONFIG_PATH, False
    return None, False


def _parse_dict_item(line):
    """解析字典项，提取键名、值和注释"""
    stripped = line.strip()
    # 匹配: 'key': value,  # comment 或 'key': value, 或 'key': value
    # 使用更简单的方式：先找注释位置
    
    # 匹配键名
    key_match = re.match(r"^['\"]?([a-zA-Z_][a-zA-Z0-9_]*)['\"]?\s*:\s*", stripped)
    if not key_match:
        return None, None, None
    
    key_name = key_match.group(1)
    rest = stripped[key_match.end():]
    
    # 找到不在字符串内的 # 位置
    comment = ""
    value_part = rest
    
    # 简单处理：从后往前找 #，检查它前面是否在字符串内
    hash_pos = -1
    in_string = False
    string_char = None
    for i, char in enumerate(rest):
        if not in_string:
            if char in ('"', "'"):
                in_string = True
                string_char = char
            elif char == '#':
                hash_pos = i
                break
        else:
            if char == string_char and (i == 0 or rest[i-1] != '\\'):
                in_string = False
                string_char = None
    
    if hash_pos >= 0:
        comment = rest[hash_pos+1:].strip()
        value_part = rest[:hash_pos]
    
    # 清理值部分
    value_str = value_part.strip().rstrip(',').strip()
    return key_name, value_str, comment


def _parse_value(value_str):
    try:
        parsed = ast.literal_eval(value_str)
        if parsed is None:
            return '', 'string'
        vtype = _TYPE_MAP.get(type(parsed))
        if vtype == 'list' and not all(isinstance(x, str) for x in parsed):
            return None, None
        if vtype:
            return parsed, vtype
    except (ValueError, SyntaxError):
        # 如果ast.literal_eval失败，检查是否是带引号的字符串
        # 处理类似 "linear-gradient(135deg, #5865F2, #7289DA)" 这样的复杂字符串
        stripped = value_str.strip()
        if (stripped.startswith('"') and stripped.endswith('"')) or \
           (stripped.startswith("'") and stripped.endswith("'")):
            # 手动提取引号内的字符串内容
            return stripped[1:-1], 'string'
    return None, None


def _format_value(value, value_type):
    if value_type == 'string':
        return 'None' if value == '' else f'"{value}"'
    if value_type == 'boolean':
        return 'True' if value else 'False'
    if value_type == 'number':
        return str(value)
    if value_type == 'list':
        return '[' + ', '.join(f'"{x}"' for x in value) + ']' if isinstance(value, list) else '[]'
    return str(value)


def handle_get_config():
    target_path, is_new = _get_target_config_path()
    if not target_path:
        return jsonify(_ERR_NO_CONFIG), 404
    
    with open(target_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    return jsonify({
        'success': True, 'content': content, 'is_new': is_new,
        'source': 'config_new.py' if is_new else 'config.py'
    })


def handle_parse_config():
    try:
        target_path, is_new = _get_target_config_path()
        if not target_path:
            return jsonify(_ERR_NO_CONFIG), 404
        
        with open(target_path, 'r', encoding='utf-8') as f:
            lines = f.read().split('\n')
        
        config_items, group_names = [], {}
        current_dict, last_comment = None, None
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped or any(stripped.startswith(p) for p in _SKIP_PREFIXES):
                continue
            
            if stripped.startswith('#'):
                last_comment = stripped.lstrip('#').strip()
                continue
            
            dict_match = _DICT_START_RE.match(stripped)
            if dict_match:
                current_dict = dict_match.group(1)
                if last_comment and '-' in last_comment:
                    group_names[current_dict] = last_comment.split('-')[0].strip()
                last_comment = None
                if dict_match.group(2).strip() == '}':
                    current_dict = None
                continue
            
            if current_dict and stripped == '}':
                current_dict = None
                continue
            
            if current_dict:
                key_name, value_str, comment = _parse_dict_item(stripped)
                if key_name and value_str:
                    value, vtype = _parse_value(value_str)
                    if vtype:
                        config_items.append({
                            'name': f"{current_dict}.{key_name}", 'dict_name': current_dict,
                            'key_name': key_name, 'value': value, 'type': vtype,
                            'comment': comment or '', 'line': i, 'is_dict_item': True
                        })
                continue
            
            m = _SIMPLE_VAR_RE.match(stripped)
            if m:
                val_str = m.group(2).strip()
                if val_str.endswith(('{', '[')) or val_str in ('{', '['):
                    continue
                value, vtype = _parse_value(val_str)
                if vtype:
                    config_items.append({
                        'name': m.group(1), 'value': value, 'type': vtype,
                        'comment': (m.group(3) or '').strip(), 'line': i, 'is_dict_item': False
                    })
        
        return jsonify({
            'success': True, 'items': config_items, 'is_new': is_new,
            'source': 'config_new.py' if is_new else 'config.py', 'group_names': group_names
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'解析配置文件失败: {e}'}), 500


def handle_update_config_items():
    try:
        data = request.get_json()
        if not data or 'items' not in data:
            return jsonify(_ERR_NO_DATA), 400
        
        target_path, _ = _get_target_config_path()
        if not target_path:
            return jsonify(_ERR_NO_CONFIG), 404
        
        with open(target_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        for item in data['items']:
            var_name, new_value, value_type = item['name'], item['value'], item['type']
            is_dict_item = item.get('is_dict_item', False)
            formatted = _format_value(new_value, value_type)
            
            if is_dict_item:
                dict_name, key_name = item.get('dict_name', ''), item.get('key_name', '')
                dict_start_re = re.compile(rf'^({re.escape(dict_name)})\s*=\s*\{{')
                in_dict, depth = False, 0
                
                for idx, ln in enumerate(lines):
                    if dict_start_re.match(ln.strip()):
                        in_dict, depth = True, 1
                        continue
                    if in_dict:
                        depth += ln.count('{') - ln.count('}')
                        if depth == 0:
                            break
                        # 使用_parse_dict_item来匹配行
                        parsed_key, _, parsed_comment = _parse_dict_item(ln)
                        if parsed_key == key_name:
                            indent = ln[:len(ln) - len(ln.lstrip())]
                            if parsed_comment:
                                lines[idx] = f"{indent}'{key_name}': {formatted},  # {parsed_comment}\n"
                            else:
                                lines[idx] = f"{indent}'{key_name}': {formatted},\n"
                            break
            else:
                pattern = rf'^(\s*)({re.escape(var_name)})\s*=\s*(.+?)(\s*#.+)?$'
                for idx, ln in enumerate(lines):
                    m = re.match(pattern, ln)
                    if m:
                        indent, comment = m.group(1), m.group(4) or ''
                        if comment:
                            lines[idx] = f'{indent}{var_name} = {formatted}  {comment.strip()}\n'
                        else:
                            lines[idx] = f'{indent}{var_name} = {formatted}\n'
                        break
        
        new_content = ''.join(lines)
        try:
            compile(new_content, '<string>', 'exec')
        except SyntaxError as e:
            return jsonify({'success': False, 'message': f'配置文件语法错误: 第{e.lineno}行 - {e.msg}'}), 400
        
        with open(_CONFIG_NEW_PATH, 'w', encoding='utf-8') as f:
            f.write(new_content)
        
        return jsonify({'success': True, 'message': '配置已保存，请重启框架以应用更改', 'file_path': _CONFIG_NEW_PATH})
    except Exception as e:
        return jsonify({'success': False, 'message': f'更新配置失败: {e}'}), 500


def handle_save_config():
    data = request.get_json()
    if not data or 'content' not in data:
        return jsonify(_ERR_NO_CONTENT), 400
    
    try:
        compile(data['content'], '<string>', 'exec')
    except SyntaxError as e:
        return jsonify({'success': False, 'message': f'配置文件语法错误: 第{e.lineno}行 - {e.msg}'}), 400
    
    with open(_CONFIG_NEW_PATH, 'w', encoding='utf-8') as f:
        f.write(data['content'])
    
    return jsonify({'success': True, 'message': '配置文件已保存，请重启框架以应用更改', 'file_path': _CONFIG_NEW_PATH})


def handle_check_pending_config():
    exists = os.path.exists(_CONFIG_NEW_PATH)
    modified_time = datetime.fromtimestamp(os.path.getmtime(_CONFIG_NEW_PATH)).strftime('%Y-%m-%d %H:%M:%S') if exists else None
    return jsonify({'success': True, 'pending': exists, 'modified_time': modified_time})


def handle_cancel_pending_config():
    if os.path.exists(_CONFIG_NEW_PATH):
        os.remove(_CONFIG_NEW_PATH)
        return jsonify({'success': True, 'message': '已取消待应用的配置'})
    return jsonify(_ERR_NO_PENDING), 404
