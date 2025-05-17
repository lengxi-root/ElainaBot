#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
默认回复模板
当没有任何插件匹配用户输入的指令时使用的回复模板
"""

class DefaultReplyTemplate:
    """默认回复模板类"""
    
    @staticmethod
    def send_reply(event):
        """
        发送默认回复
        @param event: 消息事件对象
        """
        # 固定回复格式，带按钮
        btn = event.button([
            event.rows([
                {
                    'text': '按钮',
                    'data': '消息内容',
                    'type': 2,
                    'style': 0,
                    'enter': False,
                }
            ])
        ])
        event.reply('Hello World', btn)
        
        # 如需使用纯文本回复，可以替换为：
        # event.reply('Hello Wolrd')

# 为兼容现有代码，保留原有函数
def send_reply(event):
    """
    发送默认回复
    @param event: 消息事件对象
    """
    DefaultReplyTemplate.send_reply(event) 