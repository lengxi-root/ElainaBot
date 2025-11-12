import threading, requests
from datetime import datetime
from flask import request, jsonify

def handle_get_changelog():
    try:
        commits = requests.get("https://i.elaina.vin/api/elainabot/", timeout=10).json()
        
        result = []
        for commit in commits:
            commit_info = commit.get('commit')
            if not commit_info:
                continue
            
            author = commit_info.get('author', {})
            date_str = author.get('date', '')
            
            if date_str:
                try:
                    dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                    formatted_date = dt.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    formatted_date = '未知时间'
            else:
                formatted_date = '未知时间'
            
            result.append({
                'sha': commit.get('sha', '')[:8],
                'message': commit_info.get('message', '').strip(),
                'author': author.get('name', '未知作者'),
                'date': formatted_date,
                'url': commit.get('html_url', ''),
                'full_sha': commit.get('sha', '')
            })
        
        return jsonify({
            'success': True,
            'data': result,
            'total': len(result)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取更新日志失败: {str(e)}'
        }), 500

def handle_get_current_version():
    try:
        from web.tools.updater import get_updater
        return jsonify({
            'success': True,
            'data': get_updater().get_version_info()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取版本信息失败: {str(e)}'
        }), 500

def handle_check_update():
    try:
        from web.tools.updater import get_updater
        return jsonify({
            'success': True,
            'data': get_updater().check_for_updates()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'检查更新失败: {str(e)}'
        }), 500

def handle_start_update():
    try:
        data = request.get_json() or {}
        version = data.get('version')
        
        from web.tools.updater import get_updater
        updater = get_updater()
        
        def do_update():
            try:
                if version:
                    result = updater.update_to_version(version)
                else:
                    result = updater.update_to_latest()
            except Exception as e:
                updater._report_progress('failed', f'更新出错: {str(e)}', 0)
        
        threading.Thread(target=do_update, daemon=True).start()
        
        return jsonify({
            'success': True,
            'message': '更新已开始'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'启动更新失败: {str(e)}'
        }), 500

def handle_get_update_status():
    try:
        from web.tools.updater import get_updater
        updater = get_updater()
        
        return jsonify({
            'success': True,
            'data': {
                'auto_update_enabled': False,  # 自动更新功能已移除
                'auto_update_on': False,  # 自动更新功能已移除
                'check_interval': 1800,  # 保留配置兼容性
                'is_checking': False  # 无自动检查线程
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取更新状态失败: {str(e)}'
        }), 500

def handle_get_update_progress():
    try:
        from web.tools.updater import get_updater
        return jsonify({
            'success': True,
            'data': get_updater().get_progress()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取更新进度失败: {str(e)}'
        }), 500
