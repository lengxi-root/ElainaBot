#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
新用户欢迎消息模板
当群聊中有新用户时使用的欢迎模板
"""

class WelcomeReplyTemplate:
    """新用户欢迎消息模板类"""
    
    @staticmethod
    def send_reply(event, user_count):
        """
        发送新用户欢迎消息
        @param event: 消息事件对象
        @param user_count: 当前总用户数
        """
        # 获取用户ID用于艾特和头像
        user_id = event.user_id
        
        # 欢迎消息内容
        welcome_msg = (
            f"![伊蕾娜 #200px #200px](https://q.qlogo.cn/qqapp/102134274/{user_id}/640)\n"
            f"欢迎<@{user_id}>！您是第{user_count}位使用伊蕾娜的伊宝！  \n"
            f"\n> 可以把伊蕾娜邀请到任意群使用哦！"
        )
        
        # 欢迎按钮
        btn = event.button([
            event.rows([
                {
                    'text': '🎄️ 菜单',
                    'data': '菜单',
                    'enter': True,
                    'style': 1
                },
                {
                    'text': '🪀️ 娱乐菜单',
                    'data': '/娱乐菜单',
                    'enter': True,
                    'style': 1
                }
            ]),
            event.rows([
                {
                    'text': '♥️ 群友老婆',
                    'data': '/群友老婆',
                    'enter': True,
                    'style': 1
                },
                {
                    'text': '✨ 今日老婆',
                    'data': '/今日老婆',
                    'enter': True,
                    'style': 1
                }
            ]),
            event.rows([
                {
                    'text': '🎆 邀伊蕾娜进群',
                    'data': 'https://qun.qq.com/qunpro/robot/qunshare?robot_appid=102134274&robot_uin=3889045760',
                    'type': 0,
                    'style': 1
                }
            ])
        ])
        # 发送欢迎消息
        event.reply(welcome_msg, btn)

# 为兼容现有代码，保留原有函数
def send_reply(event, user_count):
    """
    发送新用户欢迎消息
    @param event: 消息事件对象
    @param user_count: 当前总用户数
    """
    WelcomeReplyTemplate.send_reply(event, user_count) 