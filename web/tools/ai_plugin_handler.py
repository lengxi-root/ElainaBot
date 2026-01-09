import os
import re
import httpx
from flask import request, jsonify

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PLUGINS_DIR = os.path.join(BASE_DIR, 'plugins')
AI_SERVICE_URL = 'https://i.elaina.vin/api/elainabot/ai.php'
AI_TIMEOUT = 120


def call_php_service(action, data, model=None):
    try:
        payload = {'action': action, **data}
        if model:
            payload['model'] = model
        with httpx.Client(timeout=AI_TIMEOUT, verify=False) as client:
            response = client.post(AI_SERVICE_URL, json=payload)
            if response.status_code != 200:
                try:
                    error_body = response.text[:500]
                except:
                    error_body = '无法读取响应内容'
                return {'error': True, 'message': f'服务请求失败: HTTP {response.status_code}，响应: {error_body}'}
            try:
                return response.json()
            except Exception as e:
                return {'error': True, 'message': f'JSON 解析失败: {str(e)}，响应内容: {response.text[:500]}'}
    except httpx.TimeoutException:
        return {'error': True, 'message': 'AI 服务请求超时，请稍后重试'}
    except httpx.ConnectError as e:
        return {'error': True, 'message': f'无法连接到 AI 服务: {str(e)}'}
    except Exception as e:
        return {'error': True, 'message': f'请求失败: {type(e).__name__}: {str(e)}'}


def extract_code(text):
    code_match = re.search(r'```(?:python)?\n(.*?)```', text, re.DOTALL)
    return code_match.group(1).strip() if code_match else text.strip()


def handle_list_plugins():
    try:
        directory = request.json.get('directory') if request.json else None
        result = []
        skip_dirs = {'__pycache__', '.git', 'data', 'necessary'}
        
        if directory:
            dir_path = os.path.join(PLUGINS_DIR, directory)
            if not os.path.exists(dir_path):
                return jsonify({'success': False, 'message': f'目录不存在: {directory}'})
            files = [f for f in os.listdir(dir_path) if f.endswith('.py') and f != '__init__.py']
            result.append({'directory': directory, 'plugins': sorted(files)})
        else:
            for dir_name in sorted(os.listdir(PLUGINS_DIR)):
                if dir_name in skip_dirs:
                    continue
                dir_path = os.path.join(PLUGINS_DIR, dir_name)
                if os.path.isdir(dir_path):
                    files = [f for f in os.listdir(dir_path) if f.endswith('.py') and f != '__init__.py']
                    result.append({'directory': dir_name, 'plugins': sorted(files), 'count': len(files)})
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


