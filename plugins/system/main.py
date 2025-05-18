#!/usr/bin/env python
# -*- coding: utf-8 -*-

from core.plugin.PluginManager import Plugin
from db_pool import DatabaseService
import json

class system_plugin(Plugin):
    # 设置插件优先级
    priority = 10

    @staticmethod
    def get_regex_handlers():
        return {
            r'用户统计': {
                'handler': 'get_user_count',
                'owner_only': True  # 仅限主人使用
            }
        }
    
    @staticmethod
    def get_user_count(event):
        # 实例化数据库服务
        db = DatabaseService()
        
        try:
            # 获取用户数量
            user_count_result = db.execute_query(
                'SELECT COUNT(*) as count FROM bot_users'
            )
            user_count = user_count_result['count'] if user_count_result else 0
            
            # 获取群组数量
            group_count_result = db.execute_query(
                'SELECT COUNT(*) as count FROM bot_groups'
            )
            group_count = group_count_result['count'] if group_count_result else 0
            
            # 获取当前群成员数量
            member_count = 0
            if event.group_id:
                group_users_result = db.execute_query(
                    'SELECT users FROM groups_users WHERE group_id = %s',
                    (event.group_id,)
                )
                if group_users_result and group_users_result.get('users'):
                    # 解析JSON数据获取用户数量
                    try:
                        users = group_users_result['users']
                        if isinstance(users, str):
                            users = json.loads(users)
                        member_count = len(users)
                    except:
                        member_count = 0
            
            # 构建并发送响应消息
            info = [
                f'<@{event.user_id}>',
                f'📊机器人统计',
                f'👥数据库用户数量: {user_count}',
                f'👥数据库群组数量: {group_count}',
                f'👥数据库当前群成员数量: {member_count}'
            ]
            
            # 创建按钮
            btn = event.button([
                event.rows([{
                    'text': '用户统计',
                    'data': '/用户统计',
                    'type': 1,
                    'style': 1
                }])
            ])
            
            event.reply('\n'.join(info))
            
        except Exception as e:
            print(f'获取用户统计失败: {e}')
            event.reply('统计服务暂时不可用，请稍后再试') 