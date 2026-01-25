import os
import re
import httpx
import hashlib
import base64
from flask import request, jsonify
from config import appid, ROBOT_QQ

# PHP 后端地址
PHP_API_URL = 'https://i.elaina.vin/api/elainabot/cjsc.php'
TIMEOUT = 120  # 增加超时时间，上传大文件需要更长时间
PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'plugins')


def generate_author_token():
    raw = f"{appid}:{ROBOT_QQ}"
    md5_hash = hashlib.md5(raw.encode()).hexdigest()
    token = base64.b64encode(f"{appid}_{md5_hash[:16]}".encode()).decode()
    return token


def call_php(action, data=None, params=None, token=None):
    try:
        headers = {}
        if token:
            headers['X-Admin-Token'] = token
        url = f"{PHP_API_URL}?action={action}"
        if params:
            for k, v in params.items():
                url += f"&{k}={v}"
        with httpx.Client(timeout=TIMEOUT, verify=False) as client:
            if data:
                response = client.post(url, json=data, headers=headers)
            else:
                response = client.get(url, headers=headers)
            
            # 检查响应状态
            if response.status_code != 200:
                return {'success': False, 'message': f'HTTP错误: {response.status_code}'}
            
            # 检查响应是否为空
            if not response.text or not response.text.strip():
                return {'success': False, 'message': 'PHP返回空响应'}
            
            try:
                return response.json()
            except Exception as json_err:
                # 返回原始响应用于调试
                return {'success': False, 'message': f'JSON解析失败: {response.text[:200]}'}
                
    except httpx.TimeoutException:
        return {'success': False, 'message': '请求超时'}
    except Exception as e:
        return {'success': False, 'message': f'请求失败: {str(e)}'}


def handle_market_submit():
    data = request.json or {}
    data['author_token'] = generate_author_token()
    data['submit_appid'] = appid
    return jsonify(call_php('submit', data))


def handle_market_list():
    params = {k: v for k, v in {
        'category': request.args.get('category', ''),
        'status': request.args.get('status', ''),
        'search': request.args.get('search', '')
    }.items() if v}
    return jsonify(call_php('list', params=params))


def handle_market_pending():
    token = request.headers.get('X-Admin-Token') or request.args.get('token')
    return jsonify(call_php('pending', token=token))


def handle_market_review():
    token = request.headers.get('X-Admin-Token') or (request.json or {}).get('token')
    return jsonify(call_php('review', request.json or {}, token=token))


def handle_market_update_status():
    token = request.headers.get('X-Admin-Token') or (request.json or {}).get('token')
    return jsonify(call_php('update_status', request.json or {}, token=token))


def handle_market_delete():
    token = request.headers.get('X-Admin-Token') or (request.json or {}).get('token')
    return jsonify(call_php('delete', request.json or {}, token=token))


def handle_market_categories():
    return jsonify(call_php('categories'))


def handle_market_export():
    return jsonify(call_php('export'))


def handle_market_download():
    return jsonify(call_php('download', request.json or {}))


