#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
主人命令权限控制回复模板
当非主人用户尝试执行主人专属命令时使用的回复模板
"""

class OwnerReplyTemplate:
    """主人命令权限回复模板类"""
    
    @staticmethod
    def send_reply(event):
        """
        向非主人用户发送无权限提示
        @param event: 消息事件对象
        """
        # 固定回复格式，纯文本
        event.reply("暂无权限，只有主人能操作此命令")
        
        # 如需使用带按钮的回复，可以使用下面的代码
        # btn = event.button([
        #     event.rows([{
        #         'text': '提示',
        #         'data': '权限不足',
        #         'type': 2,
        #         'style': 4,  # 红色警告风格
        #         'enter': False,
        #     }])
        # ])
        # event.reply("暂无权限，只有主人能操作此命令", btn)

# 为兼容现有代码，保留send_reply函数
def send_reply(event):
    """
    发送回复
    @param event: 消息事件对象
    """
    OwnerReplyTemplate.send_reply(event) 