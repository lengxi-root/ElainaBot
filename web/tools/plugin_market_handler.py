import os
import re
import io
import httpx
import hashlib
import base64
import zipfile
import tempfile
import shutil
from flask import request, jsonify
from config import appid, ROBOT_QQ

# PHP 后端地址
PHP_API_URL = 'https://i.elaina.vin/api/elainabot/cjsc.php'
TIMEOUT = 30
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
            return response.json()
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


def handle_market_install():
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
            content_type = response.headers.get('content-type', '')
        
        is_zip = url.endswith('.zip') or 'zip' in content_type or content[:4] == b'PK\x03\x04'
        is_py = url.endswith('.py') or 'python' in content_type or (b'import ' in content[:500] or b'def ' in content[:500])
        
        if content[:100].lower().find(b'<!doctype html') != -1 or content[:100].lower().find(b'<html') != -1:
            return jsonify({'success': False, 'message': '下载链接无效，请使用 raw 文件链接或仓库压缩包链接'})
        
        if is_zip:
            result = install_zip_plugin(content, plugin_name)
        elif is_py:
            result = install_py_plugin(content, plugin_name, url)
        else:
            return jsonify({'success': False, 'message': '不支持的文件类型，仅支持 .py 或 .zip'})
        return jsonify(result)
    except httpx.TimeoutException:
        return jsonify({'success': False, 'message': '下载超时'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'安装失败: {str(e)}'})


def convert_github_url(url):
    if 'raw.githubusercontent.com' in url or '/raw/' in url or '/archive/' in url:
        return url
    
    # blob 链接转 raw
    blob_match = re.match(r'https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)', url)
    if blob_match:
        user, repo, branch, path = blob_match.groups()
        return f'https://raw.githubusercontent.com/{user}/{repo}/{branch}/{path}'
    
    # tree 链接转 archive
    tree_match = re.match(r'https?://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/?$', url)
    if tree_match:
        user, repo, branch = tree_match.groups()
        return f'https://github.com/{user}/{repo}/archive/refs/heads/{branch}.zip'
    
    # 仓库主页转 archive
    repo_match = re.match(r'https?://github\.com/([^/]+)/([^/]+)/?$', url)
    if repo_match:
        user, repo = repo_match.groups()
        repo = repo.replace('.git', '')
        return f'https://github.com/{user}/{repo}/archive/refs/heads/main.zip'
    
    return url


def install_zip_plugin(content, plugin_name):
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        tmp_extract_dir = tempfile.mkdtemp()
        installed_files = []
        try:
            with zipfile.ZipFile(tmp_path, 'r') as zf:
                zf.extractall(tmp_extract_dir)
            items = [i for i in os.listdir(tmp_extract_dir) if not i.startswith('__MACOSX') and not i.startswith('.')]
            
            if len(items) == 1 and os.path.isdir(os.path.join(tmp_extract_dir, items[0])):
                # zip 内有一个根文件夹，将其内容合并到 plugins 对应目录
                src_dir = os.path.join(tmp_extract_dir, items[0])
                folder_name = items[0]
                dest_dir = os.path.join(PLUGINS_DIR, folder_name)
                os.makedirs(dest_dir, exist_ok=True)
                # 合并文件，不删除已有文件
                for item in os.listdir(src_dir):
                    src = os.path.join(src_dir, item)
                    dst = os.path.join(dest_dir, item)
                    if os.path.isdir(src):
                        if os.path.exists(dst):
                            # 递归合并子目录
                            merge_directories(src, dst)
                        else:
                            shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                    installed_files.append(item)
                return {'success': True, 'message': f'插件已安装到 plugins/{folder_name}，新增/更新: {", ".join(installed_files)}', 'path': folder_name}
            else:
                # 多个文件/文件夹，创建以插件名命名的目录
                safe_name = "".join(c for c in plugin_name if c.isalnum() or c in ('_', '-', ' ')).strip() or 'new_plugin'
                dest_dir = os.path.join(PLUGINS_DIR, safe_name)
                os.makedirs(dest_dir, exist_ok=True)
                for item in items:
                    src = os.path.join(tmp_extract_dir, item)
                    dst = os.path.join(dest_dir, item)
                    if os.path.isdir(src):
                        if os.path.exists(dst):
                            merge_directories(src, dst)
                        else:
                            shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
                    installed_files.append(item)
                return {'success': True, 'message': f'插件已安装到 plugins/{safe_name}，新增/更新: {", ".join(installed_files)}', 'path': safe_name}
        finally:
            os.unlink(tmp_path)
            shutil.rmtree(tmp_extract_dir, ignore_errors=True)
    except zipfile.BadZipFile:
        return {'success': False, 'message': '无效的压缩包文件'}
    except Exception as e:
        return {'success': False, 'message': f'解压失败: {str(e)}'}


