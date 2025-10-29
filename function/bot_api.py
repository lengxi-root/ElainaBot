#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json, gzip, logging
from datetime import datetime
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger('ElainaBot.function.bot_api')

class QQBotAPI:
    def __init__(self):
        self.base_headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; U; Android 14; zh-cn; 22122RK93C Build/UP1A.231005.007) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/109.0.5414.118 Mobile Safari/537.36 XiaoMi/MiuiBrowser/17.8.220115 swan-mibrowser"
        }
    
    def _build_cookie_string(self, uin: str = "", quid: str = "", ticket: str = "") -> str:
        cookies = []
        if uin:
            cookies.append(f"quin={uin}")
        if quid:
            cookies.extend([f"quid={quid}", f"developerId={quid}"])
        if ticket:
            cookies.append(f"qticket={ticket}")
        return "; ".join(cookies)
    
    def _process_response(self, response_content: bytes) -> str:
        try:
            return gzip.decompress(response_content).decode('utf-8') if response_content[:3] == b'\x1f\x8b\x08' else response_content.decode('utf-8')
        except:
            return response_content.decode('utf-8', errors='ignore')
    
    def _make_request(self, method: str, url: str, uin: str = "", quid: str = "", 
                     ticket: str = "", data: Optional[Dict] = None, 
                     extra_headers: Optional[Dict] = None) -> Dict[str, Any]:
        try:
            headers = self.base_headers.copy()
            if any([uin, quid, ticket]):
                headers["Cookie"] = self._build_cookie_string(uin, quid, ticket)
            if extra_headers:
                headers.update(extra_headers)
            with httpx.Client() as client:
                response = client.get(url, headers=headers) if method.upper() == 'GET' else client.post(url, json=data, headers=headers)
            return json.loads(self._process_response(response.content))
        except:
            return {"code": 500, "msg": "请求失败"}
    
    def get_message_templates(self, uin: str = "", quid: str = "", ticket: str = "", 
                             appid: str = "", start: int = 0, limit: int = 30) -> Dict[str, Any]:
        response_data = self._make_request("POST", "https://bot.q.qq.com/cgi-bin/msg_tpl/list", uin, quid, ticket, 
                                          {"bot_appid": appid, "start": start, "limit": limit})
        if response_data.get("code") == 500:
            return response_data
        response_data['code'] = 0 if response_data.get('retcode') == 0 else -1
        if response_data.get('retcode') == 0:
            if 'data' not in response_data:
                response_data['data'] = {}
            if 'list' not in response_data['data']:
                response_data['data']['list'] = []
            template_list = response_data['data']['list']
            if template_list:
                status_map = {1: "未提审", 2: "审核中", 3: "审核通过"}
                type_map = {1: "按钮模板", 2: "markdown模板"}
                for template in template_list:
                    new_template = {
                        '模板id': template.get('tpl_id'), '模板名称': template.get('tpl_name'),
                        '模板状态': status_map.get(template.get('status'), "未通过"),
                        '模板类型': type_map.get(template.get('tpl_type'), "未知类型"),
                        '模板内容': template.get('text'), '创建时间': self._format_timestamp(template.get('create_time'))
                    }
                    template.clear()
                    template.update(new_template)
        return response_data
    
    def _format_timestamp(self, timestamp) -> str:
        try:
            return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M:%S") if timestamp else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        except:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def get_private_messages(self, uin: str = "", quid: str = "", ticket: str = "") -> Dict[str, Any]:
        response = self._make_request("POST", "https://q.qq.com/pb/AppFetchPrivateMsg", uin, quid, ticket, 
                                     {"page_num": 0, "page_size": 9999, "receiver": quid, "appType": 2})
        if response.get("code") == 500:
            return {"code": -1, "messages": [], "error": response.get("msg")}
        if response.get('code', 0) != 0:
            return {"code": -1, "messages": [], "error": f"API Error: {response.get('message', '未知错误')}"}
        formatted_response = {"code": 0, "messages": []}
        if response.get('data', {}).get('privateMsgs'):
            import re
            for msg in response['data']['privateMsgs']:
                content = re.sub(r'<[^>]+>', '', msg.get('content', ''))
                content = re.sub(r'https?://[^\s]+', '', content)
                content = re.sub(r'\[查看详情\]\(', '', content)
                formatted_response["messages"].append({
                    "title": re.sub(r'<[^>]+>', '', msg.get('title', '')),
                    "content": content,
                    "send_time": self._format_timestamp(msg.get('send_time'))
                })
        formatted_response["total_count"] = response.get('data', {}).get('total_count', 0)
        formatted_response["unread_count"] = response.get('data', {}).get('unread_count', 0)
        return formatted_response
    
    def get_bot_data(self, uin: str = "", quid: str = "", ticket: str = "", 
                    appid: str = "", data_type: int = 1) -> Dict[str, Any]:
        data = self._make_request("GET", f"https://bot.q.qq.com/cgi-bin/datareport/read?bot_appid={appid}&data_type={data_type}&data_range=2&scene_id=1", uin, quid, ticket)
        if data.get("code") == 500:
            return {"retcode": -1, "code": 500, "msg": data.get("msg"), "error": data.get("msg"), "data": {}}
        translation_maps = {
            1: ('msg_data', {'report_date': '报告日期', 'up_msg_cnt': '上行消息量', 'up_msg_uv': '上行消息人数',
                'down_msg_cnt': '下行消息量', 'down_passive_msg_cnt': '被动消息数', 'down_initiative_msg_cnt': '主动消息数', 
                'inline_msg_cnt': '内联消息数', 'bot_msg_cnt': '总消息量', 'next_day_retention': '对话用户次日留存', 'scene_name': '场景名称'}),
            2: ('group_data', {'report_date': '报告日期', 'existing_groups': '现有群组', 'used_groups': '已使用群组', 'added_groups': '新增群组', 'removed_groups': '移除群组'}),
            3: ('friend_data', {'report_date': '报告日期', 'stock_added_friends': '现有好友数', 'used_friends': '已使用好友数', 'new_added_friends': '新增好友数', 'new_removed_friends': '移除好友数'})
        }
        self._translate_data(data, translation_maps.get(data_type, (None, {})))
        return data
    
    def _translate_data(self, data: Dict[str, Any], translation_config):
        data_key, field_map = translation_config
        if data.get('retcode') == 0:
            data['msg'] = "成功"
            if data_key and data.get('data', {}).get(data_key):
                for item in data['data'][data_key]:
                    translated = {chinese_key: item.get(english_key) for english_key, chinese_key in field_map.items()}
                    item.clear()
                    item.update(translated)
        else:
            data['msg'] = f"失败: {data.get('msg', '未知错误')}"
    
    def get_bot_list(self, uin: str = "", quid: str = "", ticket: str = "") -> Dict[str, Any]:
        response = self._make_request("POST", "https://q.qq.com/homepagepb/GetAppListForLogin", uin, quid, ticket, 
                                     {"uin": uin, "developer_id": quid, "ticket": ticket, "app_type": [2]})
        return {"retcode": -1, "code": 500, "message": response.get("msg"), "error": response.get("msg"), "data": {}} if response.get("code") == 500 else response
    
    def get_qr_login_info(self, qrcode: str = "") -> Dict[str, Any]:
        response = self._make_request("POST", "https://q.qq.com/qrcode/get", data={"qrcode": qrcode}, 
                                     extra_headers={"Host": "q.qq.com", "Cache-Control": "max-age=0", "Accept": "*/*", "Origin": "https://q.qq.com", "Referer": "https://q.qq.com/"})
        return {"code": 500, "message": response.get("msg")} if response.get("code") == 500 else response
    
    def create_login_qr(self) -> Dict[str, Any]:
        response = self._make_request("POST", "https://q.qq.com/qrcode/create", data={"type": "777"}, 
                                     extra_headers={"Host": "q.qq.com", "Origin": "https://q.qq.com", "Referer": "https://q.qq.com/"})
        if response.get("code") == 500:
            return {"status": "error", "message": response.get("msg")}
        if response.get('data', {}).get('QrCode'):
            qrcode = response['data']['QrCode']
            return {"status": "success", "url": f"https://q.qq.com/login/applist?client=qq&code={qrcode}&ticket=null", "qr": qrcode}
        return {"status": "error", "message": "QrCode parameter not found in response."}
    
    def get_white_list(self, appid: str = "", uin: str = "", uid: str = "", ticket: str = "") -> Dict[str, Any]:
        if not all([appid, uin, uid, ticket]):
            return {"code": 400, "msg": "参数不完整"}
        response = self._make_request("GET", f"https://bot.q.qq.com/cgi-bin/dev_info/white_ip_config?bot_appid={appid}", uin, uid, ticket)
        if response.get("code") == 500:
            return {"code": 500, "msg": f"CURL错误：{response.get('msg')}"}
        if response.get('retcode') != 0:
            return {"code": -1, "msg": "获取白名单失败", "data": response}
        try:
            ip_list = response.get('data', {}).get('ip_white_infos', {}).get('prod', {}).get('ip_list', [])
            return {"code": 0, "msg": "获取成功", "data": ip_list if isinstance(ip_list, list) else []}
        except:
            return {"code": 0, "msg": "获取成功", "data": []}
    
    def create_white_login_qr(self, appid: str = "", uin: str = "", uid: str = "", ticket: str = "") -> Dict[str, Any]:
        if not all([appid, uin, uid, ticket]):
            return {"code": 400, "msg": "参数不完整", "qrcode": None, "url": None}
        response = self._make_request("POST", "https://q.qq.com/qrcode/create", uin, uid, ticket, {"type": 51, "miniAppId": appid})
        if response.get("code") == 500:
            return {"code": 500, "msg": f"CURL错误：{response.get('msg')}", "qrcode": None, "url": None}
        if response.get('data', {}).get('QrCode'):
            qr_code = response['data']['QrCode']
            return {"code": 0, "msg": "获取成功", "qrcode": qr_code, "url": f"https://q.qq.com/qrcode/check?client=qq&code={qr_code}&ticket={ticket}"}
        return {"code": -1, "msg": "获取链接失败", "qrcode": None, "url": None}
    
    def update_white_list(self, appid: str = "", uin: str = "", uid: str = "", ticket: str = "",
                         qrcode: str = "", ip: str = "", action: str = "") -> Dict[str, Any]:
        if not all([appid, uin, uid, ticket, qrcode, ip, action]):
            return {"code": 400, "msg": "参数不完整"}
        current_list = self.get_white_list(appid, uin, uid, ticket)
        if current_list.get('code') != 0:
            return {"code": 500, "msg": "获取白名单失败"}
        current_ip_list = current_list.get('data', [])
        ip_addresses = [i.strip() for i in ip.split(',') if i.strip()] if ',' in ip else [ip.strip()]
        if action == 'add':
            if ',' in ip:
                final_ip_list = ip_addresses
            else:
                if ip in current_ip_list:
                    return {"code": 409, "msg": "IP 已存在于白名单中"}
                final_ip_list = current_ip_list + [ip]
        elif action == 'del':
            final_ip_list = current_ip_list.copy()
            for ip_addr in ip_addresses:
                if ip_addr not in final_ip_list:
                    return {"code": 404, "msg": f"IP {ip_addr} 不在白名单中"}
                final_ip_list.remove(ip_addr)
        else:
            return {"code": 400, "msg": "无效的操作"}
        final_ip_list = list(set(i for i in final_ip_list if i.strip()))
        result = self._make_request("POST", "https://bot.q.qq.com/cgi-bin/dev_info/update_white_ip_config", uin, uid, ticket, 
                                   {"bot_appid": appid, "ip_white_infos": {"prod": {"ip_list": final_ip_list, "use": True}}, "qr_code": qrcode})
        if result.get("code") == 500:
            return {"code": 500, "msg": f"CURL错误：{result.get('msg')}"}
        if result.get('retcode') != 0:
            return {"code": -1, "msg": f"{'新增' if action == 'add' else '删除'}失败: {result.get('msg', '未知错误')}"}
        return {"code": 0, "msg": "新增成功" if action == 'add' else "删除成功"}
    
    def verify_qr_auth(self, appid: str = "", uin: str = "", uid: str = "", ticket: str = "", qrcode: str = "") -> Dict[str, Any]:
        if not all([appid, uin, uid, ticket, qrcode]):
            return {"code": 400, "msg": "参数不完整"}
        response = self._make_request("POST", "https://q.qq.com/qrcode/get", uin, uid, ticket, {"qrcode": qrcode})
        if response.get("code") == 500:
            return {"code": 500, "msg": f"CURL错误：{response.get('msg')}"}
        return {"code": 0, "msg": "授权成功"} if response.get('code') == 0 else {"code": -1, "msg": "未授权"}

