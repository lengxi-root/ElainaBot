#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
import re
import time
from core.plugin.PluginManager import Plugin
from config import OWNER_IDS

# 黑名单文件路径
BLACKLIST_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "blacklist.json")

class BlacklistManager(Plugin):
    """
    黑名单管理插件，允许主人添加和删除黑名单用户
    """
    priority = 1  # 高优先级，确保黑名单管理命令能优先被处理
    
    @staticmethod
    def get_regex_handlers():
        return {
            r'黑名单添加 *([a-zA-Z0-9]+) *(.+)?': {'handler': 'add_blacklist', 'owner_only': True},
            r'黑名单删除 *([a-zA-Z0-9]+)': {'handler': 'remove_blacklist', 'owner_only': True},
            r'黑名单查看': {'handler': 'view_blacklist', 'owner_only': True},
            r'黑名单帮助': {'handler': 'show_help', 'owner_only': True},
        }
    
    @staticmethod
    def add_blacklist(event):
        """添加用户到黑名单"""
        user_id = event.matches[0]
        reason = event.matches[1] if len(event.matches) > 1 and event.matches[1] else "未指明原因"
        
        # 不允许将主人添加到黑名单
        if user_id in OWNER_IDS:
            return event.reply(f"无法将主人添加到黑名单！")
        
        # 加载黑名单数据
        blacklist = {}
        if os.path.exists(BLACKLIST_FILE):
            try:
                with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                    blacklist = json.load(f)
            except Exception as e:
                return event.reply(f"读取黑名单文件失败: {str(e)}")
        
        # 添加用户到黑名单
        blacklist[user_id] = reason
        
        # 保存黑名单数据
        try:
            with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(blacklist, f, ensure_ascii=False, indent=2)
            return event.reply(f"已将用户 {user_id} 添加到黑名单\n原因: {reason}")
        except Exception as e:
            return event.reply(f"保存黑名单数据失败: {str(e)}")
    
    @staticmethod
    def remove_blacklist(event):
        """从黑名单中移除用户"""
        user_id = event.matches[0]
        
        # 加载黑名单数据
        if not os.path.exists(BLACKLIST_FILE):
            return event.reply("黑名单文件不存在")
        
        try:
            with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                blacklist = json.load(f)
        except Exception as e:
            return event.reply(f"读取黑名单文件失败: {str(e)}")
        
        # 检查用户是否在黑名单中
        if user_id not in blacklist:
            return event.reply(f"用户 {user_id} 不在黑名单中")
        
        # 移除用户
        reason = blacklist.pop(user_id, "未知")
        
        # 保存黑名单数据
        try:
            with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(blacklist, f, ensure_ascii=False, indent=2)
            return event.reply(f"已将用户 {user_id} 从黑名单中移除\n原先原因: {reason}")
        except Exception as e:
            return event.reply(f"保存黑名单数据失败: {str(e)}")
    
    @staticmethod
    def view_blacklist(event):
        """查看黑名单"""
        # 加载黑名单数据
        if not os.path.exists(BLACKLIST_FILE):
            return event.reply("黑名单文件不存在")
        
        try:
            with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
                blacklist = json.load(f)
        except Exception as e:
            return event.reply(f"读取黑名单文件失败: {str(e)}")
        
        # 格式化输出
        if not blacklist:
            return event.reply("黑名单为空")
        
        reply = "当前黑名单列表：\n\n"
        for user_id, reason in blacklist.items():
            reply += f"- 用户: {user_id}\n  原因: {reason}\n\n"
        
        return event.reply(reply.strip())
    
    @staticmethod
    def show_help(event):
        """显示黑名单管理帮助"""
        help_text = """黑名单管理指令：

- 黑名单添加 [用户ID] [原因]
  将用户添加到黑名单

- 黑名单删除 [用户ID]
  从黑名单中删除用户

- 黑名单查看
  查看当前所有黑名单用户

- 黑名单帮助
  显示本帮助信息
"""
        return event.reply(help_text) 