#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json, requests, os, sys, time, threading
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import appid, secret

_token_info = {'access_token': None, 'expires_in': 0, 'last_update': 0}
_session = requests.Session()

def curl(url, method, headers, params):
    url = url.replace(" ", "%20")
    if not isinstance(headers, dict):
        headers = {'Content-type': 'text/json'}
    try:
        json_params = json.loads(params) if isinstance(params, str) else params
        if method == "GET":
            return _session.get(url, headers=headers, params=params if isinstance(params, dict) else None).text
        elif method == "POST":
            return _session.post(url, headers=headers, json=json_params).text
        elif method == "PUT":
            return _session.put(url, headers=headers, json=json_params).text
        elif method == "DELETE":
            return _session.delete(url, headers=headers, json=json_params).text
        return {'Error': '不支持的请求方法'}
    except:
        return {'Error': '请求错误'}

def 获取新Token():
    global _token_info
    for retry_count in range(3):
        try:
            response = curl("https://bots.qq.com/app/getAppAccessToken", "POST", 
                          {'Content-Type': 'application/json'}, 
                          json.dumps({"appId": appid, "clientSecret": secret}, ensure_ascii=False))
            response_data = json.loads(response)
            if 'access_token' in response_data and 'expires_in' in response_data:
                _token_info['access_token'] = response_data['access_token']
                _token_info['expires_in'] = int(response_data['expires_in'])
                _token_info['last_update'] = time.time()
                return True
        except:
            pass
        if retry_count < 2:
            time.sleep(3)
    return False

def 定时更新Token():
    while True:
        try:
            time_since_last_update = time.time() - _token_info['last_update']
            if not _token_info['access_token'] or time_since_last_update >= _token_info['expires_in'] or _token_info['expires_in'] - time_since_last_update <= 60:
                获取新Token()
            time.sleep(45)
        except:
            time.sleep(5)

def BOT凭证():
    global _token_info
    if not _token_info['access_token']:
        获取新Token()
    return _token_info['access_token']

def 启动Token更新():
    threading.Thread(target=定时更新Token, daemon=True).start()

启动Token更新()

def BOTAPI(Address, method, json_data):
    return curl(f"https://api.sgroup.qq.com{Address}", method, 
                {"Authorization": f"QQBot {BOT凭证()}", 'Content-Type': 'application/json'}, json_data)

def Json(content):
    return json.dumps(content, ensure_ascii=False)

def Json取(json_str, path):
    try:
        data = json.loads(json_str) if isinstance(json_str, str) else json_str
        for key in path.split('/'):
            if isinstance(data, dict) and key in data:
                data = data[key]
            else:
                return None
        return data
    except:
        return None 
