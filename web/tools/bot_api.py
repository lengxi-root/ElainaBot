#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json, gzip, re, httpx
from datetime import datetime

_GZIP_MAGIC = b'\x1f\x8b\x08'
_BASE_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Linux; U; Android 14; zh-cn; 22122RK93C Build/UP1A.231005.007) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/109.0.5414.118 Mobile Safari/537.36 XiaoMi/MiuiBrowser/17.8.220115 swan-mibrowser"
}
_QQ_HEADERS = {"Host": "q.qq.com", "Origin": "https://q.qq.com", "Referer": "https://q.qq.com/"}
_BOT_HEADERS = {"Host": "bot.q.qq.com", "Origin": "https://q.qq.com", "Referer": "https://q.qq.com/"}

_STATUS_MAP = {1: "未提审", 2: "审核中", 3: "审核通过"}
_TYPE_MAP = {1: "按钮模板", 2: "markdown模板"}
_MSG_FIELDS = {'report_date': '报告日期', 'up_msg_cnt': '上行消息量', 'up_msg_uv': '上行消息人数',
    'down_msg_cnt': '下行消息量', 'down_passive_msg_cnt': '被动消息数', 'down_initiative_msg_cnt': '主动消息数',
    'inline_msg_cnt': '内联消息数', 'bot_msg_cnt': '总消息量', 'next_day_retention': '对话用户次日留存', 'scene_name': '场景名称'}
_GROUP_FIELDS = {'report_date': '报告日期', 'existing_groups': '现有群组', 'used_groups': '已使用群组', 'added_groups': '新增群组', 'removed_groups': '移除群组'}
_FRIEND_FIELDS = {'report_date': '报告日期', 'stock_added_friends': '现有好友数', 'used_friends': '已使用好友数', 'new_added_friends': '新增好友数', 'new_removed_friends': '移除好友数'}
_DATA_TYPE_MAP = {1: ('msg_data', _MSG_FIELDS), 2: ('group_data', _GROUP_FIELDS), 3: ('friend_data', _FRIEND_FIELDS)}

_HTML_RE = re.compile(r'<[^>]+>')
_URL_RE = re.compile(r'https?://[^\s]+')
_DETAIL_RE = re.compile(r'\[查看详情\]\(')

