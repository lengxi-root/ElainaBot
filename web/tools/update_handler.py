import threading, requests, os
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

def handle_apply_config_diff():
    """应用配置差异补全"""
    data = request.get_json() or {}
    missing = data.get('missing', [])
    if not missing:
        return jsonify({'success': False, 'message': '没有需要补全的配置项'})
    from web.tools.updater import get_updater
    result = get_updater().apply_config_diff(missing)
    return jsonify(result)

# 配置解析相关代码已移至 updater.py