def handle_market_preview():
    """预览插件"""
    data = request.json or {}
    url = data.get('url', '')
    use_proxy = data.get('use_proxy', False)
    
    if not url:
        return jsonify({'success': False, 'message': '缺少下载链接'})
    
    url = convert_github_url(url)
    
    if use_proxy and ('github.com' in url or 'githubusercontent.com' in url):
        if 'raw.githubusercontent.com' in url:
            url = url.replace('https://raw.githubusercontent.com', 'https://ghfast.top/https://raw.githubusercontent.com')
        elif 'github.com' in url:
            url = url.replace('https://github.com', 'https://ghfast.top/https://github.com')
    
    try:
        with httpx.Client(timeout=30, verify=False, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code != 200:
                return jsonify({'success': False, 'message': f'下载失败: HTTP {response.status_code}'})
            content = response.content
        
        if content[:100].lower().find(b'<!doctype html') != -1 or content[:100].lower().find(b'<html') != -1:
            return jsonify({'success': False, 'message': '下载链接无效'})
        
        # 检测是否为压缩包
        if len(content) >= 4 and content[:4] == b'PK\x03\x04':
            return preview_zip_content(content, url)
        
        # 检测是否为 Python 文件
        is_py = url.endswith('.py') or (b'import ' in content[:500] or b'def ' in content[:500] or b'class ' in content[:500])
        
        if is_py:
            try:
                code = content.decode('utf-8', errors='replace')
            except:
                code = content.decode('gbk', errors='replace')
            filename = url.split('/')[-1].split('?')[0]
            if not filename.endswith('.py'):
                filename = 'plugin.py'
            return jsonify({'success': True, 'type': 'py', 'filename': filename, 'content': code, 'size': len(code)})
        else:
            return jsonify({'success': False, 'message': '不支持的文件类型'})
    except httpx.TimeoutException:
        return jsonify({'success': False, 'message': '下载超时'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'预览失败: {str(e)}'})


def preview_zip_content(content, url):
    """预览压缩包内容"""
    import zipfile
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
            files = []
            py_files = [f for f in zf.namelist() if f.endswith('.py') and not f.startswith('__') and '/__pycache__/' not in f]
            for py_file in py_files[:10]:
                try:
                    file_content = zf.read(py_file).decode('utf-8', errors='replace')
                    files.append({'name': py_file, 'content': file_content[:5000], 'size': len(file_content)})
                except:
                    pass
            return jsonify({'success': True, 'type': 'zip', 'files': files, 'total_files': len(py_files)})
    except Exception as e:
        return jsonify({'success': False, 'message': f'解析压缩包失败: {str(e)}'})


def handle_market_install():
    """安装插件"""
    data = request.json or {}
    url = data.get('url', '')
    plugin_name = data.get('name', 'unknown_plugin')
    use_proxy = data.get('use_proxy', False)
    
    if not url:
        return jsonify({'success': False, 'message': '缺少下载链接'})
    
    url = convert_github_url(url)
    
    if use_proxy and ('github.com' in url or 'githubusercontent.com' in url):
        if 'raw.githubusercontent.com' in url:
            url = url.replace('https://raw.githubusercontent.com', 'https://ghfast.top/https://raw.githubusercontent.com')
        elif 'github.com' in url:
            url = url.replace('https://github.com', 'https://ghfast.top/https://github.com')
    
    try:
        with httpx.Client(timeout=60, verify=False, follow_redirects=True) as client:
            response = client.get(url)
            if response.status_code != 200:
                return jsonify({'success': False, 'message': f'下载失败: HTTP {response.status_code}'})
            content = response.content
        
        if content[:100].lower().find(b'<!doctype html') != -1 or content[:100].lower().find(b'<html') != -1:
            return jsonify({'success': False, 'message': '下载链接无效'})
        
        # 检测是否为压缩包
        if len(content) >= 4 and content[:4] == b'PK\x03\x04':
            result = install_zip_plugin(content, plugin_name)
            return jsonify(result)
        
        # 检测是否为 Python 文件
        is_py = url.endswith('.py') or (b'import ' in content[:500] or b'def ' in content[:500] or b'class ' in content[:500])
        
        if is_py:
            result = install_py_plugin(content, plugin_name, url)
            return jsonify(result)
        else:
            return jsonify({'success': False, 'message': '不支持的文件类型'})
    except httpx.TimeoutException:
        return jsonify({'success': False, 'message': '下载超时'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'安装失败: {str(e)}'})


def convert_github_url(url):
    """转换 GitHub URL 为 raw 链接（仅支持单文件）"""
    if 'raw.githubusercontent.com' in url or '/raw/' in url:
        return url
    
    # blob 链接转 raw
    blob_match = re.match(r'https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)', url)
    if blob_match:
        user, repo, branch, path = blob_match.groups()
        return f'https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}'
    
    # 不再支持 tree/仓库主页转 archive，直接返回原链接让后续逻辑报错
    return url


def install_py_plugin(content, plugin_name, url):
    try:
        filename = url.split('/')[-1].split('?')[0]
        if not filename.endswith('.py'):
            filename = f"{plugin_name}.py"
        safe_name = "".join(c for c in plugin_name if c.isalnum() or c in ('_', '-', ' ')).strip() or filename.replace('.py', '')
        dest_dir = os.path.join(PLUGINS_DIR, safe_name)
        os.makedirs(dest_dir, exist_ok=True)
        dest_file = os.path.join(dest_dir, filename)
        with open(dest_file, 'wb') as f:
            f.write(content)
        return {'success': True, 'message': f'插件已安装到 plugins/{safe_name}/{filename}', 'path': f'{safe_name}/{filename}'}
    except Exception as e:
        return {'success': False, 'message': f'安装失败: {str(e)}'}


def install_zip_plugin(content, plugin_name):
    """安装压缩包插件"""
    import zipfile
    import io
    try:
        safe_name = "".join(c for c in plugin_name if c.isalnum() or c in ('_', '-', ' ')).strip() or 'unknown_plugin'
        dest_dir = os.path.join(PLUGINS_DIR, safe_name)
        
        with zipfile.ZipFile(io.BytesIO(content), 'r') as zf:
            file_list = zf.namelist()
            if not file_list:
                return {'success': False, 'message': '压缩包为空'}
            
            # 检查是否有根目录
            root_dirs = set()
            for f in file_list:
                parts = f.split('/')
                if len(parts) > 1 and parts[0]:
                    root_dirs.add(parts[0])
            
            # 如果只有一个根目录，去掉它
            strip_root = len(root_dirs) == 1
            root_prefix = list(root_dirs)[0] + '/' if strip_root else ''
            
            os.makedirs(dest_dir, exist_ok=True)
            extracted = []
            
            for file_path in file_list:
                if file_path.endswith('/') or '__pycache__' in file_path or file_path.startswith('__'):
                    continue
                
                # 去掉根目录前缀
                rel_path = file_path[len(root_prefix):] if strip_root and file_path.startswith(root_prefix) else file_path
                if not rel_path:
                    continue
                
                dest_path = os.path.join(dest_dir, rel_path)
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                
                with zf.open(file_path) as src, open(dest_path, 'wb') as dst:
                    dst.write(src.read())
                extracted.append(rel_path)
            
            py_count = len([f for f in extracted if f.endswith('.py')])
            return {'success': True, 'message': f'插件已安装到 plugins/{safe_name}/ ({py_count} 个 Python 文件)', 'path': safe_name, 'files': extracted[:20]}
    except Exception as e:
        return {'success': False, 'message': f'安装压缩包失败: {str(e)}'}


def handle_market_local_plugins():
    """列出本地插件 - 仅列出单个 .py 文件（不再支持文件夹）"""
    try:
        plugins = []
        if not os.path.exists(PLUGINS_DIR):
            return jsonify({'success': True, 'plugins': []})
        
        for item in os.listdir(PLUGINS_DIR):
            item_path = os.path.join(PLUGINS_DIR, item)
            if item.startswith('.') or item.startswith('__'):
                continue
            
            # 只列出文件夹内的单个 .py 文件
            if os.path.isdir(item_path):
                py_files = [f for f in os.listdir(item_path) if f.endswith('.py') and not f.startswith('__')]
                for py_file in py_files:
                    plugins.append({
                        'name': f'{item}/{py_file.replace(".py", "")}',
                        'type': 'file',
                        'files': [py_file],
                        'path': f'{item}/{py_file}',
                        'display': f'📄 {item}/{py_file}'
                    })
            elif item.endswith('.py'):
                plugins.append({
                    'name': item.replace('.py', ''),
                    'type': 'file',
                    'files': [item],
                    'path': item,
                    'display': f'📄 {item}'
                })
        
        return jsonify({'success': True, 'plugins': plugins})
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取插件列表失败: {str(e)}'})


def handle_market_upload_local():
    data = request.json or {}
    plugin_path = data.get('plugin_path', '')
    plugin_name = data.get('name', '')
    description = data.get('description', '')
    user_key = data.get('user_key', '')
    version = data.get('version', '1.0.0')
    category = data.get('category', '其他')
    tags = data.get('tags', [])
    
    if not plugin_path or not plugin_name or not description:
        return jsonify({'success': False, 'message': '请填写完整的插件信息'})
    
    full_path = os.path.join(PLUGINS_DIR, plugin_path)
    if not os.path.exists(full_path):
        return jsonify({'success': False, 'message': '插件不存在'})
    
    # 仅支持单个 .py 文件
    if os.path.isdir(full_path):
        return jsonify({'success': False, 'message': '不再支持上传插件文件夹，请上传单个 .py 文件'})
    
    if not full_path.endswith('.py'):
        return jsonify({'success': False, 'message': '仅支持 .py 文件'})
    
    try:
        with open(full_path, 'rb') as f:
            content = f.read()
        
        file_base64 = base64.b64encode(content).decode()
        
        submit_data = {
            'name': plugin_name, 'description': description, 'user_key': user_key,
            'version': version, 'category': category, 'tags': tags,
            'author_token': generate_author_token(), 'submit_appid': appid,
            'upload_type': 'local', 'plugin_data': file_base64,
            'plugin_filename': os.path.basename(plugin_path)
        }
        return jsonify(call_php('submit_local', submit_data))
    except Exception as e:
        return jsonify({'success': False, 'message': f'上传失败: {str(e)}'})


def handle_market_register():
    data = request.json or {}
    data['robot_qq'] = ROBOT_QQ
    data['appid'] = appid
    return jsonify(call_php('register', data))


def handle_market_login():
    return jsonify(call_php('login', request.json or {}))


def handle_market_user_info():
    return jsonify(call_php('user_info', request.json or {}))


def handle_market_plugin_detail():
    return jsonify(call_php('plugin_detail', request.json or {}))


def handle_market_author_update():
    return jsonify(call_php('author_update', request.json or {}))


def handle_market_author_delete():
    return jsonify(call_php('author_delete', request.json or {}))


def handle_market_get_source():
    """获取插件源码用于编辑"""
    return jsonify(call_php('get_source', request.json or {}))


def handle_market_save_source():
    """保存编辑后的插件源码"""
    return jsonify(call_php('save_source', request.json or {}))


def handle_market_upload_direct():
    """直接上传文件（前端已压缩）"""
    data = request.json or {}
    plugin_name = data.get('name', '')
    description = data.get('description', '')
    user_key = data.get('user_key', '')
    version = data.get('version', '1.0.0')
    category = data.get('category', '其他')
    tags = data.get('tags', [])
    plugin_data = data.get('plugin_data', '')
    plugin_filename = data.get('plugin_filename', 'plugin.zip')
    
    if not plugin_name or not description:
        return jsonify({'success': False, 'message': '请填写插件名称和描述'})
    
    if not plugin_data:
        return jsonify({'success': False, 'message': '缺少插件文件数据'})
    
    if not user_key:
        return jsonify({'success': False, 'message': '请先登录'})
    
    # 直接提交到PHP后端
    submit_data = {
        'name': plugin_name,
        'description': description,
        'user_key': user_key,
        'version': version,
        'category': category,
        'tags': tags,
        'author_token': generate_author_token(),
        'submit_appid': appid,
        'upload_type': 'direct',
        'plugin_data': plugin_data,
        'plugin_filename': plugin_filename
    }
    
    return jsonify(call_php('submit_local', submit_data))


def handle_market_update_plugin_code():
    """更新现有插件的代码（不创建新插件）"""
    data = request.json or {}
    plugin_id = data.get('plugin_id', '')
    user_key = data.get('user_key', '')
    plugin_data = data.get('plugin_data', '')
    plugin_filename = data.get('plugin_filename', 'plugin.zip')
    
    if not plugin_id:
        return jsonify({'success': False, 'message': '缺少插件ID'})
    
    if not plugin_data:
        return jsonify({'success': False, 'message': '缺少插件文件数据'})
    
    if not user_key:
        return jsonify({'success': False, 'message': '请先登录'})
    
    # 调用PHP更新代码
    update_data = {
        'plugin_id': plugin_id,
        'user_key': user_key,
        'plugin_data': plugin_data,
        'plugin_filename': plugin_filename
    }
    
    return jsonify(call_php('update_plugin_code', update_data))


def handle_local_plugin_read():
    """读取本地插件文件内容"""
    data = request.json or {}
    plugin_path = data.get('path', '')
    
    if not plugin_path:
        return jsonify({'success': False, 'message': '缺少插件路径'})
    
    # 安全检查，防止路径遍历
    if '..' in plugin_path or plugin_path.startswith('/') or plugin_path.startswith('\\'):
        return jsonify({'success': False, 'message': '无效的路径'})
    
    full_path = os.path.join(PLUGINS_DIR, plugin_path)
    
    if not os.path.exists(full_path):
        return jsonify({'success': False, 'message': '文件不存在'})
    
    try:
        if os.path.isfile(full_path):
            # 单个文件
            if not full_path.endswith('.py'):
                return jsonify({'success': False, 'message': '仅支持编辑 Python 文件'})
            
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            return jsonify({
                'success': True,
                'type': 'single',
                'files': [{
                    'name': os.path.basename(plugin_path),
                    'path': plugin_path,
                    'content': content,
                    'size': len(content)
                }]
            })
        elif os.path.isdir(full_path):
            # 目录
            files = []
            for root, dirs, filenames in os.walk(full_path):
                # 过滤隐藏目录和缓存目录
                dirs[:] = [d for d in dirs if not d.startswith('__') and not d.startswith('.')]
                for filename in filenames:
                    if filename.startswith('__') or filename.startswith('.'):
                        continue
                    file_path = os.path.join(root, filename)
                    rel_path = os.path.relpath(file_path, PLUGINS_DIR)
                    
                    if filename.endswith('.py'):
                        try:
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                            files.append({
                                'name': filename,
                                'path': rel_path,
                                'content': content,
                                'size': len(content),
                                'editable': True
                            })
                        except Exception as e:
                            files.append({
                                'name': filename,
                                'path': rel_path,
                                'content': None,
                                'size': os.path.getsize(file_path),
                                'editable': False,
                                'error': str(e)
                            })
                    else:
                        files.append({
                            'name': filename,
                            'path': rel_path,
                            'content': None,
                            'size': os.path.getsize(file_path),
                            'editable': False
                        })
            
            return jsonify({
                'success': True,
                'type': 'folder',
                'files': files
            })
        else:
            return jsonify({'success': False, 'message': '未知的文件类型'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'读取失败: {str(e)}'})


def handle_local_plugin_save():
    """保存本地插件文件"""
    data = request.json or {}
    files = data.get('files', [])
    
    if not files:
        return jsonify({'success': False, 'message': '没有要保存的文件'})
    
    saved = []
    errors = []
    
    for file_info in files:
        file_path = file_info.get('path', '')
        content = file_info.get('content')
        
        if not file_path or content is None:
            continue
        
        # 安全检查
        if '..' in file_path or file_path.startswith('/') or file_path.startswith('\\'):
            errors.append(f'{file_path}: 无效的路径')
            continue
        
        if not file_path.endswith('.py'):
            errors.append(f'{file_path}: 仅支持保存 Python 文件')
            continue
        
        full_path = os.path.join(PLUGINS_DIR, file_path)
        
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            saved.append(file_path)
        except Exception as e:
            errors.append(f'{file_path}: {str(e)}')
    
    if errors and not saved:
        return jsonify({'success': False, 'message': '保存失败: ' + '; '.join(errors)})
    
    return jsonify({
        'success': True,
        'message': f'已保存 {len(saved)} 个文件' + (f'，{len(errors)} 个失败' if errors else ''),
        'saved': saved,
        'errors': errors
    })


def handle_market_update_local():
    """更新本地插件 - 仅支持单个 .py 文件"""
    data = request.json or {}
    plugin_id = data.get('plugin_id', '')
    plugin_path = data.get('plugin_path', '')
    plugin_name = data.get('name', '')
    description = data.get('description', '')
    user_key = data.get('user_key', '')
    version = data.get('version', '1.0.0')
    category = data.get('category', '其他')
    tags = data.get('tags', [])
    
    if not plugin_id or not plugin_path or not description:
        return jsonify({'success': False, 'message': '请填写完整的插件信息'})
    
    full_path = os.path.join(PLUGINS_DIR, plugin_path)
    if not os.path.exists(full_path):
        return jsonify({'success': False, 'message': '插件不存在'})
    
    # 仅支持单个 .py 文件
    if os.path.isdir(full_path):
        return jsonify({'success': False, 'message': '不再支持上传插件文件夹，请上传单个 .py 文件'})
    
    if not full_path.endswith('.py'):
        return jsonify({'success': False, 'message': '仅支持 .py 文件'})
    
    try:
        with open(full_path, 'rb') as f:
            content = f.read()
        
        file_base64 = base64.b64encode(content).decode()
        
        submit_data = {
            'plugin_id': plugin_id, 'name': plugin_name, 'description': description,
            'user_key': user_key, 'version': version, 'category': category, 'tags': tags,
            'author_token': generate_author_token(), 'submit_appid': appid,
            'plugin_data': file_base64, 'plugin_filename': os.path.basename(plugin_path)
        }
        return jsonify(call_php('author_update_local', submit_data))
    except Exception as e:
        return jsonify({'success': False, 'message': f'上传失败: {str(e)}'})