class QQBotAPI:
    __slots__ = ()
    
    def _build_cookie(self, uin="", quid="", ticket=""):
        parts = []
        if uin:
            parts.append(f"quin={uin}")
        if quid:
            parts.extend([f"quid={quid}", f"developerId={quid}"])
        if ticket:
            parts.append(f"qticket={ticket}")
        return "; ".join(parts)
    
    def _decode_response(self, content):
        try:
            return gzip.decompress(content).decode('utf-8') if content[:3] == _GZIP_MAGIC else content.decode('utf-8')
        except:
            return content.decode('utf-8', errors='ignore')
    
    def _request(self, method, url, uin="", quid="", ticket="", data=None, extra_headers=None):
        try:
            headers = _BASE_HEADERS.copy()
            if uin or quid or ticket:
                headers["Cookie"] = self._build_cookie(uin, quid, ticket)
            if extra_headers:
                headers.update(extra_headers)
            with httpx.Client() as client:
                resp = client.get(url, headers=headers) if method == 'GET' else client.post(url, json=data, headers=headers)
            return json.loads(self._decode_response(resp.content))
        except Exception as e:
            return {"code": 500, "msg": f"请求失败: {str(e)}"}
    
    def _format_ts(self, ts):
        try:
            return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
        except:
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    def get_message_templates(self, uin="", quid="", ticket="", appid="", start=0, limit=30):
        resp = self._request("POST", "https://bot.q.qq.com/cgi-bin/msg_tpl/list", uin, quid, ticket, {"bot_appid": appid, "start": start, "limit": limit})
        if resp.get("code") == 500:
            return resp
        resp['code'] = 0 if resp.get('retcode') == 0 else -1
        if resp.get('retcode') == 0:
            resp.setdefault('data', {}).setdefault('list', [])
            for t in resp['data']['list']:
                new = {'模板id': t.get('tpl_id'), '模板名称': t.get('tpl_name'), '模板状态': _STATUS_MAP.get(t.get('status'), "未通过"),
                       '模板类型': _TYPE_MAP.get(t.get('tpl_type'), "未知类型"), '模板内容': t.get('text'), '创建时间': self._format_ts(t.get('create_time'))}
                t.clear()
                t.update(new)
        return resp
    
    def get_private_messages(self, uin="", quid="", ticket=""):
        resp = self._request("POST", "https://q.qq.com/pb/AppFetchPrivateMsg", uin, quid, ticket, {"page_num": 0, "page_size": 9999, "receiver": quid, "appType": 2})
        if resp.get("code") == 500:
            return {"code": -1, "messages": [], "error": resp.get("msg")}
        if resp.get('code', 0) != 0:
            return {"code": -1, "messages": [], "error": f"API Error: {resp.get('message', '未知错误')}"}
        result = {"code": 0, "messages": []}
        for msg in resp.get('data', {}).get('privateMsgs', []):
            content = _DETAIL_RE.sub('', _URL_RE.sub('', _HTML_RE.sub('', msg.get('content', ''))))
            result["messages"].append({"title": _HTML_RE.sub('', msg.get('title', '')), "content": content, "send_time": self._format_ts(msg.get('send_time'))})
        result["total_count"] = resp.get('data', {}).get('total_count', 0)
        result["unread_count"] = resp.get('data', {}).get('unread_count', 0)
        return result
    
    def get_bot_data(self, uin="", quid="", ticket="", appid="", data_type=1):
        resp = self._request("GET", f"https://bot.q.qq.com/cgi-bin/datareport/read?bot_appid={appid}&data_type={data_type}&data_range=2&scene_id=1", uin, quid, ticket)
        if resp.get("code") == 500:
            return {"retcode": -1, "code": 500, "msg": resp.get("msg"), "error": resp.get("msg"), "data": {}}
        config = _DATA_TYPE_MAP.get(data_type)
        if resp.get('retcode') == 0:
            resp['msg'] = "成功"
            if config:
                key, fields = config
                for item in resp.get('data', {}).get(key, []):
                    new = {cn: item.get(en) for en, cn in fields.items()}
                    item.clear()
                    item.update(new)
        else:
            resp['msg'] = f"失败: {resp.get('msg', '未知错误')}"
        return resp
    
    def get_bot_list(self, uin="", quid="", ticket=""):
        resp = self._request("POST", "https://q.qq.com/homepagepb/GetAppListForLogin", uin, quid, ticket, {"uin": uin, "developer_id": quid, "ticket": ticket, "app_type": [2]})
        return {"retcode": -1, "code": 500, "message": resp.get("msg"), "error": resp.get("msg"), "data": {}} if resp.get("code") == 500 else resp
    
    def get_qr_login_info(self, qrcode=""):
        resp = self._request("POST", "https://q.qq.com/qrcode/get", data={"qrcode": qrcode}, extra_headers={**_QQ_HEADERS, "Cache-Control": "max-age=0", "Accept": "*/*"})
        return {"code": 500, "message": resp.get("msg")} if resp.get("code") == 500 else resp
    
    def create_login_qr(self):
        resp = self._request("POST", "https://q.qq.com/qrcode/create", data={"type": "777"}, extra_headers=_QQ_HEADERS)
        if resp.get("code") == 500:
            return {"status": "error", "message": resp.get("msg")}
        qr = resp.get('data', {}).get('QrCode')
        return {"status": "success", "url": f"https://q.qq.com/login/applist?client=qq&code={qr}&ticket=null", "qr": qr} if qr else {"status": "error", "message": "QrCode not found"}
    
    def get_white_list(self, appid="", uin="", uid="", ticket=""):
        if not all([appid, uin, uid, ticket]):
            return {"code": 400, "msg": "参数不完整"}
        resp = self._request("GET", f"https://bot.q.qq.com/cgi-bin/dev_info/white_ip_config?bot_appid={appid}", uin, uid, ticket)
        if resp.get("code") == 500:
            return {"code": 500, "msg": f"CURL错误：{resp.get('msg')}"}
        if resp.get('retcode') != 0:
            return {"code": -1, "msg": "获取白名单失败", "data": resp}
        try:
            ip_list = resp.get('data', {}).get('ip_white_infos', {}).get('prod', {}).get('ip_list', [])
            return {"code": 0, "msg": "获取成功", "data": ip_list if isinstance(ip_list, list) else []}
        except:
            return {"code": 0, "msg": "获取成功", "data": []}
    
    def create_white_login_qr(self, appid="", uin="", uid="", ticket=""):
        if not all([appid, uin, uid, ticket]):
            return {"code": 400, "msg": "参数不完整", "qrcode": None, "url": None}
        resp = self._request("POST", "https://q.qq.com/qrcode/create", uin, uid, ticket, {"type": 51, "miniAppId": appid})
        if resp.get("code") == 500:
            return {"code": 500, "msg": f"CURL错误：{resp.get('msg')}", "qrcode": None, "url": None}
        qr = resp.get('data', {}).get('QrCode')
        return {"code": 0, "msg": "获取成功", "qrcode": qr, "url": f"https://q.qq.com/qrcode/check?client=qq&code={qr}&ticket={ticket}"} if qr else {"code": -1, "msg": "获取链接失败", "qrcode": None, "url": None}
    
    def update_white_list(self, appid="", uin="", uid="", ticket="", qrcode="", ip="", action=""):
        if not all([appid, uin, uid, ticket, qrcode, ip, action]):
            return {"code": 400, "msg": "参数不完整"}
        current = self.get_white_list(appid, uin, uid, ticket)
        if current.get('code') != 0:
            return {"code": 500, "msg": "获取白名单失败"}
        current_list = current.get('data', [])
        ips = [i.strip() for i in ip.split(',') if i.strip()] if ',' in ip else [ip.strip()]
        if action == 'add':
            final = ips if ',' in ip else (current_list + [ip] if ip not in current_list else None)
            if final is None:
                return {"code": 409, "msg": "IP 已存在于白名单中"}
        elif action == 'del':
            final = current_list.copy()
            for addr in ips:
                if addr not in final:
                    return {"code": 404, "msg": f"IP {addr} 不在白名单中"}
                final.remove(addr)
        else:
            return {"code": 400, "msg": "无效的操作"}
        final = list(set(i for i in final if i.strip()))
        resp = self._request("POST", "https://bot.q.qq.com/cgi-bin/dev_info/update_white_ip_config", uin, uid, ticket,
            {"bot_appid": appid, "ip_white_infos": {"prod": {"ip_list": final, "use": True}}, "qr_code": qrcode})
        if resp.get("code") == 500:
            return {"code": 500, "msg": f"CURL错误：{resp.get('msg')}"}
        if resp.get('retcode') != 0:
            return {"code": -1, "msg": f"{'新增' if action == 'add' else '删除'}失败: {resp.get('msg', '未知错误')}"}
        return {"code": 0, "msg": "新增成功" if action == 'add' else "删除成功"}
    
    def verify_qr_auth(self, appid="", uin="", uid="", ticket="", qrcode=""):
        if not all([uin, uid, ticket, qrcode]):
            return {"code": 400, "msg": "参数不完整"}
        resp = self._request("POST", "https://q.qq.com/qrcode/get", uin, uid, ticket, {"qrcode": qrcode})
        if resp.get("code") == 500:
            return {"code": 500, "msg": f"CURL错误：{resp.get('msg')}"}
        return {"code": 0, "msg": "授权成功"} if resp.get('code') == 0 else {"code": -1, "msg": "未授权"}
    
    def create_template_qr(self, uin="", quid="", ticket=""):
        resp = self._request("POST", "https://q.qq.com/qrcode/create", uin, quid, ticket, {"type": 40, "miniAppId": ""}, {**_QQ_HEADERS, "Referer": "https://q.qq.com/qqbot/"})
        return {"code": 500, "msg": resp.get("msg")} if resp.get("code") == 500 else resp
    
    def preview_template(self, bot_appid="", template_data=None, uin="", uid="", ticket=""):
        if not bot_appid or not template_data:
            return {"retcode": 400, "msg": "参数不完整"}
        resp = self._request("POST", "https://bot.q.qq.com/cgi-bin/msg_tpl/preview", uin, uid, ticket, {"bot_appid": bot_appid, "info": template_data}, _BOT_HEADERS)
        return {"retcode": 500, "msg": resp.get("msg")} if resp.get("code") == 500 else resp
    
    def submit_template(self, bot_appid="", template_data=None, qrcode="", uin="", uid="", ticket=""):
        if not all([bot_appid, template_data, qrcode]):
            return {"retcode": 400, "msg": "参数不完整"}
        resp = self._request("POST", "https://bot.q.qq.com/cgi-bin/msg_tpl/create", uin, uid, ticket, {"bot_appid": bot_appid, "info": template_data, "qrcode": qrcode}, _BOT_HEADERS)
        return {"retcode": 500, "msg": resp.get("msg")} if resp.get("code") == 500 else resp
    
    def audit_templates(self, bot_appid="", tpl_ids=None, qrcode="", uin="", uid="", ticket=""):
        if not all([bot_appid, tpl_ids, qrcode]):
            return {"retcode": 400, "msg": "参数不完整"}
        resp = self._request("POST", "https://bot.q.qq.com/cgi-bin/msg_tpl/audit", uin, uid, ticket,
            {"bot_appid": int(bot_appid) if isinstance(bot_appid, str) else bot_appid, "tpl_id": tpl_ids, "qrcode": qrcode}, _BOT_HEADERS)
        return {"retcode": 500, "msg": resp.get("msg")} if resp.get("code") == 500 else resp
    
    def delete_templates(self, bot_appid="", tpl_ids=None, qrcode="", uin="", uid="", ticket=""):
        if not all([bot_appid, tpl_ids, qrcode]):
            return {"retcode": 400, "msg": "参数不完整"}
        resp = self._request("POST", "https://bot.q.qq.com/cgi-bin/msg_tpl/delete", uin, uid, ticket,
            {"bot_appid": int(bot_appid) if isinstance(bot_appid, str) else bot_appid, "tpl_id": tpl_ids, "qrcode": qrcode}, _BOT_HEADERS)
        return {"retcode": 500, "msg": resp.get("msg")} if resp.get("code") == 500 else resp

