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
ENABLE_WELCOME_MESSAGE = False                    # 是否启用入群欢迎消息功能
ENABLE_NEW_USER_WELCOME = False                   # 是否启用新用户首次交互欢迎
ENABLE_FRIEND_ADD_MESSAGE = False                 # 是否启用添加好友自动发送消息功能
SAVE_RAW_MESSAGE_TO_DB = False                   # 是否将消息的原始内容存储到数据库中

# 服务器配置 - HTTP服务相关设置
SERVER_CONFIG = {
    'host': '0.0.0.0',                          # HTTP服务监听地址，0.0.0.0表示监听所有接口
    'port': 5001,                               # HTTP服务监听端口号
    'web_dual_process': False,                  # 是否将Web面板作为独立进程启动，开启后日志无法推送，但不会阻塞程序
    'web_port': 5002,                          # Web面板独立进程端口号（仅在dual_process=True时有效）
}

# Web界面外观配置 - 自定义框架名称和网页图标
WEB_INTERFACE = {
    'framework_name': 'Elaina',                 # 框架名称，显示在页面标题和导航栏中
    'favicon_url': f'https://q1.qlogo.cn/g?b=qq&nk={ROBOT_QQ}&s=100',  # 网页图标URL，默认使用机器人QQ头像
    'mobile_title_suffix': '手机仪表盘',        # 移动端标题后缀
    'pc_title_suffix': '仪表盘',               # PC端标题后缀
    'login_title_suffix': '面板'               # 登录页面标题后缀
}

# 日志配置 - 控制台日志输出设置
LOG_CONFIG = {
    'level': 'INFO',                            # 日志级别: DEBUG/INFO/WARNING/ERROR/CRITICAL
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'  # 日志格式模板
}

# WebSocket配置 - 实时通信连接设置
WEBSOCKET_CONFIG = {
    'enabled': True,                           # 是否启用WebSocket连接功能
    'custom_url': None,                         # 自定义WebSocket连接地址，设置则直接连接，不懂不要填写
    'log_level': 'INFO',                        # WebSocket专用日志级别
    'log_message_content': False,               # 是否记录消息内容(调试模式)
}

# Web面板安全配置 - 管理界面访问控制
WEB_SECURITY = {
    'access_token': 'admin123',              # Web面板访问令牌，URL参数验证
    'admin_password': 'admin1234',           # 管理员登录密码
    'production_mode': True,                    # 生产环境模式，影响错误信息显示
}

# 主数据库配置 - 业务数据存储设置
DB_CONFIG = {
    # 连接基础配置
    'host': '127.0.0.1',                       # 数据库服务器地址
    'port': 3306,                              # 数据库服务器端口
    'user': '',                             # 数据库用户名
    'password': '',                # 数据库密码
    'database': '',                         # 数据库名称
    'charset': 'utf8mb4',                      # 字符集，支持完整Unicode
    
    # 连接池基础设置
    'min_pool_size': 3,                        # 连接池最小保持连接数
    'connect_timeout': 5,                      # 数据库连接超时时间(秒)
    'read_timeout': 7,                         # 数据读取超时时间(秒)
    'write_timeout': 7,                        # 数据写入超时时间(秒)
    'autocommit': True,                        # 是否自动提交事务
    'table_prefix': 'M_',                      # 储存用户名前缀，可自定义修改

    
    # 连接池高级设置
    'connection_lifetime': 300,               # 单个连接最大生命周期(秒，5分钟)
    'retry_count': 3,                          # 连接失败重试次数
    'retry_interval': 0.5,                     # 重试间隔时间(秒)
}

# 日志数据库配置 - 系统日志存储设置
LOG_DB_CONFIG = {
    # 连接基础配置
    'host': '127.0.0.1',                       # 日志数据库服务器地址
    'port': 3306,                              # 日志数据库服务器端口
    'user': '',                             # 日志数据库用户名
    'password': '',                # 日志数据库密码
    'database': '',                         # 日志数据库名称
    'charset': 'utf8mb4',                      # 字符集配置

    # 功能开关配置
    'enabled': True,                           # 是否启用日志数据库功能
    'use_main_db': False,                      # 是否复用主数据库配置(DB_CONFIG)
    'create_tables': True,                     # 是否自动创建日志表
    'table_per_day': True,                     # 是否按日期自动分表
    'fallback_to_file': True,                  # 数据库写入失败时是否回退到文件记录

    # 日志写入策略
    'insert_interval': 20,                     # 批量写入间隔时间(秒)，0表示立即写入
    'batch_size': 1000,                        # 每批次最大写入日志记录数
    'table_prefix': 'Mlog_',                   # 日志表名前缀，按日期自动分表
    
    # 日志保留策略
    'retention_days': 30,                      # 日志保留天数，0表示永久保留
    'auto_cleanup': True,                      # 是否自动清理过期日志表
    
    # 连接池配置(仅当use_main_db=False时生效)
    'connect_timeout': 3,                      # 连接超时时间(秒)
    'read_timeout': 10,                        # 读取超时时间(秒)
    'write_timeout': 10,                       # 写入超时时间(秒)
    'autocommit': True,                        # 自动提交事务
    
    # 错误处理配置
    'max_retry': 3,                            # 写入失败最大重试次数
    'retry_interval': 2                        # 重试间隔时间(秒)
}

COS_CONFIG = {
    'enabled': True,                          # 是否启用COS上传功能
    'secret_id': 'AKID开头的ID',             # 腾讯云API密钥ID
    'secret_key': '密钥',           # 腾讯云API密钥Key，需要从控制台获取完整的SecretKey
    'region': 'ap-guangzhou',                   # 存储桶区域
    'bucket_name': ' ',         # 存储桶名称
    'domain': None,                            # 自定义域名(可选)
    'upload_path_prefix': 'mlog/',             # 默认上传路径前缀
    'max_file_size': 50 * 1024 * 1024,       # 最大文件大小50MB
}

# Bilibili图床配置 - B站图片上传功能
BILIBILI_IMAGE_BED_CONFIG = {
    'enabled': True,  # 是否启用Bilibili图床功能
    'csrf_token': "",  # Bilibili CSRF Token (bili_jct)
    'sessdata': "",  # Bilibili SESSDATA Cookie
    'bucket': "openplatform",  # 上传bucket类型，一般为openplatform
}

# 框架自动更新配置 - 框架版本更新相关
AUTO_UPDATE_CONFIG = {
    'enabled': False,  # 是否启用自动更新功能
    'check_interval': 1800,  # 更新检测间隔时间(秒)，默认1800秒(30分钟)
    'auto_update': False,  # 是否自动更新(True=自动更新，False=仅检测提醒)
    'backup_enabled': True,  # 更新前是否备份
    
    # 更新时不覆盖的文件和文件夹列表（支持通配符）
    'skip_files': [
        "config.py",  # 配置文件
        "core/event/markdown_templates.py",  # Markdown模板
        "core/plugin/message_templates.py",  # 消息模板
        "plugins/",  # 插件目录
        "data/",  # 数据目录
        ".git/",  # Git目录
        "__pycache__/",  # Python缓存
        "*.pyc",  # Python编译文件
        # 可在此添加自定义不覆盖的文件/文件夹

    ],
}