"""插件市场 Python 处理器 - 调用 PHP 后端"""

import httpx
from flask import request, jsonify

# PHP 后端地址（需要部署到 PHP 服务器）
PHP_API_URL = 'https://i.elaina.vin/api/elainabot/plugin_market.php'
TIMEOUT = 30


def call_php(action, data=None, params=None, token=None):
    """调用 PHP 后端"""
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
    """提交插件"""
    data = request.json or {}
    result = call_php('submit', data)
    return jsonify(result)


def handle_market_list():
    """获取插件列表"""
    params = {
        'category': request.args.get('category', ''),
        'status': request.args.get('status', ''),
        'search': request.args.get('search', '')
    }
    params = {k: v for k, v in params.items() if v}
    result = call_php('list', params=params)
    return jsonify(result)


def handle_market_pending():
    """获取待审核列表"""
    token = request.headers.get('X-Admin-Token') or request.args.get('token')
    result = call_php('pending', token=token)
    return jsonify(result)


def handle_market_review():
    """审核插件"""
    token = request.headers.get('X-Admin-Token') or (request.json or {}).get('token')
    data = request.json or {}
    result = call_php('review', data, token=token)
    return jsonify(result)


def handle_market_update_status():
    """更新插件状态"""
    token = request.headers.get('X-Admin-Token') or (request.json or {}).get('token')
    data = request.json or {}
    result = call_php('update_status', data, token=token)
    return jsonify(result)


def handle_market_delete():
    """删除插件"""
    token = request.headers.get('X-Admin-Token') or (request.json or {}).get('token')
    data = request.json or {}
    result = call_php('delete', data, token=token)
    return jsonify(result)


def handle_market_categories():
    """获取分类列表"""
    result = call_php('categories')
    return jsonify(result)


def handle_market_export():
    """导出插件列表"""
    result = call_php('export')
    return jsonify(result)


def handle_market_download():
    """记录下载"""
    data = request.json or {}
    result = call_php('download', data)
    return jsonify(result)
