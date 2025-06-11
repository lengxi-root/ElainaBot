#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
QQ机器人配置文件
"""

appid = " "    
secret = " " 
OWNER_IDS = [" "]  # 主人QQ号列表
USE_MARKDOWN = Flase  # True使用Markdown，False使用纯文本格式

# 是否在没有插件匹配命令时发送默认回复
SEND_DEFAULT_RESPONSE = True

# 使用正则表达式排除不需要默认回复的消息（黑名单模式）
DEFAULT_RESPONSE_EXCLUDED_REGEX = [
  
]

# 是否启用维护模式
MAINTENANCE_MODE = False

# 是否启用入群欢迎消息
ENABLE_WELCOME_MESSAGE = True

# 是否启用新用户欢迎消息
ENABLE_NEW_USER_WELCOME = True

# 控制台日志配置
LOG_CONFIG = {
    'level': 'INFO',        # 日志级别: DEBUG, INFO, WARNING, ERROR, CRITICAL
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
}

# 图床配置
IMAGE_BED = {
    # QQ官方机器人图床
    'qq_bot': {
        'enabled': True,        # 是否启用此图床
        'priority': 1,          # 优先级，数字越小优先级越高
        'channel_id': '1673127'  # QQ机器人官方图床的频道ID
    },
    
    # QQ互联图床
    'qq_share': {
        'enabled': False,       # 默认关闭QQ互联图床
        'priority': 2,          # 优先级，数字越小优先级越高
        'p_uin': ' ',  # QQ号
        'p_skey': ' '  # p_skey值
    }
}
"""
Q互联图床：
   - 需要同时填写QQ号(p_uin)和p_skey
   - 获取p_skey方法：使用电脑浏览器登录connect.qq.com，从cookie中提取p_skey值

注意：至少需要启用一个图床，否则图片上传功能将无法使用
"""
# 数据库配置
DB_CONFIG = {
    # 基本连接配置
    'host': ' ',
    'port': 3306,
    'user': ' ',
    'password': ' ',
    'database': ' ',
    'charset': 'utf8mb4',
    
    # 连接池基础设置
    'pool_size': 8,              # 最大连接数
    'min_pool_size': 3,          # 最小连接数(空闲)
    'connect_timeout': 5,        # 连接超时时间(秒)
    'read_timeout': 30,          # 读取超时时间(秒)
    'write_timeout': 30,         # 写入超时时间(秒)
    'autocommit': True,          # 自动提交
    
    # 连接池高级设置
    'connection_lifetime': 1200, # 连接最长生命周期(秒)，默认20分钟
    'gc_interval': 60,           # 垃圾回收间隔(秒)
    'idle_timeout': 10,          # 空闲连接超时(秒)
    'thread_pool_size': 5,       # 线程池大小(并发查询)
    'request_timeout': 3.0,      # 获取连接请求超时时间(秒)
    'retry_count': 3,            # 获取连接失败重试次数
    'retry_interval': 0.5,       # 重试间隔(秒)
    
    # 监控和维护设置
    'max_usage_time': 30,        # 单个连接最长使用时间(秒)
    'max_connection_hold_time': 20, # 连接占用超时警告阈值(秒)
    'long_query_warning_time': 60,  # 长查询警告阈值(秒)
    'pool_status_interval': 180,    # 池状态记录间隔(秒)
    'pool_maintenance_interval': 15, # 池维护间隔(秒)
    
    # 兼容性设置
    'pool_name': 'mbot_pool',
    'use_pure': True,            # 使用纯Python实现
    'buffered': False            # 缓存查询结果
}

# 日志数据库配置
LOG_DB_CONFIG = {
    # 基本配置
    'enabled': True,                # 是否启用日志数据库
    'use_main_db': False,           # 是否使用主数据库配置(DB_CONFIG)
    'insert_interval': 20,          # 日志写入间隔(秒)，0表示立即写入
    'batch_size': 100,              # 每批次最大写入记录数
    'table_prefix': 'Mlog_',        # 日志表前缀
    
    # 日志表配置
    'create_tables': True,          # 是否自动创建日志表
    'table_per_day': True,          # 是否按天创建表(例如Mlog_YYYYMMDD)
    
    # 日志保留策略
    'retention_days': 90,           # 日志保留天数，0表示永久保留
    'auto_cleanup': True,           # 是否自动清理过期日志表
    
    # 独立数据库配置(仅当use_main_db=False时有效)
    'host': ' ',
    'port':  ,
    'user': ' ',
    'password': ' ',
    'database': ' ',
    'charset': 'utf8mb4',
    
    # 连接池配置(仅当use_main_db=False时有效)
    'pool_size': 5,
    'min_pool_size': 2,
    'connect_timeout': 3,
    'read_timeout': 10,
    'write_timeout': 10,
    'autocommit': True,
    
    # 错误处理
    'max_retry': 3,                 # 写入失败时的最大重试次数
    'retry_interval': 2             # 重试间隔(秒)
}