def merge_directories(src, dst):
    for item in os.listdir(src):
        s = os.path.join(src, item)
        d = os.path.join(dst, item)
        if os.path.isdir(s):
            if os.path.exists(d):
                merge_directories(s, d)
            else:
                shutil.copytree(s, d)
        else:
            shutil.copy2(s, d)


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


def handle_market_local_plugins():
    try:
        plugins = []
        if not os.path.exists(PLUGINS_DIR):
            return jsonify({'success': True, 'plugins': []})
        
        for item in os.listdir(PLUGINS_DIR):
            item_path = os.path.join(PLUGINS_DIR, item)
            if item.startswith('.') or item.startswith('__'):
                continue
            
            if os.path.isdir(item_path):
                py_files = [f for f in os.listdir(item_path) if f.endswith('.py') and not f.startswith('__')]
                if py_files:
                    plugins.append({
                        'name': item,
                        'type': 'folder',
                        'files': py_files,
                        'path': item,
                        'display': f'📁 {item} (文件夹)'
                    })
                    for py_file in py_files:
                        plugins.append({
                            'name': f'{item}/{py_file.replace(".py", "")}',
                            'type': 'file',
                            'files': [py_file],
                            'path': f'{item}/{py_file}',
                            'display': f'  📄 {item}/{py_file}'
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
    
    try:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            if os.path.isdir(full_path):
                for root, dirs, files in os.walk(full_path):
                    dirs[:] = [d for d in dirs if not d.startswith('__') and not d.startswith('.')]
                    for file in files:
                        if file.startswith('__') or file.startswith('.'):
                            continue
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, PLUGINS_DIR)
                        zf.write(file_path, arcname)
            else:
                zf.write(full_path, plugin_path)
        
        zip_buffer.seek(0)
        zip_base64 = base64.b64encode(zip_buffer.getvalue()).decode()
        
        submit_data = {
            'name': plugin_name, 'description': description, 'user_key': user_key,
            'version': version, 'category': category, 'tags': tags,
            'author_token': generate_author_token(), 'submit_appid': appid,
            'upload_type': 'local', 'plugin_data': zip_base64,
            'plugin_filename': f'{plugin_path}.zip' if os.path.isdir(full_path) else plugin_path
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


def handle_market_update_local():
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
    
    try:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            if os.path.isdir(full_path):
                for root, dirs, files in os.walk(full_path):
                    dirs[:] = [d for d in dirs if not d.startswith('__') and not d.startswith('.')]
                    for file in files:
                        if file.startswith('__') or file.startswith('.'):
                            continue
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, PLUGINS_DIR)
                        zf.write(file_path, arcname)
            else:
                zf.write(full_path, plugin_path)
        zip_buffer.seek(0)
        zip_base64 = base64.b64encode(zip_buffer.getvalue()).decode()
        submit_data = {
            'plugin_id': plugin_id, 'name': plugin_name, 'description': description,
            'user_key': user_key, 'version': version, 'category': category, 'tags': tags,
            'author_token': generate_author_token(), 'submit_appid': appid,
            'plugin_data': zip_base64, 'plugin_filename': f'{plugin_path}.zip' if os.path.isdir(full_path) else plugin_path
        }
        return jsonify(call_php('author_update_local', submit_data))
    except Exception as e:
        return jsonify({'success': False, 'message': f'上传失败: {str(e)}'})
