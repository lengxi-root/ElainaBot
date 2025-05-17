#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import requests
import os
import sys

# 从配置中导入应用凭证
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import appid, secret

def BOT凭证():
    """获取机器人凭证"""
    # 使用导入的配置
    url = "https://bots.qq.com/app/getAppAccessToken"
    json_data = Json({"appId": appid, "clientSecret": secret})
    headers = {'Content-Type': 'application/json'}
    response = curl(url, "POST", headers, json_data)
    return Json取(response, 'access_token')

def BOTAPI(Address, method, json_data):
    """调用BOT API"""
    url = "https://api.sgroup.qq.com" + Address
    headers = {"Authorization": f"QQBot {BOT凭证()}", 'Content-Type': 'application/json'}
    return curl(url, method, headers, json_data)

def Json(content):
    """将内容转换为JSON字符串"""
    return json.dumps(content, ensure_ascii=False)

def Json取(json_str, path):
    """从JSON字符串中获取数据"""
    if isinstance(json_str, str):
        try:
            data = json.loads(json_str)
        except:
            return "null"
    else:
        data = json_str
    
    keys = path.split('/')
    for key in keys:
        if isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return "null"
    
    return data

def curl(url, method, headers, params):
    """模拟cURL请求"""
    url = url.replace(" ", "%20")
    
    if isinstance(headers, str):
        headers = {'Content-type': 'text/json'}
    elif not isinstance(headers, dict):
        headers = {'Content-type': 'text/json'}
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers, params=params if isinstance(params, dict) else None)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=json.loads(params) if isinstance(params, str) else params)
        elif method == "PUT":
            response = requests.put(url, headers=headers, json=json.loads(params) if isinstance(params, str) else params)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers, json=json.loads(params) if isinstance(params, str) else params)
        else:
            return {'Error': '不支持的请求方法'}
        
        return response.text
    except Exception as e:
        return {'Error': f'请求错误: {str(e)}'} 