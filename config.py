#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
QQ机器人配置文件
"""

# 机器人配置
appid = "appid"
# 机器人的secret
secret = "密钥" 

# 机器人主人配置
OWNER_IDS = ["12345678"]  # 主人QQ号列表

# 回复控制开关
OWNER_ONLY_REPLY = False    # 当非主人触发主人专属命令时是否回复提示
SEND_DEFAULT_RESPONSE = False  # 当没有插件匹配命令时，是否发送默认回复

# 数据库配置
DB_CONFIG = {
    'host': '127.0.0.1',
    'port': 3306,
    'user': '库用户',
    'password': '库密码',
    'database': '账号',
    'charset': 'utf8mb4',
    # 连接池设置
    'pool_name': 'mbot_pool',
    'pool_size': 5,         # 连接池大小
    'connect_timeout': 5,   # 连接超时时间(秒)
    'read_timeout': 30,     # 读取超时时间(秒)
    'write_timeout': 30,    # 写入超时时间(秒)
    'autocommit': True,     # 自动提交
    'use_pure': True,       # 使用纯Python实现
    'buffered': True        # 缓存查询结果
}

# 日志配置
LOG_CONFIG = {
    'level': 'INFO',        # 日志级别: DEBUG, INFO, WARNING, ERROR, CRITICAL
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    'file': 'logs/mbot.log', # 日志文件路径
    'max_size': 10485760,   # 单个日志文件大小上限(字节)，默认10MB
    'backup_count': 5       # 保留日志文件数量
} 
