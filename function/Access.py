#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json, requests, os, sys, time, threading, logging
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import appid, secret

logger = logging.getLogger('ElainaBot.function.Access')

_token_info = {'access_token': None, 'expires_in': 0, 'last_update': 0}
_session = requests.Session()

def curl(url, method="POST", headers=None, params=None):
    url = url.replace(" ", "%20")
    headers = headers or {'Content-type': 'application/json'}
    params = json.loads(params) if isinstance(params, str) else params
    
    if method.upper() == "GET":
        return _session.get(url, headers=headers, params=params).text
    return _session.request(method.upper(), url, headers=headers, json=params).text

def 获取新Token():
    global _token_info
    for i in range(3):
        try:
            response = json.loads(curl("https://bots.qq.com/app/getAppAccessToken", "POST",
                                      {'Content-Type': 'application/json'}, {"appId": appid, "clientSecret": secret}))
            if 'access_token' in response:
                _token_info.update({
                    'access_token': response['access_token'],
                    'expires_in': int(response.get('expires_in', 7200)),
                    'last_update': time.time()
                })
                return True
            else:
                logger.error(f"获取访问令牌失败，: {response}")
                if i < 2:
                    time.sleep(3)
        except Exception as e:
            logger.error(f"获取访问令牌异常 (尝试 {i + 1}/3): {type(e).__name__}: {e}")
            if i < 2:
                time.sleep(3)
    logger.error("在3次尝试后仍然无法获取访问令牌")
    return False

def 定时更新Token():
    while True:
        try:
            elapsed = time.time() - _token_info['last_update']
            if not _token_info['access_token'] or elapsed >= _token_info['expires_in'] - 60:
                获取新Token()
            time.sleep(45)
        except:
            time.sleep(5)

def BOT凭证():
    if not _token_info['access_token']:
        获取新Token()
    return _token_info['access_token']

threading.Thread(target=定时更新Token, daemon=True).start()

def BOTAPI(Address, method, json_data):
    return curl(f"https://api.sgroup.qq.com{Address}", method, 
                {"Authorization": f"QQBot {BOT凭证()}", 'Content-Type': 'application/json'}, json_data)

def Json(content):
    return json.dumps(content, ensure_ascii=False)

def Json取(json_str, path):
    data = json.loads(json_str) if isinstance(json_str, str) else json_str
    for key in path.split('/'):
        data = data.get(key) if isinstance(data, dict) else None
        if data is None:
            return None
    return data
