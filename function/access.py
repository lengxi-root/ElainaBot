import json
import requests
from config import Config

def get_bot_token():
    """获取机器人访问令牌"""
    config = Config()
    url = "https://bots.qq.com/app/getAppAccessToken"
    data = {
        "appId": config.get_appid,
        "clientSecret": config.get_secret
    }
    headers = {'Content-Type': 'application/json'}
    response = requests.post(url, json=data, headers=headers)
    return response.json().get("access_token")

def BOTAPI(address, method="GET", data=None):
    """调用机器人API"""
    url = f"https://api.sgroup.qq.com{address}"
    headers = {
        "Authorization": f"QQBot {get_bot_token()}",
        'Content-Type': 'application/json'
    }
    
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            pass
            
    response = requests.request(
        method=method,
        url=url,
        headers=headers,
        json=data if method in ["POST", "PUT", "DELETE"] else None,
        params=data if method == "GET" else None
    )
    
    if response.status_code == 404 or not response.text:
        return {"Error": "请求错误"}
    
    try:
        return response.json()
    except:
        return response.text

def get_json_value(json_data, path):
    """从JSON中获取指定路径的值"""
    if isinstance(json_data, str):
        try:
            data = json.loads(json_data)
        except json.JSONDecodeError:
            return "null"
    else:
        data = json_data
        
    keys = path.split('/')
    for key in keys:
        if isinstance(data, dict) and key in data:
            data = data[key]
        else:
            return "null"
    return data 