<div align="center">

# ElainaBot

ElainaBot 是一个基于 Python 的 QQ 官方机器人框架，支持WH WS连接，插件热更新、内存优化、Web面板监控等特性。
支持便捷发送Markdown,ark,语音等方法，无需过多构建RAW，适配普通消息与Markdown快捷转换，便捷导入markdown模板。

</div>

## ElainaBot 特性

- ✨ **插件化架构**：动态加载与卸载插件，热更新
- 🚀 **高性能优化**：内置连接池和内存优化，提升运行效率
- 📊 **Web控制面板**：实时监控系统状态、内存使用和日志
- 🖼 **多图床支持**：集成B站和QQ频道和cos桶三种图片上传方案
- 💾 **数据持久化**：MySQL数据库支持，完整的用户数据管理
- 🔄 **内存管理**：自动垃圾回收机制，优化长期稳定性

项目仅供学习交流使用，严禁用于任何商业用途和非法行为

## 📢 交流群

如果你在使用过程中遇到问题或有任何建议，欢迎加入我们的交流群：

**ElainaBot框架交流群：[631348711](https://qm.qq.com/q/qSErOcGf2o)**


## 安装教程

<details><summary>手动安装</summary>

> 环境准备：Windows/Linux/MacOS  
> [Python 3.8+](https://python.org), [MySQL 5.7+](https://mysql.com), [Git](https://git-scm.com)

1. Git Clone 项目

```bash
git clone https://github.com/lengxi-root/ElainaBot.git
cd ElainaBot
```

2. 安装依赖包

```bash
pip install -r requirements.txt
```

3. 配置机器人

编辑 `config.py` 文件，填写QQ机器人等配置信息：

```python
# 机器人配置
appid = "机器人APPID"
secret = "机器人SECRET" 



# 数据库配置
DB_CONFIG = {
    'host': 'localhost',
    'port': 3306,
    'user': '用户名',
    'password': '密码',
    'database': '数据库名',
    # ...其他配置
}
```

4. 运行机器人

```bash
python main.py
```
details>

## 使用教程

1. 启动机器人后，访问Web控制面板，可视化配置

```
http://localhost:端口/web/?token=自己设置的access_token
```

2. 通过Web面板可以：
   - 实时监控机器人状态
   - 监控内存使用情况
   等等

## 框架结构

```
ElainaBot/
├── config.py                    # 全局配置文件
├── main.py                      # 主程序入口
├── requirements.txt             # 项目依赖包
├── core/                        # 核心功能模块
│   ├── event/                   # 事件处理系统
│   │   ├── MessageEvent.py      # 消息事件处理
│   │   └── markdown_templates.py # Markdown模板定义
│   └── plugin/                  # 插件管理系统
│       ├── PluginManager.py     # 插件管理器
│       └── message_templates.py # 消息模板系统
├── function/                    # 工具函数库
│   ├── cos_uploader.py          # 腾讯云COS上传
│   ├── database.py              # 用户数据库操作
│   ├── dau_analytics.py         # DAU数据统计
│   ├── db_pool.py               # 数据库连接池
│   ├── httpx_pool.py            # HTTP连接池
│   ├── log_db.py                # 日志数据库
│    ws_client.py             # WebSocket客户端
├── plugins/                     # 插件目录
│   ├── example/                 # 示例插件
│   │   └── 测试插件.py          # 示例：测试开发插件
│   └── system/                  # 系统功能插件
│       ├── 用户统计.py          # 用户数据统计
│       └── 黑名单.py            # 黑名单管理
├── web/                         # Web控制面板
│   └── app.py                   # Flask应用主文件
```