_bot_api_instance = None

def get_bot_api() -> QQBotAPI:
    global _bot_api_instance
    if _bot_api_instance is None:
        _bot_api_instance = QQBotAPI()
    return _bot_api_instance

def get_message_templates(**kwargs) -> Dict[str, Any]:
    return get_bot_api().get_message_templates(**kwargs)

def get_private_messages(**kwargs) -> Dict[str, Any]:
    return get_bot_api().get_private_messages(**kwargs)

def get_bot_data(**kwargs) -> Dict[str, Any]:
    return get_bot_api().get_bot_data(**kwargs)

def get_bot_list(**kwargs) -> Dict[str, Any]:
    return get_bot_api().get_bot_list(**kwargs)

def create_login_qr(**kwargs) -> Dict[str, Any]:
    return get_bot_api().create_login_qr(**kwargs)

def get_qr_login_info(**kwargs) -> Dict[str, Any]:
    return get_bot_api().get_qr_login_info(**kwargs)

def get_white_list(**kwargs) -> Dict[str, Any]:
    return get_bot_api().get_white_list(**kwargs)

def create_white_login_qr(**kwargs) -> Dict[str, Any]:
    return get_bot_api().create_white_login_qr(**kwargs)

def update_white_list(**kwargs) -> Dict[str, Any]:
    return get_bot_api().update_white_list(**kwargs)

def verify_qr_auth(**kwargs) -> Dict[str, Any]:
    return get_bot_api().verify_qr_auth(**kwargs)