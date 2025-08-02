# ElainaBot

基于Python的QQ机器人框架，支持WS WH链接方式，支持插件系统、Web面板监控。

## 核心特性

- 🖼 **多图床支持**：集成QQ官方和QQShare两种图片上传方案
- 💾 **数据持久化**：MySQL数据库支持，记录用户和群组信息
- 🔌 **插件系统**：支持插件热加载，无需重启
- 📊 **Web控制面板**：实时监控、日志查看、插件管理
- ⚡ **高性能**：内置连接池和内存优化
- 🔒 **权限控制**：主人命令、群组权限管理
- 💾 **数据持久化**：MySQL数据库支持

## 快速部署

### 1. 环境准备
- Python 3.8+
- MySQL 5.7+（可选）

### 2. 基础配置
编辑 `config.py`：
```python

记得创建1-2个数据库
用于数据库处理和日志处理，可用相同的
记得将配置文件都补充完整
# 机器人配置
appid = "你的机器人APPID"
secret = "你的机器人SECRET"


# Web面板访问配置
WEB_SECURITY = {
    'access_token': '你的访问令牌',  # 用于URL验证
    'admin_password': '你的管理密码'  # 用于登录验证
}

```

### 3. 启动机器人
```bash
python main.py
```

### 4. 访问Web面板
浏览器访问：`http://你的IP:5005/web/?token=你的访问令牌`

## 插件开发

创建插件文件 `plugins/your_plugin/example.py`：
```python
class ExamplePlugin:
    @staticmethod
    def get_regex_handlers():
        return {
            r'^/hello': ExamplePlugin.hello
        }
    
    @staticmethod
    def hello(event):
        return "Hello, World!"
```

## 配置说明

### WebSocket连接（可选）
```python
WEBSOCKET_CONFIG = {
    'enabled': True,        # 启用WebSocket
    'auto_connect': True,   # 自动连接
}
```

### 数据库配置（可选）
```python
DB_CONFIG = {
    'host': 'localhost',
    'port': 3306,
    'user': 'root',
    'password': '密码',
    'database': '数据库名'
}
```

## 目录结构
```
MBot/
├── config.py          # 配置文件
├── main.py            # 启动文件
├── plugins/           # 插件目录
├── web_panel/         # Web面板
└── function/          # 工具函数
```

## 注意事项

1. 首次运行会自动创建必要的数据表
2. 插件放入 `plugins/` 对应目录即可自动加载
3. Web面板提供实时日志查看和系统监控
4. 支持HTTP和WebSocket两种接收消息方式

---

更多详细配置请查看 `config.py` 文件注释。
