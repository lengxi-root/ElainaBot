import requests
from flask import request, jsonify

ROBOT_QQ = None
appid = None
WEBSOCKET_CONFIG = None
get_websocket_status = None

_API_URL = "https://qun.qq.com/qunpro/robot/proxy/domain/qun.qq.com/cgi-bin/group_pro/robot/manager/share_info?bkn=508459323&robot_appid={}"
_QR_API = "https://api.2dcode.biz/v1/create-qr-code?data={}"
_SHARE_URL = "https://qun.qq.com/qunpro/robot/qunshare?robot_uin={}"
_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 15; PJX110 Build/UKQ1.231108.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/135.0.7049.111 Mobile Safari/537.36 V1_AND_SQ_9.1.75_10026_HDBM_T PA QQ/9.1.75.25965 NetType/WIFI WebP/0.4.1 AppId/537287845 Pixel/1080 StatusBarHeight/120 SimpleUISwitch/0 QQTheme/1000 StudyMode/0 CurrentMode/0 CurrentFontScale/0.87 GlobalDensityScale/0.9028571 AllowLandscape/false InMagicWin/0',
    'qname-service': '976321:131072', 'qname-space': 'Production'
}

def set_config(robot_qq, app_id, websocket_config, ws_status_func):
    global ROBOT_QQ, appid, WEBSOCKET_CONFIG, get_websocket_status
    ROBOT_QQ, appid, WEBSOCKET_CONFIG, get_websocket_status = robot_qq, app_id, websocket_config, ws_status_func

def _get_connection_info():
    is_ws = WEBSOCKET_CONFIG.get('enabled', False)
    return ('WebSocket', get_websocket_status()) if is_ws else ('WebHook', 'WebHook')

def handle_get_robot_info():
    share_url = _SHARE_URL.format(ROBOT_QQ)
    conn_type, conn_status = _get_connection_info()
    
    try:
        resp = requests.get(_API_URL.format(appid), headers=_HEADERS, timeout=10)
        resp.raise_for_status()
        api_resp = resp.json()
        
        if api_resp.get('retcode') != 0:
            raise Exception(f"API返回错误: {api_resp.get('msg', 'Unknown error')}")
        
        robot = api_resp.get('data', {}).get('robot_data', {})
        commands = api_resp.get('data', {}).get('commands', [])
        
        avatar = robot.get('robot_avatar', '')
        if avatar and 'myqcloud.com' in avatar:
            avatar += '&imageMogr2/format/png' if '?' in avatar else '?imageMogr2/format/png'
        
        return jsonify({
            'success': True, 'qq': robot.get('robot_uin', ROBOT_QQ), 'name': robot.get('robot_name', '未知机器人'),
            'description': robot.get('robot_desc', '暂无描述'), 'avatar': avatar, 'appid': robot.get('appid', appid),
            'developer': robot.get('create_name', '未知'), 'link': share_url,
            'status': '正常' if robot.get('robot_offline', 1) == 0 else '离线',
            'connection_type': conn_type, 'connection_status': conn_status, 'data_source': 'api',
            'is_banned': robot.get('robot_ban', False), 'mute_status': robot.get('mute_status', 0),
            'commands_count': len(commands), 'is_sharable': robot.get('is_sharable', False),
            'service_note': robot.get('service_note', ''), 'qr_code_api': f'/web/api/robot_qrcode?url={share_url}'
        })
    except Exception as e:
        return jsonify({
            'success': False, 'error': str(e), 'qq': ROBOT_QQ, 'name': '加载失败',
            'description': '无法获取机器人信息', 'avatar': '', 'appid': appid, 'developer': '未知',
            'link': share_url, 'status': '未知', 'connection_type': conn_type, 'connection_status': conn_status,
            'data_source': 'fallback', 'qr_code_api': f'/web/api/robot_qrcode?url={share_url}'
        })

def handle_get_robot_qrcode():
    if not (url := request.args.get('url')):
        return jsonify({'success': False, 'error': '缺少URL参数'}), 400
    try:
        resp = requests.get(_QR_API.format(url), timeout=10)
        resp.raise_for_status()
        return resp.content, 200, {'Content-Type': 'image/png', 'Cache-Control': 'public, max-age=3600'}
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
