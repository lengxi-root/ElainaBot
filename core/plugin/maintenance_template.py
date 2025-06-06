#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
维护模式回复模板
"""

from config import MAINTENANCE_CONFIG, USE_MARKDOWN

def get_maintenance_message():
    """获取维护消息"""
    message = MAINTENANCE_CONFIG.get('message', '系统正在维护中，请稍后再试...')
    return message

def send_reply(event):
    """发送维护模式回复"""
    # 获取维护消息
    message = get_maintenance_message()
    
    # 创建按钮
    buttons = event.button([
        event.rows([
            {
                'text': '联系管理员',
                'data': '联系管理员',
                'enter': True,
                'style': 5
            }
        ])
    ])
    
    # 发送回复
    event.reply(message, buttons) 