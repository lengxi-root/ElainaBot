#!/usr/bin/env python
# -*- coding: utf-8 -*-

from core.plugin.PluginManager import Plugin

class example_plugin(Plugin):
    # 设置插件优先级（数字越小，优先级越高，默认为10）
    priority = 5
    
    @staticmethod
    def get_regex_handlers():
        return {
            # 格式1：使用字符串值指定处理函数（所有人可用）
            r'^普通文字': 'text',
            
            # 格式2：使用字典指定处理函数和权限
            r'^按钮': {
                'handler': 'btn',      # 处理函数名
                'owner_only': False    # 是否仅限主人使用
            },
            
            # 主人专属命令示例
            r'^(#|\/)?我的id$': {
                'handler': 'getid',
                'owner_only': True     # 仅限主人使用
            },
            
            # 多插件匹配测试
            r'^测试优先级': 'test_priority_1',
            r'^测试优先级2': 'test_priority_2'
        }
    
    @staticmethod
    def text(event):
        event.reply('Hello Wolrd')
        # 返回 False 或不返回值，表示处理完成，不继续匹配其他插件
    
    @staticmethod
    def btn(event):
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
        # 返回 False 或不返回值，表示处理完成，不继续匹配其他插件
    
    @staticmethod
    def getid(event):
        info = f"<@{event.user_id}>\n"
        info += f"请求用户: {event.user_id}\n"
        info += f"群组ID: {event.group_id}\n"
        info += f"【主人专属命令】\n"
        event.reply(info)
        # 返回 False 或不返回值，表示处理完成，不继续匹配其他插件
    
    @staticmethod
    def test_priority_1(event):
        event.reply("高优先级插件的处理函数1")
        # 返回 True 表示继续执行后续匹配的插件
        return True
    
    @staticmethod
    def test_priority_2(event):
        event.reply("高优先级插件的处理函数2")
        # 返回 False 或不返回值，表示处理完成，不继续匹配其他插件 