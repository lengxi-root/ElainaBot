# MBot

MBot机器人框架，基于Python实现，支持插件热更新、内存优化、Web面板监控等特性。

## 特性

- ✨ **插件化架构**：动态加载与卸载插件，支持热更新
- 🚀 **高性能**：内置连接池和内存优化，提升运行效率
- 📊 **Web控制面板**：实时监控系统状态、内存使用和日志
- 🔒 **多级权限系统**：主人命令、管理员权限细粒度控制
- 🖼 **多图床支持**：集成QQ官方和QQShare两种图片上传方案
- 💾 **数据持久化**：MySQL数据库支持，记录用户和群组信息
- 🔄 **垃圾回收机制**：优化内存使用，提升长期稳定性

## 安装

### 环境要求

- Python 3.8+
- MySQL 5.7+

### 安装步骤

1. 克隆代码库

```bash
git clone https://github.com/lengxi-root/MBot-Framework.git
```

2. 安装依赖包

```bash
pip install -r requirements.txt
```

3. 配置机器人

编辑`config.py`文件，填写QQ机器人的appid和secret等信息：

```python
# 机器人配置
appid = "你的APPID"
secret = "你的SECRET" 

# 主人QQ号
OWNER_IDS = ["你的QQ号的MD5值"]

# 图床配置
IMAGE_BED = {
    'qq_bot': {
        'channel_id': '你的频道ID',
    },
    'qq_share': {
        'p_uin': '你的QQ号',
        'p_skey': '你的p_skey值'
    }
}

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

## 快速开始

### 访问Web控制面板

启动MBot后，访问以下地址打开Web控制面板：

```
http://你的服务器IP:端口/
```

通过Web面板可以监控机器人状态、查看日志、管理插件等。

## 框架结构

```
Mbot/
├── config.py             # 全局配置文件
├── core/                 # 核心功能
│   ├── event/            # 事件处理
│   └── plugin/           # 插件管理
├── function/             # 工具函数
├── plugins/              # 插件目录
│   ├── example/          # 热更新插件
│   ├── system/           # 系统插件
│   └── [其他插件]/        # 标准插件
├── web_panel/            # Web控制面板
└── main.py               # 主程序入口
```

## 内存管理

MBot内置了垃圾回收机制，定期清理不再使用的对象，可通过Web面板监控内存使用情况：

- 总体内存使用率
- 各组件内存占用情况
- 大型内存对象追踪
- 手动触发内存回收

## 图床配置

MBot支持两种图床，可在`config.py`中配置：

1. **QQ官方图床**：需要配置`channel_id`
2. **QQShare图床**：需要配置QQ号和p_skey

获取p_skey的方法：
1. 使用PC浏览器登录connect.qq.com
2. 打开开发者工具(F12)
3. 在网络或应用标签页下查找Cookie中的p_skey值
