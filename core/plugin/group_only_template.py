#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
群聊专用命令回复模板
当用户在私聊中尝试使用群聊专用命令时使用的回复模板
"""

class GroupOnlyTemplate:
    """群聊专用命令回复模板类"""
    
    @staticmethod
    def send_reply(event):
        """
        向私聊用户发送群聊专用命令提示
        @param event: 消息事件对象
        """
        # 获取用户ID用于艾特
        user_id = event.user_id
        
        btn = event.button([
            event.rows([{
                'text': '提示',
                'data': '仅限群聊',
                'type': 2,
                'list': [],  # 空数组，任何人都不能点击
                'style': 0,  # 红色警告风格
                'enter': False
            },{
                'text': '邀请我进群',
                'data': 'https://qun.qq.com/qunpro/robot/qunshare?robot_appid=102134274&robot_uin=3889045760',
                'type': 0,  # 链接类型
                'style': 1
            }])
        ])
        event.reply(f"<@{user_id}> 该指令仅在群聊中可用，请在群聊中使用", btn)

# 为兼容现有代码，保留send_reply函数
def send_reply(event):
    """
    发送回复
    @param event: 消息事件对象
    """
    GroupOnlyTemplate.send_reply(event) 