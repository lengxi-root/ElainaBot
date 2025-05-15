from flask import Flask, request, jsonify
import json
import os
import time
import psutil
import platform
from threading import Thread
import base64
from config import Config
from function.access import BOTAPI
from core.plugin.plugin_manager import PluginManager
from core.event.message_event import MessageEvent

app = Flask(__name__)

# 全局配置
config = Config()

def format_bytes(bytes, precision=2):
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    bytes = max(bytes, 0)
    pow_val = min(4, int(0 if bytes == 0 else (len(str(bytes)) - 1) // 3))
    bytes = float(bytes) / (1024 ** pow_val)
    return f"{round(bytes, precision)} {units[pow_val]}"

@app.route('/', methods=['POST', 'GET'])
def handle_request():
    if request.method == 'GET':
        # 收集服务状态信息
        info = "服务状态信息\n"
        me = BOTAPI("/users/@me", "GET")
        name = me.get('username', 'Unknown')
        info += "==================\n"
        info += f"Bot: {name}\n"
        info += f"Python 版本: {platform.python_version()}\n"
        info += "服务状态: 运行中\n"
        info += f"当前内存使用: {format_bytes(psutil.Process().memory_info().rss)}\n"
        info += f"内存使用峰值: {format_bytes(psutil.Process().memory_info().peak_wset)}\n"
        info += f"服务器时间: {time.strftime('%Y-%m-d %H:%M:%S')}\n"
        info += f"Python 运行模式: {platform.python_implementation()}\n"
        info += f"操作系统: {platform.system()} {platform.release()}\n"
        return info, 200, {'Content-Type': 'text/plain; charset=utf-8'}

    data = request.get_data()
    if not data:
        return jsonify({"code": 1, "msg": "无数据"})

    # 立即返回200响应
    response = jsonify({"code": 0, "msg": "接收成功"})
    
    try:
        json_data = json.loads(data)
        op = json_data.get("op")
        t = json_data.get("t")

        # 签名校验
        if op == 13:
            from function.sign import Signs
            sign = Signs()
            return sign.sign(data)

        # 消息事件
        if op == 0:
            def process_message():
                plugin_manager = PluginManager()
                plugin_manager.load_plugins()
                message_event = MessageEvent(json_data)
                
                # 消息去重
                with open('message.log', 'a+') as f:
                    f.seek(0)
                    if str(message_event) in f.read():
                        print("重复消息，已忽略")
                        return
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} 收到消息: {json.dumps(json_data)}\n")
                
                if not plugin_manager.dispatch_message(message_event):
                    # 移除默认回复
                    pass

            Thread(target=process_message).start()
            return response

    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"code": 1, "msg": str(e)})

    return response

def upload_group_image(group, content):
    return json.loads(BOTAPI(f"/v2/groups/{group}/files", "POST", 
                           json.dumps({
                               'srv_send_msg': False,
                               'file_type': 1,
                               'file_data': base64.b64encode(content).decode()
                           })))

def send_group(group, content):
    return BOTAPI(f"/v2/groups/{group}/messages", "POST", json.dumps(content))

def send_group2(group, content, type=0, msg_id=None):
    ark = {
        'template_id': 23,
        'kv': [
            {'key': '#DESC#', 'value': 'TSmoe'},
            {'key': '#PROMPT#', 'value': 'MBot'},
            {
                'key': '#LIST#',
                'obj': [
                    {
                        'obj_kv': [
                            {'key': 'desc', 'value': content}
                        ]
                    }
                ]
            }
        ]
    }
    
    return BOTAPI(f"/v2/groups/{group}/messages", "POST", 
                 json.dumps({
                     "msg_id": msg_id,
                     "msg_type": 3,
                     "ark": ark,
                     "msg_seq": int(time.time() * 1000) % 1000000
                 }))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001) 