#!/usr/bin/env python
# -*- coding: utf-8 -*-

from core.plugin.PluginManager import Plugin
from function.db_pool import DatabaseService
import json
import logging
import time
import concurrent.futures
from functools import partial
import datetime
from config import LOG_DB_CONFIG
import traceback

# 导入日志数据库相关内容
try:
    from function.log_db import LogDatabasePool
except ImportError:
    LogDatabasePool = None

# 导入插件管理器
try:
    from core.plugin.PluginManager import PluginManager
except ImportError:
    PluginManager = None

# 设置日志
logger = logging.getLogger('user_stats')

class system_plugin(Plugin):
    # 设置插件优先级
    priority = 10
    
    # 不再需要自定义线程池，直接使用db_pool的线程池
    
    @staticmethod
    def get_regex_handlers():
        return {
            r'^用户统计$': {
                'handler': 'get_stats',
                'owner_only': True  # 仅限主人使用
            },
            r'^我的id$': {
                'handler': 'getid',
                'owner_only': False  # 所有人可用
            },
            r'^dau$': {
                'handler': 'get_dau',
                'owner_only': True  # 仅限主人使用
            },
            r'^dau(\d{4})$': {
                'handler': 'get_dau_with_date',
                'owner_only': True  # 仅限主人使用
            },
            r'^获取全部指令$': {
                'handler': 'admin_tools',
                'owner_only': True  # 仅限主人使用
            },
            r'^主人指令$': {
                'handler': 'owner_commands',
                'owner_only': True  # 仅限主人使用
            },
            r'^关于$': {
                'handler': 'about_info',
                'owner_only': False  # 所有人可用
            }
        }
    
    @staticmethod
    def getid(event):
        # 先拼接用户ID和群组ID（不脱敏，直接显示）
        info = f"<@{event.user_id}>\n"
        info += f"用户ID: {event.user_id}\n"
        info += f"群组ID: {event.group_id}\n"
        # 查询权限
        perm_str = ""
        try:
            import requests
            api_url = 'https://api.elaina.vin/tk/jrcnl/mysql.php'
            resp = requests.get(api_url, timeout=5)
            data = resp.json()
            user_id_str = str(event.user_id)
            found = None
            for item in data:
                if item.get('openid') == user_id_str or item.get('qq') == user_id_str:
                    found = item
                    break
            if found:
                perm_str = f"用户权限：{found.get('reason', '特殊权限用户')}"
            else:
                perm_str = "用户权限：普通用户"
        except Exception as e:
            perm_str = "用户权限：查询失败"
        # 统一输出
        info += perm_str + "\n"
        event.reply(info)
    
    @classmethod
    def owner_commands(cls, event):
        """显示所有主人可用指令的按钮"""
        # 如果无法导入PluginManager，则返回错误
        if PluginManager is None:
            event.reply("无法加载插件管理器，请检查系统配置")
            return
            
        try:
            # 创建插件管理器实例并加载所有插件
            plugin_manager = PluginManager()
            plugin_manager.load_plugins()
            
            # 获取所有已加载的插件及其优先级
            plugins = list(plugin_manager._plugins.keys())
            
            # 收集所有主人专属命令及其长度信息
            commands_info = []
            
            # 定义正则表达式特殊字符
            regex_special_chars = ['(', ')']
            
            for plugin in plugins:
                handlers = plugin.get_regex_handlers()
                
                if handlers:
                    for pattern, handler_info in handlers.items():
                        if isinstance(handler_info, dict) and handler_info.get('owner_only', True):
                            # 去除正则表达式特殊字符，提取纯文本命令
                            clean_command = pattern.replace('^', '').replace('$', '')
                            
                            # 检查命令是否需要设置enter为False
                            should_enter = True
                            
                            # 检查是否以dm或jx开头，或包含+号
                            if '+' in clean_command or clean_command.startswith('dm') or clean_command.startswith('jx'):
                                should_enter = False
                            
                            # 检查是否包含其他正则表达式特殊字符
                            if any(char in clean_command for char in regex_special_chars):
                                should_enter = False
                            
                            # 检查其他常见模式，如数字+文字的组合形式
                            if any(c.isdigit() for c in clean_command) and any(c.isalpha() for c in clean_command):
                                should_enter = False
                            
                            # 只添加有意义的命令作为按钮
                            if clean_command and len(clean_command) <= 10:
                                commands_info.append({
                                    'command': clean_command,
                                    'length': len(clean_command),
                                    'enter': should_enter
                                })
            
            # 按长度排序
            commands_info.sort(key=lambda x: x['length'])
            
            # 按钮最多使用5x5布局（最多25个按钮）
            if len(commands_info) > 25:
                commands_info = commands_info[:25]
            
            # 智能分组构建按钮行
            rows = []
            current_row = []
            row_button_count = 0
            
            # 长命令(6个字符及以上) - 每行2个按钮
            long_commands = [cmd for cmd in commands_info if cmd['length'] > 5]
            # 中等长度命令(4-5个字符) - 每行3个按钮
            medium_commands = [cmd for cmd in commands_info if 3 < cmd['length'] <= 5]
            # 短命令(1-3个字符) - 每行4个按钮
            short_commands = [cmd for cmd in commands_info if cmd['length'] <= 3]
            
            # 处理短命令 - 每行4个
            while short_commands and len(rows) < 5:
                row_commands = short_commands[:4]
                short_commands = short_commands[4:]
                
                row_buttons = []
                for cmd in row_commands:
                    # 确保长度不超过6个字符
                    display_text = cmd['command'][:6]
                    row_buttons.append({
                        'text': display_text,
                        'data': cmd['command'],
                        'enter': cmd['enter'],
                        'style': 1
                    })
                
                if row_buttons:
                    rows.append(event.rows(row_buttons))
            
            # 处理中等长度命令 - 每行3个
            while medium_commands and len(rows) < 5:
                row_commands = medium_commands[:3]
                medium_commands = medium_commands[3:]
                
                row_buttons = []
                for cmd in row_commands:
                    # 确保长度不超过6个字符
                    display_text = cmd['command'][:6]
                    row_buttons.append({
                        'text': display_text,
                        'data': cmd['command'],
                        'enter': cmd['enter'],
                        'style': 1
                    })
                
                if row_buttons:
                    rows.append(event.rows(row_buttons))
            
            # 处理长命令 - 每行2个
            while long_commands and len(rows) < 5:
                row_commands = long_commands[:2]
                long_commands = long_commands[2:]
                
                row_buttons = []
                for cmd in row_commands:
                    # 确保长度不超过6个字符
                    display_text = cmd['command'][:6]
                    row_buttons.append({
                        'text': display_text,
                        'data': cmd['command'],
                        'enter': cmd['enter'],
                        'style': 1
                    })
                
                if row_buttons:
                    rows.append(event.rows(row_buttons))
            
            # 添加获取全部指令按钮到最后一行
            if len(rows) < 5:
                rows.append(event.rows([
                    {
                        'text': '获取全部指令',
                        'data': '获取全部指令',
                        'type': 1,
                        'style': 1
                    }
                ]))
            
            # 创建按钮组
            buttons = event.button(rows)
            
            # 发送带按钮的消息
            event.reply(f"<@{event.user_id}>\n👑 主人专属指令快捷按钮", buttons)
            
        except Exception as e:
            logger.error(f'获取主人指令失败: {e}')
            event.reply(f'主人指令功能暂时不可用，错误信息: {str(e)}')
    
    @classmethod
    def admin_tools(cls, event):
        """管理工具，显示所有可用指令和统计数据"""
        # 如果无法导入PluginManager，则返回错误
        if PluginManager is None:
            event.reply("无法加载插件管理器，请检查系统配置")
            return
            
        try:
            # 创建插件管理器实例并加载所有插件
            plugin_manager = PluginManager()
            plugin_manager.load_plugins()
            
            # 获取所有已加载的插件及其优先级 - 使用_plugins字典
            plugins = list(plugin_manager._plugins.keys())
            
            # 构建头部信息
            header = [
                f'<@{event.user_id}>',
                f'📋 所有可用指令列表',
                f'总插件数: {len(plugins)}个'
            ]
            
            # 构建代码框内容
            code_content = []
            
            # 遍历所有插件并提取命令
            total_commands = 0
            
            for plugin in plugins:
                plugin_name = plugin.__name__
                priority = plugin_manager._plugins[plugin]  # 从_plugins字典获取优先级
                handlers = plugin.get_regex_handlers()
                
                if handlers:
                    code_content.append(f'🔧 插件: {plugin_name} (优先级: {priority})')
                    
                    # 所有命令统一显示，不再区分权限
                    commands = []
                    
                    for pattern, handler_info in handlers.items():
                        total_commands += 1
                        # 根据是否是主人命令添加不同的emoji
                        if isinstance(handler_info, dict) and handler_info.get('owner_only', False):
                            emoji = "👑"  # 主人命令
                        else:
                            emoji = "🔹"  # 普通命令
                        
                        # 删除正则表达式的^和$符号
                        clean_pattern = pattern.replace('^', '').replace('$', '')
                        commands.append(f"  {emoji} {clean_pattern}")
                    
                    # 只有命令非空的插件才添加到输出中
                    if commands:
                        code_content.extend(sorted(commands))
                        code_content.append('-' * 30)
            
            # 命令总结
            code_content.append(f'总命令数: {total_commands}个')
            
            # 创建最终消息内容 - 使用代码框包裹
            message = '\n'.join(header) + "\n```\n" + '\n'.join(code_content) + "\n```"
            
            # 创建按钮
            buttons = event.button([
                event.rows([
                    {
                        'text': '查看DAU',
                        'data': 'dau',
                        'type': 1,
                        'style': 1,
                        'enter': False
                    },
                    {
                        'text': '主人指令',
                        'data': '主人指令',
                        'type': 1,
                        'style': 1,
                        'enter': False
                    }
                ])
            ])
            
            # 发送带按钮的消息
            event.reply(message, buttons)
            
        except Exception as e:
            logger.error(f'管理工具执行失败: {e}')
            event.reply(f'管理工具暂时不可用，错误信息: {str(e)}')
    
    @classmethod
    def get_dau_with_date(cls, event):
        """处理特定日期的DAU查询，格式为MMDD"""
        # 从正则匹配中获取日期参数（MMDD格式）
        date_str = event.matches[0] if event.matches else None
        
        if not date_str or len(date_str) != 4:
            event.reply("日期格式错误，请使用MMDD格式，例如：dau0522表示5月22日")
            return
            
        # 构建完整日期（假设是当前年份）
        current_year = datetime.datetime.now().year
        try:
            month = int(date_str[:2])
            day = int(date_str[2:])
            # 验证日期是否有效
            query_date = datetime.datetime(current_year, month, day)
            
            # 如果生成的日期在未来，可能是去年的日期
            if query_date > datetime.datetime.now():
                query_date = datetime.datetime(current_year - 1, month, day)
                
            # 将日期格式化为YYYYMMDD格式
            formatted_date = query_date.strftime('%Y%m%d')
            # 调用通用DAU查询方法
            cls._get_dau_data(event, formatted_date)
        except ValueError:
            event.reply(f"无效的日期: {date_str}，请使用有效的月份和日期")
            return
    
    @classmethod
    def get_dau(cls, event):
        """获取当日活跃用户统计信息"""
        # 获取今天的日期格式化为YYYYMMDD
        today = datetime.datetime.now().strftime('%Y%m%d')
        # 调用通用DAU查询方法
        cls._get_dau_data(event, today)
    
    @classmethod
    def _get_dau_data(cls, event, date_str):
        """获取特定日期的DAU统计数据的通用方法
        
        Args:
            event: 消息事件
            date_str: 日期字符串，格式为YYYYMMDD
        """
        start_time = time.time()
        
        # 如果日志数据库功能未启用，则返回提示
        if not LOG_DB_CONFIG.get('enabled', False):
            event.reply("日志数据库未启用，无法获取DAU统计")
            return
            
        # 如果无法导入LogDatabasePool，则使用普通数据库
        if LogDatabasePool is None:
            event.reply("无法访问日志数据库，请检查配置")
            return
        
        try:
            # 使用日志数据库连接池
            log_db_pool = LogDatabasePool()
            connection = log_db_pool.get_connection()
            
            if not connection:
                event.reply("无法连接到日志数据库，请稍后再试")
                return
            
            cursor = None
            
            try:
                cursor = connection.cursor()
                
                # 构建消息表名
                table_name = f"Mlog_{date_str}_message"
                
                # 检查消息表是否存在
                check_query = f"""
                    SELECT COUNT(*) as count 
                    FROM information_schema.tables 
                    WHERE table_schema = DATABASE() 
                    AND table_name = %s
                """
                
                cursor.execute(check_query, (table_name,))
                result = cursor.fetchone()
                if not result or result['count'] == 0:
                    # 将YYYYMMDD格式转换为更易读的格式
                    display_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
                    event.reply(f"该日期({display_date})无消息记录")
                    return
                
                # 查询总消息数
                total_messages_query = f"SELECT COUNT(*) as count FROM {table_name}"
                cursor.execute(total_messages_query)
                total_messages_result = cursor.fetchone()
                total_messages = total_messages_result['count'] if total_messages_result else 0
                
                # 查询不同用户数量（去重）
                unique_users_query = f"SELECT COUNT(DISTINCT user_id) as count FROM {table_name} WHERE user_id IS NOT NULL AND user_id != ''"
                cursor.execute(unique_users_query)
                unique_users_result = cursor.fetchone()
                unique_users = unique_users_result['count'] if unique_users_result else 0
                
                # 查询不同群组数量（去重）- 不包括私聊
                unique_groups_query = f"SELECT COUNT(DISTINCT group_id) as count FROM {table_name} WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''"
                cursor.execute(unique_groups_query)
                unique_groups_result = cursor.fetchone()
                unique_groups = unique_groups_result['count'] if unique_groups_result else 0
                
                # 查询私聊消息数量
                private_messages_query = f"SELECT COUNT(*) as count FROM {table_name} WHERE group_id = 'c2c'"
                cursor.execute(private_messages_query)
                private_messages_result = cursor.fetchone()
                private_messages = private_messages_result['count'] if private_messages_result else 0
                
                # 获取最活跃的5个群组
                active_groups_query = f"""
                    SELECT group_id, COUNT(*) as msg_count 
                    FROM {table_name} 
                    WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''
                    GROUP BY group_id 
                    ORDER BY msg_count DESC 
                    LIMIT 5
                """
                cursor.execute(active_groups_query)
                active_groups_result = cursor.fetchall()
                
                # 获取最活跃的5个用户
                active_users_query = f"""
                    SELECT user_id, COUNT(*) as msg_count 
                    FROM {table_name} 
                    WHERE user_id IS NOT NULL AND user_id != ''
                    GROUP BY user_id 
                    ORDER BY msg_count DESC 
                    LIMIT 5
                """
                cursor.execute(active_users_query)
                active_users_result = cursor.fetchall()
                
                # 按小时统计消息数量
                hourly_stats_query = f"""
                    SELECT HOUR(timestamp) as hour, COUNT(*) as count 
                    FROM {table_name} 
                    GROUP BY HOUR(timestamp) 
                    ORDER BY hour
                """
                cursor.execute(hourly_stats_query)
                hourly_stats_result = cursor.fetchall()
                
                # 计算每小时的消息数
                hours_data = {i: 0 for i in range(24)}
                if hourly_stats_result:
                    for row in hourly_stats_result:
                        hour = row['hour']
                        count = row['count']
                        hours_data[hour] = count
                
                # 查找最活跃的小时
                most_active_hour = max(hours_data.items(), key=lambda x: x[1]) if hours_data else (0, 0)
                
                # 将YYYYMMDD格式转换为更易读的格式
                display_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
                
                # 构建响应信息
                info = [
                    f'<@{event.user_id}>',
                    f'📊 {display_date} 活跃统计',
                    f'👤 活跃用户数: {unique_users}',
                    f'👥 活跃群聊数: {unique_groups}',
                    f'💬 消息总数: {total_messages}',
                    f'📱 私聊消息: {private_messages}',
                    f'⏰ 最活跃时段: {most_active_hour[0]}点 ({most_active_hour[1]})'
                ]
                
                # 添加最活跃群组信息
                if active_groups_result:
                    info.append('🔝 最活跃群组:')
                    idx = 1
                    for group in active_groups_result:
                        group_id = group['group_id']
                        if not group_id:
                            continue  # 跳过空/None
                        if group_id and len(group_id) > 6:
                            masked_group_id = group_id[:3] + "****" + group_id[-3:]
                        else:
                            masked_group_id = group_id
                        info.append(f"  {idx}. {masked_group_id} ({group['msg_count']}条)")
                        idx += 1
                
                # 添加最活跃用户信息
                if active_users_result:
                    info.append('👑 最活跃用户:')
                    idx = 1
                    for user in active_users_result:
                        user_id = user['user_id']
                        if not user_id:
                            continue  # 跳过空/None
                        if user_id and len(user_id) > 6:
                            masked_user_id = user_id[:3] + "****" + user_id[-3:]
                        else:
                            masked_user_id = user_id
                        info.append(f"  {idx}. {masked_user_id} ({user['msg_count']}条)")
                        idx += 1
                
                # 计算查询耗时
                query_time = round((time.time() - start_time) * 1000)
                info.append(f'🕒 查询耗时: {query_time}ms')
                
                # 创建按钮 - 添加用户统计按钮和前一天查询按钮
                # 计算前一天的日期
                query_datetime = datetime.datetime.strptime(date_str, '%Y%m%d')
                prev_day = (query_datetime - datetime.timedelta(days=1)).strftime('%m%d')
                
                buttons = event.button([
                    event.rows([
                        {
                            'text': f'查询dau',
                            'data': f'dau',
                            'type': 2,
                            'style': 1,
                            'enter': False
                        },
                        {
                            'text': '今日DAU',
                            'data': 'dau',
                            'type': 1,
                            'style': 1,
                            'enter': True
                        }
                    ]),
                    event.rows([
                        {
                            'text': '用户统计',
                            'data': '用户统计',
                            'type': 1,
                            'style': 1,
                            'enter': True
                        }
                    ])
                ])
                
                # 发送带按钮的消息
                event.reply('\n'.join(info), buttons)
                
            finally:
                # 确保关闭游标和释放连接
                if cursor:
                    cursor.close()
                if connection:
                    log_db_pool.release_connection(connection)
            
        except Exception as e:
            logger.error(f'获取DAU统计信息失败: {e}')
            event.reply(f'DAU统计服务暂时不可用，错误信息: {str(e)}')
    
    @classmethod
    def _get_query_params(cls):
        """获取所有查询参数"""
        return [
            # 基础查询
            ("SELECT COUNT(*) as count FROM M_users", None, False),  # 用户数量
            ("SELECT COUNT(*) as count FROM M_groups", None, False),  # 群组数量
            ("SELECT COUNT(*) as count FROM M_members", None, False),  # 私聊用户数量
            # 最活跃群组查询
            ("""
                SELECT group_id, JSON_LENGTH(users) as member_count
                FROM M_groups_users
                ORDER BY member_count DESC
                LIMIT 1
            """, None, False)
        ]
    
    @classmethod
    def _get_group_info_params(cls, group_id):
        """获取指定群组的查询参数"""
        return [
            # 群成员数量
            ("SELECT users FROM M_groups_users WHERE group_id = %s", (group_id,), False),
            # 获取所有群组数据，用于计算排名
            ("""
                SELECT group_id, JSON_LENGTH(users) as member_count
                FROM M_groups_users
                ORDER BY member_count DESC
            """, None, True)
        ]
    
    @classmethod
    def _process_result(cls, results):
        """处理查询结果"""
        user_count = results[0]['count'] if results[0] else 0
        group_count = results[1]['count'] if results[1] else 0
        private_users_count = results[2]['count'] if results[2] else 0
        
        # 处理最活跃群组数据
        most_active_result = results[3]
        if most_active_result:
            group_id = most_active_result.get('group_id', "无数据")
            # 隐藏群ID的中间部分 - 使用XXXX隐藏中间部分
            if group_id != "无数据" and len(group_id) > 6:
                group_id = group_id[:2] + "****" + group_id[-2:]
            
            most_active_group = {
                'group_id': group_id,
                'member_count': most_active_result.get('member_count', 0)
            }
        else:
            most_active_group = {'group_id': "无数据", 'member_count': 0}
        
        return {
            'user_count': user_count,
            'group_count': group_count,
            'private_users_count': private_users_count,
            'most_active_group': most_active_group
        }
    
    @classmethod
    def _process_group_results(cls, results, group_id):
        """处理群组相关查询结果"""
        # 解析群成员数据
        group_members = 0
        if results[0] and results[0].get('users'):
            users = results[0]['users']
            if isinstance(users, str):
                users = json.loads(users)
            group_members = len(users)
        
        # 计算群排名
        group_rank = 'N/A'
        if results[1]:
            for i, group in enumerate(results[1], 1):
                if group.get('group_id') == group_id:
                    group_rank = i
                    break
        
        return {
            'member_count': group_members,
            'rank': group_rank
        }
    
    @classmethod
    def get_stats(cls, event):
        """获取统计信息 - 使用db_pool的并发查询"""
        start_time = time.time()
        
        try:
            db = DatabaseService()
            
            # 准备所有查询
            query_params = cls._get_query_params()
            
            # 如果在群聊中，添加群组相关查询
            group_results = None
            if event.group_id:
                group_query_params = cls._get_group_info_params(event.group_id)
                
                # 执行群组查询
                group_results = db.execute_concurrent_queries(group_query_params)
            
            # 执行基础查询
            results = db.execute_concurrent_queries(query_params)
            
            # 处理查询结果
            stats = cls._process_result(results)
            
            # 构建详细统计信息
            info = [
                f'<@{event.user_id}>',
                f'📊 统计信息',
            ]
            
            # 如果在群聊中，首先添加当前群成员信息
            if event.group_id and group_results:
                group_info = cls._process_group_results(group_results, event.group_id)
                info.append(f'👥 当前群成员: {group_info["member_count"]}')
            
            # 按照指定顺序添加统计信息
            info.append(f'👤 好友总数量: {stats["private_users_count"]}')
            info.append(f'👥 群组总数量: {stats["group_count"]}')
            info.append(f'👥 所有用户总数量: {stats["user_count"]}')
            info.append(f'🔝 最大群: {stats["most_active_group"]["group_id"]} (群员: {stats["most_active_group"]["member_count"]})')
            
            # 如果在群聊中，添加当前群的排名信息
            if event.group_id and group_results:
                group_info = cls._process_group_results(group_results, event.group_id)
                info.append(f'📈 当前群排名: 第{group_info["rank"]}名')
            
            # 统计查询时间
            query_time = round((time.time() - start_time) * 1000)
            info.append(f'🕒 查询耗时: {query_time}ms')
            
            # 创建按钮 - 添加DAU查询按钮
            buttons = event.button([
                event.rows([
                    {
                        'text': 'DAU查询',
                        'data': 'dau',
                        'type': 1,
                        'style': 1,
                        'enter': True
                    }
                ])
            ])
            
            # 发送带按钮的消息
            event.reply('\n'.join(info), buttons)
            
        except Exception as e:
            logger.error(f'获取统计信息失败: {e}')
            event.reply(f'统计服务暂时不可用，错误信息: {str(e)}')
    
    @staticmethod
    def about_info(event):
        """关于界面，展示内核、版本、作者等信息（不使用代码框，每行前加表情）"""
        # 导入PluginManager获取插件和功能数量
        try:
            from core.plugin.PluginManager import PluginManager
            
            # 创建插件管理器实例并加载所有插件
            plugin_manager = PluginManager()
            plugin_manager.load_plugins()
            
            # 获取内核数（已加载的插件数）
            kernel_count = len(plugin_manager._plugins)
            
            # 获取功能数（已注册的处理器数）
            function_count = len(plugin_manager._regex_handlers)
        except Exception as e:
            # 如果获取失败，使用默认值
            kernel_count = "获取失败"
            function_count = "获取失败"
            add_error_log(f"获取插件信息失败: {str(e)}", traceback.format_exc())
            
        # 获取Python版本
        import platform
        python_version = platform.python_version()
            
        # 添加用户@并用markdown横线分隔
        msg = (
            f'<@{event.user_id}>关于伊蕾娜\n___\n'
            '🔗 连接方式: WebHook\n'
            '🧬 内核版本：Elaina 1.2.3\n'
            '🏰 连接Bot框架: Elaina-Mbot\n'
            f'🐍 Python版本: {python_version}\n'
            f'🧩 已加载内核数: {kernel_count}\n'
            f'🛠️ 已加载处理器数: {function_count}\n'
            '\n\n>Tip:只有艾特伊蕾娜，伊蕾娜才能接收到你的消息~！'
        )
        btn = event.button([
            event.rows([
               {
                'text': '菜单',
                'data': '/菜单',
                'enter': True,
                'style': 1
            }, {
                'text': '娱乐菜单',
                'data': '/娱乐菜单',
                'enter': True,
                'style': 1
            }
            ])
        ])
        event.reply(msg,btn) 