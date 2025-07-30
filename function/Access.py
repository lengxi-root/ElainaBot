#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import requests
import os
import sys
import datetime
import time
import threading
import logging
from logging.handlers import TimedRotatingFileHandler

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

# 配置日志
def _setup_logger():
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'tokenlog')
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
        
    logger = logging.getLogger('access_token')
    logger.setLevel(logging.INFO)
    
    # 每天轮换日志，保留7天
    handler = TimedRotatingFileHandler(
        os.path.join(log_dir, 'access_token.log'),
        when='midnight',
        backupCount=7
    )
    formatter = logging.Formatter('[%(asctime)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger

# 初始化日志记录器
logger = _setup_logger()

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

def 记录日志(内容):
    """记录日志到文件"""
    logger.info(内容)

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
            
            # 记录完整响应内容
            记录日志(f"获取AccessToken响应: {response}")
            
            # 解析响应
            response_data = json.loads(response)
            if 'access_token' in response_data and 'expires_in' in response_data:
                _token_info['access_token'] = response_data['access_token']
                _token_info['expires_in'] = int(response_data['expires_in'])
                _token_info['last_update'] = time.time()
                记录日志(f"Token更新成功，过期时间: {_token_info['expires_in']}秒")
                return True
            else:
                记录日志(f"Token响应格式错误: {response}")
                
        except Exception as e:
            记录日志(f"获取Token失败 (尝试 {retry_count+1}/{max_retries+1}): {str(e)}")
        
        # 如果到达这里，说明获取失败，需要重试
        retry_count += 1
        if retry_count <= max_retries:
            记录日志(f"3秒后重试获取Token (尝试 {retry_count+1}/{max_retries+1})")
            time.sleep(3)  # 等待3秒后重试
    
    # 如果所有重试都失败
    记录日志(f"获取Token失败: 已重试{max_retries}次，放弃重试")
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
                记录日志("Token不存在或已过期，立即更新")
                获取新Token()
            # 接近过期（60秒内），更新
            elif _token_info['expires_in'] - time_since_last_update <= 60:
                记录日志("Token接近过期(60秒内)，进行更新")
                获取新Token()
            
            # 固定45秒检查一次
            time.sleep(45)
                
        except Exception as e:
            记录日志(f"定时更新Token出错: {str(e)}")
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
    记录日志("Token更新线程已启动")

# 在模块加载时启动更新线程
启动Token更新()

def BOTAPI(Address, method, json_data):
    """调用BOT API"""
    url = "https://api.sgroup.qq.com" + Address
    headers = {"Authorization": f"QQBot {BOT凭证()}", 'Content-Type': 'application/json'}
    return curl(url, method, headers, json_data)

# JSON处理优化：缓存解析结果
_json_cache = {}
def Json(content):
    """将内容转换为JSON字符串"""
    return json.dumps(content, ensure_ascii=False)

def Json取(json_str, path):
    """从JSON字符串中获取数据，优化：缓存解析结果"""
    if isinstance(json_str, str):
        # 使用缓存避免重复解析相同的JSON字符串
        cache_key = hash(json_str)
        if cache_key in _json_cache:
            data = _json_cache[cache_key]
        else:
            try:
                data = json.loads(json_str)
                # 只缓存大于100字符的JSON，避免缓存过多小对象
                if len(json_str) > 20:
                    _json_cache[cache_key] = data
                    # 限制缓存大小，避免内存泄漏
                    if len(_json_cache) > 100:
                        # 简单清理策略：清除全部缓存
                        _json_cache.clear()
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
