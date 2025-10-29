import os, re, logging, traceback, importlib.util
from datetime import datetime
from flask import request, jsonify

plugins_info = []

def get_plugins_dir():
    script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(script_dir, 'plugins')

def validate_plugin_path(plugin_path, plugins_dir):
    abs_plugin_path = os.path.abspath(plugin_path)
    if not abs_plugin_path.startswith(os.path.abspath(plugins_dir)):
        return False, abs_plugin_path
    return True, abs_plugin_path

def process_plugin_module(module, plugin_path, module_name, is_system=False, dir_name=None):
    plugin_info_list, plugin_classes_found = [], False
    last_modified_str = datetime.fromtimestamp(os.path.getmtime(plugin_path)).strftime('%Y-%m-%d %H:%M:%S') if os.path.exists(plugin_path) else ""
    is_web_plugin = plugin_path.endswith('.web.py')
    normalized_path = plugin_path.replace('\\', '/')
    
    for attr_name in dir(module):
        if attr_name.startswith('__') or not hasattr((attr := getattr(module, attr_name)), '__class__'):
            continue
        if isinstance(attr, type) and attr.__module__ == module.__name__ and hasattr(attr, 'get_regex_handlers'):
            plugin_classes_found = True
            name = f"{'system' if is_system else dir_name}/{module_name}/{attr_name}"
            plugin_info = {
                'name': name,
                'class_name': attr_name,
                'status': 'loaded',
                'error': '',
                'path': normalized_path,
                'is_system': is_system,
                'directory': dir_name,
                'last_modified': last_modified_str,
                'is_web_plugin': is_web_plugin
            }
            
            try:
                handlers = attr.get_regex_handlers()
                plugin_info.update({
                    'handlers': len(handlers) if handlers else 0,
                    'handlers_list': list(handlers.keys()) if handlers else [],
                    'priority': getattr(attr, 'priority', 10),
                    'handlers_owner_only': {p: (h.get('owner_only', False) if isinstance(h, dict) else False) for p, h in handlers.items()},
                    'handlers_group_only': {p: (h.get('group_only', False) if isinstance(h, dict) else False) for p, h in handlers.items()}
                })
                
                if is_web_plugin and hasattr(attr, 'get_web_routes') and callable(getattr(attr, 'get_web_routes')):
                    try:
                        web_route_info = attr.get_web_routes()
                        if web_route_info and isinstance(web_route_info, dict):
                            plugin_info['web_route'] = {
                                'path': web_route_info.get('path', ''),
                                'menu_name': web_route_info.get('menu_name', ''),
                                'menu_icon': web_route_info.get('menu_icon', 'bi-puzzle'),
                                'description': web_route_info.get('description', ''),
                                'priority': web_route_info.get('priority', 100)
                            }
                    except Exception as e:
                        logging.warning(f"获取插件 {attr_name} 的web路由信息失败: {str(e)}")
            except Exception as e:
                plugin_info.update({
                    'status': 'error',
                    'error': f"获取处理器失败: {str(e)}",
                    'traceback': traceback.format_exc()
                })
            
            plugin_info_list.append(plugin_info)
    
    if not plugin_classes_found:
        plugin_info_list.append({
            'name': f"{'system/' if is_system else ''}{dir_name}/{module_name}",
            'class_name': 'unknown',
            'status': 'error',
            'error': '未在模块中找到有效的插件类',
            'path': normalized_path,
            'directory': dir_name,
            'last_modified': last_modified_str,
            'is_web_plugin': is_web_plugin
        })
    
    return plugin_info_list

def load_plugin_module(plugin_file, module_name, is_system=False):
    normalized_path = plugin_file.replace('\\', '/')
    
    try:
        dir_name = os.path.basename(os.path.dirname(plugin_file))
        spec = importlib.util.spec_from_file_location(f"plugins.{dir_name}.{module_name}", plugin_file)
        
        if not spec or not spec.loader:
            return [{
                'name': f"{dir_name}/{module_name}",
                'class_name': 'unknown',
                'status': 'error',
                'error': '无法加载插件文件',
                'path': normalized_path,
                'directory': dir_name
            }]
        
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return process_plugin_module(module, plugin_file, module_name, is_system=is_system, dir_name=dir_name)
    
    except Exception as e:
        return [{
            'name': f"{os.path.basename(os.path.dirname(plugin_file))}/{module_name}",
            'class_name': 'unknown',
            'status': 'error',
            'error': str(e),
            'path': normalized_path,
            'directory': os.path.basename(os.path.dirname(plugin_file)),
            'traceback': traceback.format_exc()
        }]

