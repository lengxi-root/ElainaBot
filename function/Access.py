#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json, requests, os, sys, time, threading
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import appid, secret

try:
    from config import SANDBOX_MODE
except ImportError:
    SANDBOX_MODE = False

_token_info = {'access_token': None, 'expires_in': 0, 'last_update': 0}
_session = requests.Session()
_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
_API_BASE = "https://api.sgroup.qq.com"
_SANDBOX_API_BASE = "https://sandbox.api.sgroup.qq.com"
_DEFAULT_HEADERS = {'Content-Type': 'application/json'}
_TOKEN_PAYLOAD = {"appId": appid, "clientSecret": secret}
_TOKEN_REFRESH_BUFFER = 60
_TOKEN_RETRY_DELAY = 3
_TOKEN_CHECK_INTERVAL = 45

def curl(url, method="POST", headers=None, params=None):
    url = url.replace(" ", "%20")
    headers = headers or _DEFAULT_HEADERS
    params = json.loads(params) if isinstance(params, str) else params
    
    if method == "GET":
        return _session.get(url, headers=headers, params=params).text
    return _session.request(method, url, headers=headers, json=params).text

def 获取新Token():
    global _token_info
    for i in range(3):
        try:
            response = json.loads(curl(_TOKEN_URL, "POST", _DEFAULT_HEADERS, _TOKEN_PAYLOAD))
            if 'access_token' in response:
                _token_info['access_token'] = response['access_token']
                _token_info['expires_in'] = int(response.get('expires_in', 7200))
                _token_info['last_update'] = time.time()
                return True
        except:
            if i < 2:
                time.sleep(_TOKEN_RETRY_DELAY)
    return False

def 定时更新Token():
    while True:
        try:
            elapsed = time.time() - _token_info['last_update']
            if not _token_info['access_token'] or elapsed >= _token_info['expires_in'] - _TOKEN_REFRESH_BUFFER:
                获取新Token()
            time.sleep(_TOKEN_CHECK_INTERVAL)
        except:
            time.sleep(5)

def BOT凭证():
    if not _token_info['access_token']:
        获取新Token()
    return _token_info['access_token']

threading.Thread(target=定时更新Token, daemon=True).start()

def is_sandbox_group(group_id):
    """检查是否为沙盒群（无论沙盒模式是否开启，只要在列表中就返回True）"""
    # 从 data/sandbox.json 读取沙盒群列表
    try:
        import os
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sandbox_file = os.path.join(base_dir, 'data', 'sandbox.json')
        
        if not os.path.exists(sandbox_file):
            return False
        
        with open(sandbox_file, 'r', encoding='utf-8') as f:
            sandbox_data = json.load(f)
            sandbox_groups = sandbox_data.get('sandbox_groups', [])
            return str(group_id) in [str(g) for g in sandbox_groups]
    except:
        return False

def get_api_base(group_id=None):
    """根据群ID获取API基础地址"""
    if group_id and is_sandbox_group(group_id):
        return _SANDBOX_API_BASE
    return _API_BASE

def BOTAPI(Address, method, json_data, group_id=None):
    """发送API请求，支持沙盒模式"""
    api_base = get_api_base(group_id)
    return curl(f"{api_base}{Address}", method, 
                {"Authorization": f"QQBot {BOT凭证()}", 'Content-Type': 'application/json'}, json_data)

def Json(content):
    return json.dumps(content, ensure_ascii=False)

def Json取(json_str, path):
    data = json.loads(json_str) if isinstance(json_str, str) else json_str
    for key in path.split('/'):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
        if data is None:
            return None
    return data
