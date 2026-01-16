#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""QQ机器人配置文件"""

# 基础配置 - QQ开发者平台相关
appid = ""                              # 机器人APPID，在QQ开发者平台获取
secret = ""      # 机器人密钥
ROBOT_QQ = ""                          # 机器人QQ号
IMAGE_BED_CHANNEL_ID = ''                 # 图床频道子频道ID，用于图床上传
OWNER_IDS = [""] # 主人OPENID列表，可使用仅主人插件

# 消息处理配置 - 控制消息格式和行为
USE_MARKDOWN = False                             # 是否使用Markdown格式发送消息 MD模板请使用False
HIDE_AVATAR_GLOBAL = False                        # 全局启用Markdown无头像模式（私聊可用）
SEND_DEFAULT_RESPONSE = False                     # 无匹配命令时是否发送默认回复
DEFAULT_RESPONSE_EXCLUDED_REGEX = []             # 排除默认回复的消息正则表达式列表
MAINTENANCE_MODE = False                         # 维护模式开关，开启后机器人暂停服务
BLACKLIST_ENABLED = True                         # 黑名单功能开关，关闭后黑名单用户能正常使用插件
GROUP_BLACKLIST_ENABLED = True  # 群黑名单功能开关，开启后黑名单群内所有消息都会发送群黑名单模板
ENABLE_WELCOME_MESSAGE = False                    # 是否启用入群欢迎消息功能
ENABLE_NEW_USER_WELCOME = False                   # 是否启用新用户首次交互欢迎
ENABLE_FRIEND_ADD_MESSAGE = False                 # 是否启用添加好友自动发送消息功能
SAVE_RAW_MESSAGE_TO_DB = False                   # 是否将消息的原始内容存储到数据库中,开启后可能会数据库硬盘占用略微上涨

# 用户ID反转模式配置（可选） - 控制user_id和union_openid的使用
USE_UNION_ID_FOR_GROUP = False  # 群聊/私聊ID反转模式：True时user_id使用union_openid，union_openid使用原user_id；若union_openid为空则使用原user_id
USE_UNION_ID_FOR_CHANNEL = False  # 频道ID反转模式：True时user_id使用union_openid，union_openid使用原user_id；若union_openid为空则使用原user_id
# 注意：开启反转模式后，event.raw_user_id 始终保存原始user_id（用于@人等场景）

# Markdown AJ万能模板配置 - 没有万能模板请留空
MARKDOWN_AJ_TEMPLATE = {
    'template_id': "1",  # AJ 模板 ID
    'keys': "a,b,c,d,e,f,g,h,i,j",  # 模板参数键名，使用逗号分割
}

# 服务器配置 - HTTP服务相关设置
SERVER_CONFIG = {
    'host': "0.0.0.0",  # HTTP服务监听地址，0.0.0.0表示监听所有接口
    'port': 5001,  # HTTP服务监听端口号
}

# WebSocket配置 - 实时通信连接设置
WEBSOCKET_CONFIG = {
    'enabled': True,  # 是否启用WebSocket连接功能
    'custom_url': None,  # 自定义WebSocket连接地址，如果设置则直接连接，不懂不要填写
    'log_level': "INFO",  # WebSocket专用日志级别
    'log_message_content': False,  # 是否记录消息内容(调试模式)
}

