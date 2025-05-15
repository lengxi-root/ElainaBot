import json
import random
from typing import Optional, Union, List, Dict, Any
from function.access import BOTAPI, get_json_value
from core.segment.segment import Segment

class MessageEvent:
    def __init__(self, data: Dict[str, Any]):
        """
        初始化消息事件
        :param data: 原始
        消息数据
        """
        self.raw_data = data
        self.event_type = self.get('t')  # 事件类型，如GROUP_AT_MESSAGE_CREATE, INTERACTION_CREATE
        self.message_id = self.get('d/id')
        self.content = self.sanitize_content(self.get('d/content'))
        self.sender_id = self.get('d/author/id')
        self.timestamp = self.get('d/timestamp')
        self.matches = None  # 用于存储正则匹配结果

        # 动态提取群/频道信息
        self.group_id = self.get('d/group_id')
        self.user_id = self.get('d/author/id') or self.sender_id
        self.channel_id = self.get('d/channel_id')
        self.guild_id = self.get('d/guild_id')

    def reply(self, content: Union[str, Dict, List], raw: Optional[Union[str, Dict]] = None) -> Dict:
        """
        回复消息
        :param content: 消息内容
        :param raw: 原始消息数据（可选）
        :return: API响应
        """
        if isinstance(content, (dict, list)):
            # 如果是消息段，直接使用
            payload = {
                "msg_id": self.message_id,
                "content": json.dumps(content) if isinstance(content, list) else content,
                "msg_seq": random.randint(10000, 999999)
            }
        else:
            # 文本消息使用ark模板
            ark = {
                'template_id': 23,
                'kv': [
                    {'key': '#DESC#', 'value': 'TSmoe'},
                    {'key': '#PROMPT#', 'value': '闲仁Bot'},
                    {
                        'key': '#LIST#',
                        'obj': [
                            {
                                'obj_kv': [
                                    {'key': 'desc', 'value': str(content)}
                                ]
                            }
                        ]
                    }
                ]
            }
            payload = {
                "msg_id": self.message_id,
                "msg_type": 3,
                "ark": ark,
                "msg_seq": random.randint(10000, 999999)
            }

        if not content and raw:
            if isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"content": raw}
            else:
                payload = raw

        response = None
        if self.event_type in ["GROUP_AT_MESSAGE_CREATE", "GROUP_ADD_ROBOT"]:
            response = BOTAPI(f"/v2/groups/{self.group_id}/messages", "POST", json.dumps(payload))
        elif self.event_type == "C2C_MESSAGE_CREATE":
            response = BOTAPI(f"/v2/users/{self.user_id}/messages", "POST", json.dumps(payload))
        elif self.event_type == "DIRECT_MESSAGE_CREATE":
            response = BOTAPI(f"/v2/dms/{self.guild_id}/messages", "POST", json.dumps(payload))
            
        return response or {}

    def get(self, path: str) -> Any:
        """
        获取消息数据中的指定字段
        :param path: 字段路径
        :return: 字段值
        """
        return get_json_value(self.raw_data, path)

    @staticmethod
    def sanitize_content(content: Optional[str]) -> str:
        """
        清理消息内容
        :param content: 原始内容
        :return: 清理后的内容
        """
        if not content:
            return ""
        return content.lstrip("/").strip()

    def make_msg(self, msg: Union[str, Dict, List]) -> List[Dict]:
        """
        构造消息
        :param msg: 消息内容
        :return: 消息段列表
        """
        result = []
        messages = msg if isinstance(msg, list) else [msg]
        
        for item in messages:
            # 处理非字典输入
            if not isinstance(item, dict):
                item = Segment.text(str(item))
            
            processed = item.copy()
            
            # 类型验证和结构补全
            msg_type = processed.get('type', 'text')
            if msg_type == 'text':
                if 'text' not in processed:
                    processed['text'] = ''
            elif msg_type == 'image':
                if 'file' not in processed:
                    processed['file'] = ''
            elif msg_type == 'at':
                if 'user_id' not in processed:
                    processed['user_id'] = ''
                
            result.append(processed)
            
        return result

    def __str__(self) -> str:
        """字符串表示"""
        return f"{self.event_type}:{self.message_id}:{self.content}" 