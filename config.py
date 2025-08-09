#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""QQ机器人配置文件"""

# 基础配置 - QQ开发者平台相关
appid = ""                              # 机器人APPID，在QQ开发者平台获取
secret = ""      # 机器人密钥
ROBOT_QQ = ""                          # 机器人QQ号
IMAGE_BED_CHANNEL_ID = ''                 # 图床频道子频道ID，用于图床上传
OWNER_IDS = ["8C7A05AC58E3BCAAA3E83B22486FAF8F"] # 主人QQ号列表，可使用仅主人插件

# 消息处理配置 - 控制消息格式和行为
USE_MARKDOWN = False                             # 是否使用Markdown格式发送消息 MD模板请使用False
HIDE_AVATAR_GLOBAL = False                        # 全局启用Markdown无头像模式（私聊可用）
SEND_DEFAULT_RESPONSE = False                     # 无匹配命令时是否发送默认回复
DEFAULT_RESPONSE_EXCLUDED_REGEX = []             # 排除默认回复的消息正则表达式列表
MAINTENANCE_MODE = False                         # 维护模式开关，开启后机器人暂停服务
ENABLE_WELCOME_MESSAGE = False                    # 是否启用入群欢迎消息功能
ENABLE_NEW_USER_WELCOME = False                   # 是否启用新用户首次交互欢迎
SAVE_RAW_MESSAGE_TO_DB = False                   # 是否将消息的原始内容存储到数据库中

# 服务器配置 - HTTP服务相关设置
SERVER_CONFIG = {
    'host': '0.0.0.0',                          # HTTP服务监听地址，0.0.0.0表示监听所有接口
    'port': 5001,                               # HTTP服务监听端口号
    'socket_timeout': 30,                       # Socket连接超时时间(秒)
    'keepalive': True,                          # 是否启用HTTP Keep-Alive连接复用
}

# 日志配置 - 控制台日志输出设置
LOG_CONFIG = {
    'level': 'INFO',                            # 日志级别: DEBUG/INFO/WARNING/ERROR/CRITICAL
    'format': '%(asctime)s - %(name)s - %(levelname)s - %(message)s'  # 日志格式模板
}

# WebSocket配置 - 实时通信连接设置
WEBSOCKET_CONFIG = {
    'enabled': True,                           # 是否启用WebSocket连接功能
    'auto_connect': True,                       # 启动时是否自动建立连接
    'client_name': 'elaina_main',               # WebSocket客户端标识名称
    'reconnect_interval': 1,                    # 断线重连间隔时间(秒)
    'max_reconnects': -1,                       # 最大重连次数，-1表示无限重连
    'log_level': 'INFO',                        # WebSocket专用日志级别
    'log_message_content': False,               # 是否记录消息内容(调试模式)
}

# Web面板安全配置 - 管理界面访问控制
WEB_SECURITY = {
    'access_token': 'admin123',              # Web面板访问令牌，URL参数验证
    'admin_password': 'admin1234',           # 管理员登录密码
    'cookie_secret': 'elaina_cookie_secret_key_2024_v1',  # Cookie加密签名密钥
    'cookie_name': 'elaina_admin_session',      # 管理员会话Cookie名称
    'cookie_expires_days': 7,                   # Cookie有效期天数
    'production_mode': True,                    # 生产环境模式，影响错误信息显示
    'secure_headers': True                      # 是否启用安全响应头防护
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
    'pool_size': 15,                           # 连接池最大连接数
    'min_pool_size': 3,                        # 连接池最小保持连接数
    'connect_timeout': 5,                      # 数据库连接超时时间(秒)
    'read_timeout': 7,                         # 数据读取超时时间(秒)
    'write_timeout': 7,                        # 数据写入超时时间(秒)
    'autocommit': True,                        # 是否自动提交事务
    
    # 连接池高级设置
    'connection_lifetime': 1200,               # 单个连接最大生命周期(秒，20分钟)
    'gc_interval': 60,                         # 连接池垃圾回收间隔(秒)
    'idle_timeout': 10,                        # 空闲连接超时时间(秒)
    'thread_pool_size': 10,                    # 并发查询线程池大小
    'request_timeout': 3.0,                    # 获取连接请求超时时间(秒)
    'retry_count': 3,                          # 连接失败重试次数
    'retry_interval': 0.5,                     # 重试间隔时间(秒)
    
    # 监控和维护设置
    'max_usage_time': 30,                      # 单次连接最长使用时间(秒)
    'max_connection_hold_time': 20,            # 连接占用超时警告阈值(秒)
    'long_query_warning_time': 60,             # 长查询时间警告阈值(秒)
    'pool_status_interval': 180,               # 连接池状态记录间隔(秒，3分钟)
    'pool_maintenance_interval': 15,           # 连接池维护清理间隔(秒)
    
    # 兼容性设置
    'pool_name': 'elaina_pool',                # 连接池标识名称
    'use_pure': True,                          # 使用纯Python MySQL驱动
    'buffered': False                          # 是否缓存查询结果
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
    
    # 日志写入策略
    'insert_interval': 20,                     # 批量写入间隔时间(秒)，0表示立即写入
    'batch_size': 1000,                        # 每批次最大写入日志记录数
    'table_prefix': 'Mlog_',                   # 日志表名前缀，按日期自动分表
    
    # 日志保留策略
    'retention_days': 30,                      # 日志保留天数，0表示永久保留
    'auto_cleanup': True,                      # 是否自动清理过期日志表
    
    # 连接池配置(仅当use_main_db=False时生效)
    'pool_size': 5,                            # 日志库连接池大小
    'min_pool_size': 2,                        # 日志库最小连接数
    'connect_timeout': 3,                      # 连接超时时间(秒)
    'read_timeout': 10,                        # 读取超时时间(秒)
    'write_timeout': 10,                       # 写入超时时间(秒)
    'autocommit': True,                        # 自动提交事务
    
    # 错误处理配置
    'max_retry': 3,                            # 写入失败最大重试次数
    'retry_interval': 2                        # 重试间隔时间(秒)
}

