#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import requests
import os
import sys
import datetime
import time
import threading

# 从配置中导入应用凭证
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import appid, secret

# 全局变量
_token_info = {
    'access_token': None,
    'expires_in': 0,
    'last_update': 0
}

# 使用 Session 复用连接
_session = requests.Session()

def curl(url, method, headers, params):
    """模拟cURL请求"""
    url = url.replace(" ", "%20")
    
    if isinstance(headers, str):
        headers = {'Content-type': 'text/json'}
    elif not isinstance(headers, dict):
        headers = {'Content-type': 'text/json'}
    
    try:
        if method == "GET":
            response = _session.get(url, headers=headers, params=params if isinstance(params, dict) else None)
        elif method == "POST":
            response = _session.post(url, headers=headers, json=json.loads(params) if isinstance(params, str) else params)
        elif method == "PUT":
            response = _session.put(url, headers=headers, json=json.loads(params) if isinstance(params, str) else params)
        elif method == "DELETE":
            response = _session.delete(url, headers=headers, json=json.loads(params) if isinstance(params, str) else params)
        else:
            return {'Error': '不支持的请求方法'}
        
        return response.text
    except Exception as e:
        return {'Error': f'请求错误: {str(e)}'}

def 获取新Token():
    """获取新的AccessToken"""
    global _token_info
    
    # 添加重试机制
    max_retries = 2  # 最多重试2次
    retry_count = 0
    
    while retry_count <= max_retries:  # 允许初始尝试 + 2次重试
        try:
            url = "https://bots.qq.com/app/getAppAccessToken"
            json_data = json.dumps({"appId": appid, "clientSecret": secret}, ensure_ascii=False)
            headers = {'Content-Type': 'application/json'}
            response = curl(url, "POST", headers, json_data)
            
            # 解析响应
            response_data = json.loads(response)
            if 'access_token' in response_data and 'expires_in' in response_data:
                _token_info['access_token'] = response_data['access_token']
                _token_info['expires_in'] = int(response_data['expires_in'])
                _token_info['last_update'] = time.time()
                return True
                
        except Exception as e:
            pass
        
        # 如果到达这里，说明获取失败，需要重试
        retry_count += 1
        if retry_count <= max_retries:
            time.sleep(3)  # 等待3秒后重试
    
    # 如果所有重试都失败
    return False

def 定时更新Token():
    """定时更新Token的线程函数"""
    while True:
        try:
            current_time = time.time()
            time_since_last_update = current_time - _token_info['last_update']
            remaining_time = _token_info['expires_in'] - time_since_last_update if _token_info['last_update'] > 0 else 0
            
            # 没有token或已过期，立即更新
            if not _token_info['access_token'] or time_since_last_update >= _token_info['expires_in']:
                获取新Token()
            # 接近过期（60秒内），更新
            elif _token_info['expires_in'] - time_since_last_update <= 60:
                获取新Token()
            
            # 固定45秒检查一次
            time.sleep(45)
                
        except Exception as e:
            time.sleep(5)  # 出错后等待5秒再试

def BOT凭证():
    """获取机器人凭证"""
    global _token_info
    
    # 如果Token不存在，立即获取
    if not _token_info['access_token']:
        获取新Token()
    
    return _token_info['access_token']

# 启动定时更新线程
def 启动Token更新():
    """启动Token更新线程"""
    update_thread = threading.Thread(target=定时更新Token, daemon=True)
    update_thread.start()

# 在模块加载时启动更新线程
启动Token更新()

def BOTAPI(Address, method, json_data):
    """调用BOT API"""
    url = "https://api.sgroup.qq.com" + Address
    headers = {"Authorization": f"QQBot {BOT凭证()}", 'Content-Type': 'application/json'}
    return curl(url, method, headers, json_data)

# JSON处理优化：删除缓存相关代码
def Json(content):
    """将内容转换为JSON字符串"""
    return json.dumps(content, ensure_ascii=False)

def Json取(json_str, path):
    """从JSON字符串中获取数据"""
    if isinstance(json_str, str):
            try:
                data = json.loads(json_str)
            except:
                return None
    else:
        data = json_str
    
    keys = path.split('/')
    for key in keys:
        if isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return None
    
    return data 
