import requests
from flask import request, jsonify

ROBOT_QQ = None
appid = None
WEBSOCKET_CONFIG = None
get_websocket_status = None

def set_config(robot_qq, app_id, websocket_config, ws_status_func):
    global ROBOT_QQ, appid, WEBSOCKET_CONFIG, get_websocket_status
    ROBOT_QQ = robot_qq
    appid = app_id
    WEBSOCKET_CONFIG = websocket_config
    get_websocket_status = ws_status_func

def handle_get_robot_info():
    try:
        robot_share_url = f"https://qun.qq.com/qunpro/robot/qunshare?robot_uin={ROBOT_QQ}"
        is_websocket = WEBSOCKET_CONFIG.get('enabled', False)
        
        if is_websocket:
            connection_type = 'WebSocket'
            connection_status = get_websocket_status()
        else:
            connection_type = 'WebHook'
            connection_status = 'WebHook'
        
        response = requests.get(
            f"https://qun.qq.com/qunpro/robot/proxy/domain/qun.qq.com/cgi-bin/group_pro/robot/manager/share_info?bkn=508459323&robot_appid={appid}",
            headers={
                'User-Agent': 'Mozilla/5.0 (Linux; Android 15; PJX110 Build/UKQ1.231108.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/135.0.7049.111 Mobile Safari/537.36 V1_AND_SQ_9.1.75_10026_HDBM_T PA QQ/9.1.75.25965 NetType/WIFI WebP/0.4.1 AppId/537287845 Pixel/1080 StatusBarHeight/120 SimpleUISwitch/0 QQTheme/1000 StudyMode/0 CurrentMode/0 CurrentFontScale/0.87 GlobalDensityScale/0.9028571 AllowLandscape/false InMagicWin/0',
                'qname-service': '976321:131072',
                'qname-space': 'Production'
            },
            timeout=10
        )
        response.raise_for_status()
        api_response = response.json()
        
        if api_response.get('retcode') != 0:
            error_msg = api_response.get('msg', 'Unknown error')
            raise Exception(f"API返回错误: {error_msg}")
        
        robot_data = api_response.get('data', {}).get('robot_data', {})
        commands = api_response.get('data', {}).get('commands', [])
        
        avatar_url = robot_data.get('robot_avatar', '')
        if avatar_url and 'myqcloud.com' in avatar_url:
            if '?' in avatar_url:
                avatar_url += '&imageMogr2/format/png'
            else:
                avatar_url += '?imageMogr2/format/png'
        
        return jsonify({
            'success': True,
            'qq': robot_data.get('robot_uin', ROBOT_QQ),
            'name': robot_data.get('robot_name', '未知机器人'),
            'description': robot_data.get('robot_desc', '暂无描述'),
            'avatar': avatar_url,
            'appid': robot_data.get('appid', appid),
            'developer': robot_data.get('create_name', '未知'),
            'link': robot_share_url,
            'status': '正常' if robot_data.get('robot_offline', 1) == 0 else '离线',
            'connection_type': connection_type,
            'connection_status': connection_status,
            'data_source': 'api',
            'is_banned': robot_data.get('robot_ban', False),
            'mute_status': robot_data.get('mute_status', 0),
            'commands_count': len(commands),
            'is_sharable': robot_data.get('is_sharable', False),
            'service_note': robot_data.get('service_note', ''),
            'qr_code_api': f'/web/api/robot_qrcode?url={robot_share_url}'
        })
        
    except Exception as e:
        robot_share_url = f"https://qun.qq.com/qunpro/robot/qunshare?robot_uin={ROBOT_QQ}"
        is_websocket = WEBSOCKET_CONFIG.get('enabled', False)
        
        if is_websocket:
            connection_type = 'WebSocket'
            connection_status = get_websocket_status()
        else:
            connection_type = 'WebHook'
            connection_status = 'WebHook'
        
        return jsonify({
            'success': False,
            'error': str(e),
            'qq': ROBOT_QQ,
            'name': '加载失败',
            'description': '无法获取机器人信息',
            'avatar': '',
            'appid': appid,
            'developer': '未知',
            'link': robot_share_url,
            'status': '未知',
            'connection_type': connection_type,
            'connection_status': connection_status,
            'data_source': 'fallback',
            'qr_code_api': f'/web/api/robot_qrcode?url={robot_share_url}'
        })

def handle_get_robot_qrcode():
    url = request.args.get('url')
    
    if not url:
        return jsonify({
            'success': False,
            'error': '缺少URL参数'
        }), 400
    
    try:
        response = requests.get(
            f"https://api.2dcode.biz/v1/create-qr-code?data={url}",
            timeout=10
        )
        response.raise_for_status()
        
        return response.content, 200, {
            'Content-Type': 'image/png',
            'Cache-Control': 'public, max-age=3600'
        }
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