# Web面板配置 - 安全控制和界面外观设置
WEB_CONFIG = {
    # 安全控制配置
    'access_token': "admin",  # Web面板访问令牌，URL参数验证
    'admin_password': "admin",  # 管理员登录密码
    
    # 界面外观配置
    'framework_name': "Elaina",  # 框架名称，显示在页面标题和导航栏中
    'favicon_url': f'https://q1.qlogo.cn/g?b=qq&nk={ROBOT_QQ}&s=100',  # 网页图标URL，默认使用机器人QQ头像
    'pc_title_suffix': "仪表盘",  # PC端标题后缀
    'login_title_suffix': "面板",  # 登录页面标题后缀
}
# 日志数据库配置 - 系统日志存储设置
LOG_DB_CONFIG = {
    # 连接基础配置
    'host': "127.0.0.1",  # 日志数据库服务器地址
    'port': 3306,  # 日志数据库服务器端口
    'user': "",  # 日志数据库用户名
    'password': "",  # 日志数据库密码
    'database': "",  # 日志数据库名称
    
    # 日志策略
    'insert_interval': 2,  # 批量写入间隔时间(秒)，0表示立即写入
    'batch_size': 1000,  # 每批次最大写入日志记录数
    'table_prefix': f"{appid}_",  # 日志表名前缀，使用机器人appid作为前缀
    'retention_days': 5,  # 日志保留天数，0表示永久保留
    'max_retry': 3,  # 写入失败最大重试次数
    'retry_interval': 2,  # 重试间隔时间(秒)
    
    # web日志界面加载配置
    'initial_load_count': 50,  # 进入日志界面时自动加载的今日日志条数
}

# 主数据库配置 - 业务数据存储设置
DB_CONFIG = {
    'enabled': True,  # 是否启用主数据库（线程池供插件使用，如不需要可设为False）
    'host': "127.0.0.1",  # 数据库服务器地址
    'port': 3306,  # 数据库服务器端口
    'user': "",  # 数据库用户名
    'password': "",  # 数据库密码
    'database': "",  # 数据库名称
    
    # 连接池基础设置
    'min_pool_size': 5,  # 连接池最小保持连接数
    'connect_timeout': 5,  # 数据库连接超时时间(秒)
    'read_timeout': 3,  # 数据读取超时时间(秒)
    'write_timeout': 3,  # 数据写入超时时间(秒)
    'autocommit': True,  # 是否自动提交事务
    
    # 连接池高级设置
    'connection_lifetime': 300,  # 单个连接最大生命周期(秒，5分钟)
    'retry_count': 3,  # 连接失败重试次数
    'retry_interval': 0.5,  # 重试间隔时间(秒)
}

# Redis缓存配置 - 可选功能，用于缓存和高速数据存储
REDIS_CONFIG = {
    'enabled': False,  # 是否启用Redis连接池（需要安装redis模块: pip install redis）
    'host': "127.0.0.1",  # Redis服务器地址
    'port': 6379,  # Redis服务器端口
    'password': None,  # Redis密码，无密码设为None
    'db': 0,  # 数据库编号（0-15）
    
    # 连接池设置
    'max_connections': 50,  # 连接池最大连接数
    'socket_timeout': 5,  # 套接字超时时间(秒)
    'socket_connect_timeout': 5,  # 连接超时时间(秒)
    'retry_on_timeout': True,  # 超时时是否重试
    'health_check_interval': 30,  # 健康检查间隔(秒)
    'decode_responses': True,  # 是否自动解码响应为字符串
}
# 腾讯云COS对象存储配置 - 简单上传功能
COS_CONFIG = {
    'enabled': True,  # 是否启用COS上传功能
    'secret_id': "AKID开头的ID",  # 腾讯云API密钥ID
    'secret_key': "密钥",  # 腾讯云API密钥Key
    'region': "ap-guangzhou",  # 存储桶区域
    'bucket_name': "",  # 存储桶名称
    'domain': None,  # 自定义域名(可选)
    'upload_path_prefix': "meme/",  # 默认上传路径前缀
    'max_file_size': 30 * 1024 * 1024,       # 最大文件大小30MB
}

# Bilibili图床配置 - B站图片上传功能
BILIBILI_IMAGE_BED_CONFIG = {
    'enabled': True,  # 是否启用Bilibili图床功能
    'csrf_token': "",  # Bilibili CSRF Token (bili_jct)
    'sessdata': "",  # Bilibili SESSDATA Cookie
    'bucket': "openplatform",  # 上传bucket类型，一般为openplatform
}

# 文件保护配置 - 系统更新时不覆盖的文件和文件夹列表（支持通配符）
PROTECTED_FILES = [
    "config.py",  # 配置文件
    "core/event/markdown_templates.py",  # Markdown模板
    "core/plugin/message_templates.py",  # 消息模板
    "plugins/",  # 插件目录
    # 用户可在此添加自定义不覆盖的文件/文件夹
]