_api = None

def get_bot_api():
    global _api
    if _api is None:
        _api = QQBotAPI()
    return _api

get_message_templates = lambda **kw: get_bot_api().get_message_templates(**kw)
get_private_messages = lambda **kw: get_bot_api().get_private_messages(**kw)
get_bot_data = lambda **kw: get_bot_api().get_bot_data(**kw)
get_bot_list = lambda **kw: get_bot_api().get_bot_list(**kw)
create_login_qr = lambda **kw: get_bot_api().create_login_qr(**kw)
get_qr_login_info = lambda **kw: get_bot_api().get_qr_login_info(**kw)
get_white_list = lambda **kw: get_bot_api().get_white_list(**kw)
create_white_login_qr = lambda **kw: get_bot_api().create_white_login_qr(**kw)
update_white_list = lambda **kw: get_bot_api().update_white_list(**kw)
verify_qr_auth = lambda **kw: get_bot_api().verify_qr_auth(**kw)
create_template_qr = lambda **kw: get_bot_api().create_template_qr(**kw)
preview_template = lambda **kw: get_bot_api().preview_template(**kw)
submit_template = lambda **kw: get_bot_api().submit_template(**kw)
audit_templates = lambda **kw: get_bot_api().audit_templates(**kw)
delete_templates = lambda **kw: get_bot_api().delete_templates(**kw)