def scan_plugins_internal():
    global plugins_info
    plugins_info = []
    
    plugins_dir = get_plugins_dir()
    
    for dir_name in os.listdir(plugins_dir):
        plugin_dir = os.path.join(plugins_dir, dir_name)
        if not os.path.isdir(plugin_dir):
            continue
        
        py_files = [f for f in os.listdir(plugin_dir) if f.endswith('.py') and f != '__init__.py']
        
        for py_file in py_files:
            plugin_file = os.path.join(plugin_dir, py_file)
            plugin_name = os.path.splitext(py_file)[0]
            
            plugin_info_list = load_plugin_module(
                plugin_file,
                plugin_name,
                is_system=(dir_name == 'system')
            )
            
            for plugin_info in plugin_info_list:
                plugin_info['enabled'] = True
            
            plugins_info.extend(plugin_info_list)
        
        ban_files = [f for f in os.listdir(plugin_dir) if f.endswith('.py.ban')]
        
        for ban_file in ban_files:
            plugin_file = os.path.join(plugin_dir, ban_file)
            plugin_name = os.path.splitext(os.path.splitext(ban_file)[0])[0]
            last_modified_str = datetime.fromtimestamp(os.path.getmtime(plugin_file)).strftime('%Y-%m-%d %H:%M:%S')
            
            plugins_info.append({
                'name': f"{dir_name}/{plugin_name}",
                'class_name': 'unknown',
                'status': 'disabled',
                'error': '插件已禁用',
                'path': plugin_file.replace('\\', '/'),
                'is_system': (dir_name == 'system'),
                'directory': dir_name,
                'last_modified': last_modified_str,
                'enabled': False,
                'handlers': 0,
                'handlers_list': []
            })
    
    plugins_info.sort(key=lambda x: (0 if x['status'] == 'loaded' else (1 if x['status'] == 'disabled' else 2)))
    return plugins_info

def handle_toggle_plugin(add_framework_log):
    data = request.get_json()
    plugin_path = data.get('path')
    action = data.get('action')
    
    if not plugin_path or not action or action not in ['enable', 'disable']:
        return jsonify({'success': False, 'message': '参数错误'}), 400
    
    plugin_path = os.path.normpath(plugin_path)
    
    plugins_dir = get_plugins_dir()
    is_valid, abs_plugin_path = validate_plugin_path(plugin_path, plugins_dir)
    
    if not is_valid:
        return jsonify({'success': False, 'message': '无效的插件路径'}), 403
    
    if action == 'disable':
        if not plugin_path.endswith('.py'):
            return jsonify({'success': False, 'message': '只能禁用 .py 文件'}), 400
        
        new_path = plugin_path + '.ban'
        if os.path.exists(new_path):
            return jsonify({'success': False, 'message': '禁用文件已存在'}), 409
        
        os.rename(plugin_path, new_path)
        add_framework_log(f"插件已禁用: {os.path.basename(plugin_path)}")
        return jsonify({'success': True, 'message': '插件已禁用', 'new_path': new_path.replace('\\', '/')})
    
    else:
        if not plugin_path.endswith('.py.ban'):
            return jsonify({'success': False, 'message': '只能启用 .py.ban 文件'}), 400
        
        new_path = plugin_path[:-4]
        if os.path.exists(new_path):
            return jsonify({'success': False, 'message': '启用文件已存在'}), 409
        
        os.rename(plugin_path, new_path)
        add_framework_log(f"插件已启用: {os.path.basename(new_path)}")
        return jsonify({'success': True, 'message': '插件已启用', 'new_path': new_path.replace('\\', '/')})

