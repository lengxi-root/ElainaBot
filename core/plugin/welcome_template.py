#!/usr/bin/env python
# -*- coding: utf-8 -*-

# 当机器人被添加到新的群聊时的回复模板
WELCOME_MESSAGE = "![感谢 #1755px #2048px](https://gd-hbimg.huaban.com/d8b5c087d33e7d25835db96adab5f226227e943a165000-gzpWLe)\n__「你绝不是只身一人」 「我一直在你身边。」\n今朝依旧，今后亦然。__\n\n>大家好，我是有着沉鱼落雁般美貌的灰之魔女伊蕾娜！\n\n>可以为群内提供各种各样的群娱互动，与一些高质量图库功能，欢迎大家使用！\n***\n\n>注:所有指令必须_[@伊蕾娜]_才能使用,可以先尝试发送娱乐菜单，有按钮可以一键发送命令使用哦~\n"

def get_welcome_message():
    """获取欢迎消息"""
    return WELCOME_MESSAGE

def send_reply(event):
    """发送欢迎消息"""
    try:
        # 获取欢迎消息
        welcome_message = get_welcome_message()
        
        # 构建回复按钮
        btn = event.button([
             event.rows([{
                 'text': '娱乐菜单',
                 'data': '/娱乐菜单',
                 'type': 2,
                 'style': 1,  # 红色警告风格
                 'enter': True,
             },{
                 'text': '今日老婆',
                 'data': '/今日老婆',
                 'type': 2,
                 'style': 1,  # 红色警告风格
                 'enter': True,
             }]),
             event.rows([{
                 'text': '关于',
                 'data': '/关于',
                 'type': 2,
                 'style': 1,  # 红色警告风格
                 'enter': True,
             },{
                'text': '邀我进群',
                'data': 'https://qun.qq.com/qunpro/robot/qunshare?robot_appid=102134274&robot_uin=3889045760',
                'type': 0,
                'style': 1
            }])
         ])
    
        # 发送消息
        result = event.reply(welcome_message, btn)
        return result is not None
            
    except Exception:
        return False 