def handle_read_plugin():
    try:
        data = request.json
        directory = data.get('directory')
        filename = data.get('filename')
        if not directory or not filename:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        if not filename.endswith('.py'):
            filename += '.py'
        file_path = os.path.join(PLUGINS_DIR, directory, filename)
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': f'文件不存在: {directory}/{filename}'})
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({'success': True, 'content': content, 'path': f'{directory}/{filename}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


def handle_ai_create_plugin():
    try:
        data = request.json
        directory = data.get('directory')
        filename = data.get('filename')
        description = data.get('description')
        model = data.get('model')
        if not directory or not filename or not description:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        filename_clean = filename[:-3] if filename.endswith('.py') else filename
        result = call_php_service('create', {'filename': filename_clean, 'description': description}, model)
        if result.get('error'):
            return jsonify({'success': False, 'message': result.get('message')})
        return jsonify({
            'success': True, 'code': result.get('code', ''), 'model': result.get('model', 'AI'),
            'directory': directory, 'filename': filename_clean + '.py'
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


def handle_ai_modify_plugin():
    try:
        data = request.json
        directory = data.get('directory')
        filename = data.get('filename')
        modification = data.get('modification')
        model = data.get('model')
        if not directory or not filename or not modification:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        if not filename.endswith('.py'):
            filename += '.py'
        file_path = os.path.join(PLUGINS_DIR, directory, filename)
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': f'文件不存在: {directory}/{filename}'})
        with open(file_path, 'r', encoding='utf-8') as f:
            current_code = f.read()
        result = call_php_service('modify', {'current_code': current_code, 'modification': modification}, model)
        if result.get('error'):
            return jsonify({'success': False, 'message': result.get('message')})
        return jsonify({
            'success': True, 'code': result.get('code', ''), 'model': result.get('model', 'AI'),
            'directory': directory, 'filename': filename
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


def handle_ai_add_feature():
    try:
        data = request.json
        directory = data.get('directory')
        filename = data.get('filename')
        feature = data.get('feature')
        model = data.get('model')
        if not directory or not filename or not feature:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        if not filename.endswith('.py'):
            filename += '.py'
        file_path = os.path.join(PLUGINS_DIR, directory, filename)
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': f'文件不存在: {directory}/{filename}'})
        with open(file_path, 'r', encoding='utf-8') as f:
            current_code = f.read()
        result = call_php_service('add_feature', {'current_code': current_code, 'feature': feature}, model)
        if result.get('error'):
            return jsonify({'success': False, 'message': result.get('message')})
        return jsonify({
            'success': True, 'code': result.get('code', ''), 'model': result.get('model', 'AI'),
            'directory': directory, 'filename': filename
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


def handle_ai_fix_plugin():
    try:
        data = request.json
        directory = data.get('directory')
        filename = data.get('filename')
        error_message = data.get('error_message')
        model = data.get('model')
        if not directory or not filename or not error_message:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        if not filename.endswith('.py'):
            filename += '.py'
        file_path = os.path.join(PLUGINS_DIR, directory, filename)
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'message': f'文件不存在: {directory}/{filename}'})
        with open(file_path, 'r', encoding='utf-8') as f:
            current_code = f.read()
        result = call_php_service('fix', {'current_code': current_code, 'error_message': error_message}, model)
        if result.get('error'):
            return jsonify({'success': False, 'message': result.get('message')})
        return jsonify({
            'success': True, 'code': result.get('code', ''), 'model': result.get('model', 'AI'),
            'directory': directory, 'filename': filename
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


def handle_save_ai_plugin():
    try:
        data = request.json
        directory = data.get('directory')
        filename = data.get('filename')
        code = data.get('code')
        if not directory or not filename or not code:
            return jsonify({'success': False, 'message': '缺少必要参数'})
        if not filename.endswith('.py'):
            filename += '.py'
        dir_path = os.path.join(PLUGINS_DIR, directory)
        if not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        file_path = os.path.join(dir_path, filename)
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                backup_content = f.read()
            with open(file_path + '.backup', 'w', encoding='utf-8') as f:
                f.write(backup_content)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(extract_code(code))
        return jsonify({'success': True, 'message': f'插件已保存: {directory}/{filename}', 'path': f'{directory}/{filename}'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


def handle_search_plugins():
    try:
        data = request.json
        keyword = data.get('keyword')
        directory = data.get('directory')
        if not keyword:
            return jsonify({'success': False, 'message': '缺少搜索关键字'})
        results = []
        dirs_to_search = [directory] if directory else os.listdir(PLUGINS_DIR)
        for dir_name in dirs_to_search:
            dir_path = os.path.join(PLUGINS_DIR, dir_name)
            if not os.path.isdir(dir_path):
                continue
            for filename in os.listdir(dir_path):
                if not filename.endswith('.py') or filename == '__init__.py':
                    continue
                file_path = os.path.join(dir_path, filename)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                    if keyword.lower() in content.lower():
                        lines = content.split('\n')
                        matches = [{'line': i, 'content': line.strip()[:80]} for i, line in enumerate(lines, 1) if keyword.lower() in line.lower()]
                        results.append({'directory': dir_name, 'filename': filename, 'matches': matches[:5]})
                except:
                    pass
        return jsonify({'success': True, 'results': results, 'count': len(results)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})


def handle_get_plugin_template():
    return jsonify({'success': True, 'template': ''})


def handle_get_ai_models():
    result = call_php_service('status', {})
    if result.get('success'):
        return jsonify({
            'success': True, 'models': result.get('models', []),
            'default_model': result.get('default_model', 'gpt-4o-mini'), 'status': 'online'
        })
    return jsonify({'success': False, 'message': result.get('message', 'AI 服务不可用')})


def handle_get_ai_config():
    return jsonify({'success': True, 'config': {'service': AI_SERVICE_URL}})


def handle_save_ai_config():
    return jsonify({'success': True, 'message': 'AI 服务由官方提供，无需配置'})