def handle_read_plugin():
    data = request.get_json()
    plugin_path = data.get('path')
    
    if not plugin_path:
        return jsonify({'success': False, 'message': '缺少插件路径'}), 400
    
    plugin_path = os.path.normpath(plugin_path)
    
    plugins_dir = get_plugins_dir()
    is_valid, abs_plugin_path = validate_plugin_path(plugin_path, plugins_dir)
    
    if not is_valid:
        return jsonify({'success': False, 'message': '无效的插件路径'}), 403
    
    if not os.path.isfile(abs_plugin_path):
        return jsonify({'success': False, 'message': '文件不存在'}), 404
    
    with open(abs_plugin_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    return jsonify({
        'success': True,
        'content': content,
        'path': plugin_path.replace('\\', '/'),
        'filename': os.path.basename(plugin_path)
    })

def handle_save_plugin(add_framework_log):
    data = request.get_json()
    plugin_path = data.get('path')
    content = data.get('content')
    
    if not plugin_path or content is None:
        return jsonify({'success': False, 'message': '缺少必要参数'}), 400
    
    plugin_path = os.path.normpath(plugin_path)
    
    plugins_dir = get_plugins_dir()
    is_valid, abs_plugin_path = validate_plugin_path(plugin_path, plugins_dir)
    
    if not is_valid:
        return jsonify({'success': False, 'message': '无效的插件路径'}), 403
    
    if os.path.exists(abs_plugin_path):
        import shutil
        shutil.copy2(abs_plugin_path, abs_plugin_path + '.backup')
    
    with open(abs_plugin_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    add_framework_log(f"插件已保存: {os.path.basename(plugin_path)}")
    
    return jsonify({'success': True, 'message': '插件保存成功'})

def handle_create_plugin(add_framework_log):
    data = request.get_json()
    directory = data.get('directory')
    filename = data.get('filename')
    
    if not directory or not filename:
        return jsonify({'success': False, 'message': '缺少必要参数'}), 400
    
    if not filename.endswith('.py'):
        filename += '.py'
    
    plugins_dir = get_plugins_dir()
    target_dir = os.path.join(plugins_dir, directory)
    
    if not os.path.abspath(target_dir).startswith(os.path.abspath(plugins_dir)):
        return jsonify({'success': False, 'message': '无效的目录路径'}), 403
    
    plugin_path = os.path.join(target_dir, filename)
    
    if os.path.exists(plugin_path):
        return jsonify({'success': False, 'message': '文件已存在'}), 409
    
    os.makedirs(target_dir, exist_ok=True)
    
    template = '''from core.plugin.PluginManager import Plugin

class plugin_name(Plugin):
    priority = 10
    
    @classmethod
    def get_regex_handlers(cls):
        return {
            r'^指令$': {'handler': 'handle_command', 'owner_only': False},
        }
    
    @staticmethod
    def handle_command(event):
        event.reply("Hello, World!")
'''
    
    with open(plugin_path, 'w', encoding='utf-8') as f:
        f.write(template)
    
    add_framework_log(f"新插件已创建: {filename}")
    
    return jsonify({'success': True, 'message': '插件创建成功', 'path': plugin_path.replace('\\', '/')})

def handle_create_plugin_folder(add_framework_log):
    data = request.get_json()
    parent_dir = data.get('parent_dir', '')
    folder_name = data.get('folder_name')
    
    if not folder_name:
        return jsonify({'success': False, 'message': '缺少文件夹名'}), 400
    
    plugins_dir = get_plugins_dir()
    target_dir = os.path.join(plugins_dir, parent_dir, folder_name) if parent_dir else os.path.join(plugins_dir, folder_name)
    
    if not os.path.abspath(target_dir).startswith(os.path.abspath(plugins_dir)):
        return jsonify({'success': False, 'message': '无效的目录路径'}), 403
    
    if os.path.exists(target_dir):
        return jsonify({'success': False, 'message': '文件夹已存在'}), 409
    
    os.makedirs(target_dir, exist_ok=True)
    add_framework_log(f"新文件夹已创建: {folder_name}")
    
    return jsonify({'success': True, 'message': '文件夹创建成功', 'path': target_dir.replace('\\', '/')})

def handle_get_plugin_folders():
    plugins_dir = get_plugins_dir()
    
    folders = []
    for item in os.listdir(plugins_dir):
        item_path = os.path.join(plugins_dir, item)
        if os.path.isdir(item_path) and not item.startswith('.') and not item.startswith('__'):
            folders.append({'name': item, 'path': item, 'display_name': item})
    
    folders.sort(key=lambda x: x['name'])
    
    return jsonify({'success': True, 'folders': folders})

def handle_upload_plugin(add_framework_log):
    if 'file' not in request.files:
        return jsonify({'success': False, 'message': '没有文件'}), 400
    
    file = request.files['file']
    directory = request.form.get('directory', 'alone')
    
    if file.filename == '' or not file.filename.endswith('.py'):
        return jsonify({'success': False, 'message': '只能上传 .py 文件'}), 400
    
    safe_filename = re.sub(r'[^\w\u4e00-\u9fa5\-\.]', '_', file.filename)
    
    plugins_dir = get_plugins_dir()
    target_dir = os.path.join(plugins_dir, directory)
    
    if not os.path.abspath(target_dir).startswith(os.path.abspath(plugins_dir)):
        return jsonify({'success': False, 'message': '无效的目录路径'}), 403
    
    plugin_path = os.path.join(target_dir, safe_filename)
    
    if os.path.exists(plugin_path):
        base_name = safe_filename[:-3]
        counter = 1
        while os.path.exists(plugin_path):
            plugin_path = os.path.join(target_dir, f"{base_name}_{counter}.py")
            counter += 1
        safe_filename = os.path.basename(plugin_path)
    
    os.makedirs(target_dir, exist_ok=True)
    file.save(plugin_path)
    add_framework_log(f"插件已上传: {safe_filename} 到 {directory}/")
    
    return jsonify({
        'success': True,
        'message': f'插件上传成功: {safe_filename}',
        'path': plugin_path.replace('\\', '/'),
        'filename': safe_filename
    })
