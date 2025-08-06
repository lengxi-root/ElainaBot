#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import os
from core.plugin.PluginManager import Plugin
from config import OWNER_IDS

# 黑名单文件路径
BLACKLIST_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "blacklist.json")

class BlacklistManager(Plugin):
    priority = 1
    
    @staticmethod
    def _load_blacklist():
        """加载黑名单数据"""
        if not os.path.exists(BLACKLIST_FILE):
            return {}
        with open(BLACKLIST_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @staticmethod
    def _save_blacklist(blacklist):
        """保存黑名单数据"""
        with open(BLACKLIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(blacklist, f, ensure_ascii=False, indent=2)
    
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
        user_id = event.matches[0]
        reason = event.matches[1] if len(event.matches) > 1 and event.matches[1] else "未指明原因"
        
        if user_id in OWNER_IDS:
            return event.reply("无法将主人添加到黑名单")
        
        blacklist = BlacklistManager._load_blacklist()
        blacklist[user_id] = reason
        BlacklistManager._save_blacklist(blacklist)
        event.reply(f"已添加用户 {user_id} 到黑名单\n原因: {reason}")
    
    @staticmethod
    def remove_blacklist(event):
        user_id = event.matches[0]
        blacklist = BlacklistManager._load_blacklist()
        
        if user_id not in blacklist:
            return event.reply(f"用户 {user_id} 不在黑名单中")
        
        reason = blacklist.pop(user_id, "未知")
        BlacklistManager._save_blacklist(blacklist)
        event.reply(f"已移除用户 {user_id}\n原因: {reason}")
    
    @staticmethod
    def view_blacklist(event):
        blacklist = BlacklistManager._load_blacklist()
        
        if not blacklist:
            return event.reply("黑名单为空")
        
        reply = "黑名单列表：\n"
        for user_id, reason in blacklist.items():
            reply += f"{user_id}: {reason}\n"
        
        event.reply(reply.strip())
    
    @staticmethod
    def show_help(event):
        help_text = """黑名单指令：
黑名单添加 [用户ID] [原因] - 添加用户
黑名单删除 [用户ID] - 删除用户  
黑名单查看 - 查看列表
黑名单帮助 - 显示帮助"""
        event.reply(help_text) 