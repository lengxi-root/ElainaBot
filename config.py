#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
QQ机器人配置文件
"""

# 机器人配置
appid = ""
# 机器人的secret
secret = "" 

# 机器人主人配置
OWNER_IDS = ["12345678"]  # 主人QQ号列表

# 回复控制开关
OWNER_ONLY_REPLY = False    # 当非主人触发主人专属命令时是否回复提示
SEND_DEFAULT_RESPONSE = False  # 当没有插件匹配命令时，是否发送默认回复

# 数据库配置
DB_CONFIG = {
    'host': '',
    'port': ,
    'user': '',
    'password': '',
    'database': '',
    'pool_name': 'mypool',
    'pool_size': 5,
    'connect_timeout': 10,
    'use_pure': True
} 