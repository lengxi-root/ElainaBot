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
        # 获取用户ID用于艾特
        user_id = event.user_id
        
        # 固定回复格式，带按钮
        btn = event.button([
            event.rows([
               {
                'text': '菜单',
                'data': '/菜单',
                'enter': True,
                'style': 1
            }, {
                'text': '娱乐菜单',
                'data': '/娱乐菜单',
                'enter': True,
                'style': 1
            }
            ]),
            event.rows([
               {
                'text': '盯伊蕾娜',
                'data': '/盯伊蕾娜',
                'enter': True,
                'style': 1
            }, {
                'text': '邀我进群',
                'data': 'https://qun.qq.com/qunpro/robot/qunshare?robot_appid=102134274&robot_uin=3889045760',
                'type': 0,
                'style': 1
            }
            ]),
            event.rows([
               {
                'text': '反馈与投稿',
                'data': 'https://www.wjx.cn/vm/rJ1ZKHn.aspx',
                'type': 0,
                'style': 4
            }, {
                'text': '提供赞助',
                'data': 'https://afdian.com/a/VSTlengxi',
                'type': 0,
                'style': 4
            }
            ])
        ])
        event.reply(f"![错误指令 #1360px #680px](https://gd-hbimg.huaban.com/53f695e975a52018a87ab8dc21bffff16da658ff7c6d7-fDXTPP)\n\n><@{user_id}> ", btn)
        
        # 如需使用纯文本回复，可以替换为：
        # event.reply(f"<@{user_id}> Hello Wolrd")

# 为兼容现有代码，保留原有函数
def send_reply(event):
    """
    发送默认回复
    @param event: 消息事件对象
    """
    DefaultReplyTemplate.send_reply(event) 