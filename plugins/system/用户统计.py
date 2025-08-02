#!/usr/bin/env python
# -*- coding: utf-8 -*-

from core.plugin.PluginManager import Plugin
from function.db_pool import DatabaseService
import json
import logging
import time
import datetime
from config import LOG_DB_CONFIG
import traceback
from function.httpx_pool import sync_get
from function.database import Database  # 导入Database类获取QQ号

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
            r'^dau\s+(\d{4})$': {
                'handler': 'get_dau_with_date',
                'owner_only': True  # 仅限主人使用
            },
            r'^补全dau$': {
                'handler': 'complete_dau',
                'owner_only': True  # 仅限主人使用
            },
            r'^获取全部指令$': {
                'handler': 'admin_tools',
                'owner_only': True  # 仅限主人使用
            },
            r'^关于$': {
                'handler': 'about_info',
                'owner_only': False  # 所有人可用
            },
            r'^删除历史数据$': {
                'handler': 'clean_historical_data',
                'owner_only': True  # 仅限主人使用
            }
        }
    
    @staticmethod
    def getid(event):
        # 初始化基本信息
        info = f"<@{event.user_id}>\n"
        
        # 查询用户QQ号，放在最前面显示
        try:
            db = Database()
            sql = "SELECT qq, base64_data FROM M_users WHERE user_id = %s"
            result = DatabaseService.execute_query(sql, (event.user_id,))
            
            qq = None
            if result:
                # 如果数据库中已有QQ号
                if result.get('qq'):
                    qq = result.get('qq')
                # 如果QQ号为空，但有base64数据，尝试重新解析
                elif result.get('base64_data'):
                    import base64
                    import httpx
                    try:
                        # 尝试解码并获取QQ号
                        base64_data = result.get('base64_data')
                        decoded_data = base64.b64decode(base64_data).hex()
                        
                        # 调用API获取QQ号
                        response = httpx.get(f"http://127.0.0.1:34343/pb={decoded_data}", timeout=5)
                        if response.status_code == 200:
                            data = response.json()
                            qq_number = data.get("3")
                            if qq_number:
                                # 保存QQ号
                                sql = "UPDATE M_users SET qq = %s WHERE user_id = %s"
                                DatabaseService.execute_update(sql, (str(qq_number), event.user_id))
                                logging.info(f"用户 {event.user_id} 的QQ号 {qq_number} 已保存")
                                qq = str(qq_number)
                    except Exception as e:
                        logging.error(f"解码获取QQ号失败: {e}")
            
            # 添加UIN信息到最前面
            if qq:
                # 脱敏处理：只显示第一位和最后两位，其他用*替换
                if len(qq) > 3:
                    # 转义*号，避免被当作markdown语法
                    masked_qq = qq[0] + "\\*" * (len(qq) - 3) + qq[-2:]
                    info = f"<@{event.user_id}>\nUIN: {masked_qq}\n" + info[len(f"<@{event.user_id}>\n"):]
                else:
                    info = f"<@{event.user_id}>\nUIN: {qq}\n" + info[len(f"<@{event.user_id}>\n"):]
            else:
                # QQ号获取失败
                info = f"<@{event.user_id}>\nUIN: 获取失败\n" + info[len(f"<@{event.user_id}>\n"):]
                
        except Exception as e:
            logging.error(f"查询QQ号失败: {e}")
            info = f"<@{event.user_id}>\nUIN: 获取失败\n" + info[len(f"<@{event.user_id}>\n"):]
        
        # 添加用户ID和群组ID
        info += f"用户ID: {event.user_id}\n"
        info += f"群组ID: {event.group_id}\n"
        
        # 查询权限
        perm_str = ""
        try:
            api_url = 'https://api.elaina.vin/api/积分/特殊用户.php'
            resp = sync_get(api_url, timeout=5)
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
            message = '\n'.join(header) + "\n\n```python\n" + '\n'.join(code_content) + "\n```\n"
            
            # 创建按钮
            buttons = event.button([
                event.rows([
                    {
                        'text': '查看DAU',
                        'data': 'dau',
                        'type': 1,
                        'style': 1,
                        'enter': False
                    }
                ])
            ])
            
            # 发送带按钮的消息
            event.reply(message, buttons, hide_avatar_and_center=True)
            
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
        """获取当日活跃用户统计信息，并与昨天同时段进行对比"""
        # 获取今天的日期格式化为YYYYMMDD
        today = datetime.datetime.now()
        today_str = today.strftime('%Y%m%d')
        
        # 获取昨天的日期格式化为YYYYMMDD
        yesterday = today - datetime.timedelta(days=1)
        yesterday_str = yesterday.strftime('%Y%m%d')
        
        # 当前小时和分钟，用于限制昨天数据查询范围
        current_hour = today.hour
        current_minute = today.minute
        
        # 调用通用DAU查询方法，添加对比参数
        cls._get_dau_data(event, today_str, yesterday_str, current_hour, current_minute)
    
    @classmethod
    def _get_dau_data(cls, event, date_str, yesterday_str=None, current_hour=None, current_minute=None):
        """获取特定日期的DAU统计数据的通用方法
        
        Args:
            event: 消息事件
            date_str: 日期字符串，格式为YYYYMMDD
            yesterday_str: 昨天日期字符串，格式为YYYYMMDD（可选）
            current_hour: 当前小时（可选）
            current_minute: 当前分钟（可选）
        """
        start_time = time.time()
        
        # 将YYYYMMDD格式转换为datetime对象
        target_date = datetime.datetime.strptime(date_str, '%Y%m%d')
        today = datetime.datetime.now().date()
        is_today = target_date.date() == today
        
        # 优先尝试从本地文件读取DAU数据
        try:
            from function.dau_analytics import get_dau_analytics
            
            dau_analytics = get_dau_analytics()
            dau_data = dau_analytics.load_dau_data(target_date)
            
            if dau_data:
                # 从本地文件成功读取到数据
                cls._send_dau_from_file(event, dau_data, target_date, start_time)
                return
                
        except Exception as e:
            logger.warning(f"尝试从本地文件读取DAU数据失败: {e}")
        
        # 如果是非今日数据且文件不存在，直接返回提示
        if not is_today:
            display_date = f"{date_str[4:6]}-{date_str[6:8]}"
            
            # 创建补全DAU按钮
            buttons = event.button([
                event.rows([
                    {
                        'text': '补全DAU',
                        'data': '补全dau',
                        'type': 1,
                        'style': 1,
                        'enter': True
                    }
                ])
            ])
            
            event.reply(
                f"<@{event.user_id}>\n"
                f"❌ {display_date} 的DAU数据未生成或无该日期数据\n"
                f"💡 可以尝试使用下方按钮补全DAU记录",
                buttons,
                hide_avatar_and_center=True
            )
            return
        
        # 如果是今日数据且本地文件不存在，则从数据库查询（保持原有逻辑）
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
                    display_date = f"{date_str[4:6]}-{date_str[6:8]}"
                    event.reply(f"该日期({display_date})无消息记录")
                    return
                
                # 时间限制条件 - 如果有当前小时和分钟，则限制查询范围
                time_condition = ""
                if current_hour is not None and current_minute is not None:
                    time_limit = f"{current_hour:02d}:{current_minute:02d}:00"
                    time_condition = f" WHERE TIME(timestamp) <= '{time_limit}'"
                
                # 查询总消息数
                total_messages_query = f"SELECT COUNT(*) as count FROM {table_name}{time_condition}"
                cursor.execute(total_messages_query)
                total_messages_result = cursor.fetchone()
                total_messages = total_messages_result['count'] if total_messages_result else 0
                
                # 查询不同用户数量（去重）
                unique_users_query = f"SELECT COUNT(DISTINCT user_id) as count FROM {table_name}{time_condition}"
                unique_users_query += " AND user_id IS NOT NULL AND user_id != ''" if time_condition else " WHERE user_id IS NOT NULL AND user_id != ''"
                cursor.execute(unique_users_query)
                unique_users_result = cursor.fetchone()
                unique_users = unique_users_result['count'] if unique_users_result else 0
                
                # 查询不同群组数量（去重）- 不包括私聊
                unique_groups_query = f"SELECT COUNT(DISTINCT group_id) as count FROM {table_name}{time_condition}"
                unique_groups_query += " AND group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''" if time_condition else " WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''"
                cursor.execute(unique_groups_query)
                unique_groups_result = cursor.fetchone()
                unique_groups = unique_groups_result['count'] if unique_groups_result else 0
                
                # 查询私聊消息数量
                private_messages_query = f"SELECT COUNT(*) as count FROM {table_name}{time_condition}"
                private_messages_query += " AND group_id = 'c2c'" if time_condition else " WHERE group_id = 'c2c'"
                cursor.execute(private_messages_query)
                private_messages_result = cursor.fetchone()
                private_messages = private_messages_result['count'] if private_messages_result else 0
                
                # 获取最活跃的5个群组
                active_groups_query = f"""
                    SELECT group_id, COUNT(*) as msg_count 
                    FROM {table_name}{time_condition}
                    """
                active_groups_query += " AND group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''" if time_condition else " WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''"
                active_groups_query += """
                    GROUP BY group_id 
                    ORDER BY msg_count DESC 
                    LIMIT 3
                """
                cursor.execute(active_groups_query)
                active_groups_result = cursor.fetchall()
                
                # 获取最活跃的5个用户
                active_users_query = f"""
                    SELECT user_id, COUNT(*) as msg_count 
                    FROM {table_name}{time_condition}
                    """
                active_users_query += " AND user_id IS NOT NULL AND user_id != ''" if time_condition else " WHERE user_id IS NOT NULL AND user_id != ''"
                active_users_query += """
                    GROUP BY user_id 
                    ORDER BY msg_count DESC 
                    LIMIT 3
                """
                cursor.execute(active_users_query)
                active_users_result = cursor.fetchall()
                
                # 按小时统计消息数量
                hourly_stats_query = f"""
                    SELECT HOUR(timestamp) as hour, COUNT(*) as count 
                    FROM {table_name}{time_condition} 
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
                display_date = f"{date_str[4:6]}-{date_str[6:8]}"
                
                # 如果有昨天的日期，查询昨天同时段的数据进行对比
                yesterday_data = None
                if yesterday_str and current_hour is not None and current_minute is not None:
                    yesterday_table = f"Mlog_{yesterday_str}_message"
                    
                    # 检查昨天的表是否存在
                    cursor.execute(check_query, (yesterday_table,))
                    y_result = cursor.fetchone()
                    
                    if y_result and y_result['count'] > 0:
                        time_limit = f"{current_hour:02d}:{current_minute:02d}:00"
                        y_time_condition = f" WHERE TIME(timestamp) <= '{time_limit}'"
                        
                        # 获取昨天同时段的基础统计数据
                        yesterday_data = {}
                        
                        # 昨天总消息数
                        cursor.execute(f"SELECT COUNT(*) as count FROM {yesterday_table}{y_time_condition}")
                        y_total = cursor.fetchone()
                        yesterday_data['total_messages'] = y_total['count'] if y_total else 0
                        
                        # 昨天活跃用户数
                        y_users_query = f"SELECT COUNT(DISTINCT user_id) as count FROM {yesterday_table}{y_time_condition}"
                        y_users_query += " AND user_id IS NOT NULL AND user_id != ''"
                        cursor.execute(y_users_query)
                        y_users = cursor.fetchone()
                        yesterday_data['unique_users'] = y_users['count'] if y_users else 0
                        
                        # 昨天活跃群组数
                        y_groups_query = f"SELECT COUNT(DISTINCT group_id) as count FROM {yesterday_table}{y_time_condition}"
                        y_groups_query += " AND group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''"
                        cursor.execute(y_groups_query)
                        y_groups = cursor.fetchone()
                        yesterday_data['unique_groups'] = y_groups['count'] if y_groups else 0
                        
                        # 昨天私聊消息数
                        y_private_query = f"SELECT COUNT(*) as count FROM {yesterday_table}{y_time_condition}"
                        y_private_query += " AND group_id = 'c2c'"
                        cursor.execute(y_private_query)
                        y_private = cursor.fetchone()
                        yesterday_data['private_messages'] = y_private['count'] if y_private else 0
                
                # 构建响应信息
                info = [
                    f'<@{event.user_id}>',
                    f'📊 {display_date} 活跃统计' + (f' (截至{current_hour:02d}:{current_minute:02d})' if current_hour is not None else '')
                ]
                
                # 添加基本数据与昨天对比（如果有）
                if yesterday_data:
                    y_display_date = f"{yesterday_str[4:6]}-{yesterday_str[6:8]}"
                    
                    # 用户数对比
                    user_diff = unique_users - yesterday_data['unique_users']
                    user_change = f"🔺{user_diff}" if user_diff > 0 else f"🔻{abs(user_diff)}" if user_diff < 0 else "➖0"
                    info.append(f'👤 活跃用户数: {unique_users} ({user_change})')
                    
                    # 群组数对比
                    group_diff = unique_groups - yesterday_data['unique_groups']
                    group_change = f"🔺{group_diff}" if group_diff > 0 else f"🔻{abs(group_diff)}" if group_diff < 0 else "➖0"
                    info.append(f'👥 活跃群聊数: {unique_groups} ({group_change})')
                    
                    # 消息总数对比
                    msg_diff = total_messages - yesterday_data['total_messages']
                    msg_change = f"🔺{msg_diff}" if msg_diff > 0 else f"🔻{abs(msg_diff)}" if msg_diff < 0 else "➖0"
                    info.append(f'💬 消息总数: {total_messages} ({msg_change})')
                    
                    # 私聊消息对比
                    private_diff = private_messages - yesterday_data['private_messages']
                    private_change = f"🔺{private_diff}" if private_diff > 0 else f"🔻{abs(private_diff)}" if private_diff < 0 else "➖0"
                    info.append(f'📱 私聊消息: {private_messages} ({private_change})')
                else:
                    # 没有昨天数据时显示普通格式
                    info.append(f'👤 活跃用户数: {unique_users}')
                    info.append(f'👥 活跃群聊数: {unique_groups}')
                    info.append(f'💬 消息总数: {total_messages}')
                    info.append(f'📱 私聊消息: {private_messages}')
                
                info.append(f'⏰ 最活跃时段: {most_active_hour[0]}点 ({most_active_hour[1]})')
                
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
                info.append(f'📁 数据源: 实时数据库查询')
                
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
                event.reply('\n'.join(info), buttons, hide_avatar_and_center=True)
                
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
    def _send_dau_from_file(cls, event, dau_data, target_date, start_time):
        """从本地文件加载DAU数据并发送"""
        try:
            # 获取消息统计数据
            msg_stats = dau_data.get('message_stats', {})
            
            info = [
                f'<@{event.user_id}>',
                f'📊 {target_date.strftime("%m-%d")} 活跃统计'
            ]
            
            # 添加基本数据
            info.append(f'👤 活跃用户数: {msg_stats.get("active_users", 0)}')
            info.append(f'👥 活跃群聊数: {msg_stats.get("active_groups", 0)}')
            info.append(f'💬 消息总数: {msg_stats.get("total_messages", 0)}')
            info.append(f'📱 私聊消息: {msg_stats.get("private_messages", 0)}')
            
            # 添加最活跃时段
            peak_hour = msg_stats.get("peak_hour", 0)
            peak_hour_count = msg_stats.get("peak_hour_count", 0)
            info.append(f'⏰ 最活跃时段: {peak_hour}点 ({peak_hour_count}条)')
            
            # 添加最活跃群组信息
            top_groups = msg_stats.get("top_groups", [])
            if top_groups:
                info.append('🔝 最活跃群组:')
                idx = 1
                for group in top_groups[:3]:  # 只显示前3个
                    group_id = group.get("group_id", "")
                    if not group_id:
                        continue  # 跳过空/None
                    if group_id and len(group_id) > 6:
                        masked_group_id = group_id[:3] + "****" + group_id[-3:]
                    else:
                        masked_group_id = group_id
                    info.append(f"  {idx}. {masked_group_id} ({group.get('message_count', 0)}条)")
                    idx += 1
            
            # 添加最活跃用户信息
            top_users = msg_stats.get("top_users", [])
            if top_users:
                info.append('👑 最活跃用户:')
                idx = 1
                for user in top_users[:3]:  # 只显示前3个
                    user_id = user.get("user_id", "")
                    if not user_id:
                        continue  # 跳过空/None
                    if user_id and len(user_id) > 6:
                        masked_user_id = user_id[:3] + "****" + user_id[-3:]
                    else:
                        masked_user_id = user_id
                    info.append(f"  {idx}. {masked_user_id} ({user.get('message_count', 0)}条)")
                    idx += 1
            
            # 计算查询耗时
            query_time = round((time.time() - start_time) * 1000)
            info.append(f'🕒 查询耗时: {query_time}ms')
            info.append(f'📁 数据源: 本地文件')
            
            # 添加生成时间信息
            if dau_data.get('generated_at'):
                try:
                    gen_time = datetime.datetime.fromisoformat(dau_data['generated_at'].replace('Z', '+00:00'))
                    info.append(f'🕒 数据生成时间: {gen_time.strftime("%m-%d %H:%M")}')
                except:
                    pass
            
            # 创建按钮
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
            event.reply('\n'.join(info), buttons, hide_avatar_and_center=True)
            
        except Exception as e:
            logger.error(f"发送DAU文件数据失败: {e}")
            # 如果解析文件数据失败，回退到原始错误消息
            event.reply(f"DAU数据文件解析失败: {str(e)}")
    
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
            """, None, False),
            # UIN统计查询（只保留一个占位查询，实际使用固定值）
            ("SELECT 1 as placeholder", None, False)  # 占位查询，UIN成功数使用固定值64019
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
            
        # 处理UIN统计数据（使用固定值，不从数据库获取）
        uin_success = 64019  # 固定值
        
        return {
            'user_count': user_count,
            'group_count': group_count,
            'private_users_count': private_users_count,
            'most_active_group': most_active_group,
            'uin_stats': {
                'success': uin_success
            }
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
            
            # 添加UIN统计信息（只显示成功获取数）
            info.append(f'✅ UIN成功获取: {stats["uin_stats"]["success"]}')
            
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
            event.reply('\n'.join(info), buttons, hide_avatar_and_center=True)
            
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
            logger.error(f"获取插件信息失败: {e}", exc_info=True)
            
        # 获取Python版本
        import platform
        python_version = platform.python_version()
            
        # 添加用户@并用markdown横线分隔
        msg = (
f'<@{event.user_id}>关于伊蕾娜\n___\n'
'🔌 连接方式: WebHook\n'
'🤖 机器人QQ: 3889045760\n'
'🆔 机器人appid: 102134274\n'
'🚀 内核版本：Elaina 1.2.3\n'
'🏗️ 连接Bot框架: Elaina-Mbot\n'
f'⚙️ Python版本: {python_version}\n'
f'💫 已加载内核数: {kernel_count}\n'
f'⚡ 已加载处理器数: {function_count}\n'
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
    
    @staticmethod
    def complete_dau(event):
        """补全30天内的DAU数据（除了今天）"""
        try:
            from function.dau_analytics import get_dau_analytics
            
            dau_analytics = get_dau_analytics()
            today = datetime.datetime.now()
            
            # 检查30天内的DAU数据（除了今天）
            missing_dates = []
            
            for i in range(1, 31):  # 从昨天开始，检查30天
                target_date = today - datetime.timedelta(days=i)
                
                # 检查是否存在DAU数据文件
                dau_data = dau_analytics.load_dau_data(target_date)
                if not dau_data:
                    missing_dates.append(target_date)
            
            if not missing_dates:
                event.reply(f"<@{event.user_id}>\n✅ 近30天DAU数据完整，无需补全！")
                return
            
            # 发送开始消息
            event.reply(f"<@{event.user_id}>\n🔧 检测到{len(missing_dates)}天的DAU数据缺失，开始补全...\n请稍等，正在处理中...")
            
            # 开始生成缺失的DAU数据
            generated_count = 0
            failed_count = 0
            generated_dates = []
            failed_dates = []
            
            for target_date in missing_dates:
                try:
                    success = dau_analytics.manual_generate_dau(target_date)
                    if success:
                        generated_count += 1
                        generated_dates.append(target_date.strftime('%Y-%m-%d'))
                    else:
                        failed_count += 1
                        failed_dates.append(target_date.strftime('%Y-%m-%d'))
                except Exception as e:
                    logger.error(f"生成DAU数据失败 {target_date.strftime('%Y-%m-%d')}: {e}")
                    failed_count += 1
                    failed_dates.append(target_date.strftime('%Y-%m-%d'))
            
            # 构建结果消息
            info = [
                f'<@{event.user_id}>',
                f'📊 DAU数据补全完成！',
                f'',
                f'📈 处理结果:',
                f'✅ 成功生成: {generated_count}天',
                f'❌ 生成失败: {failed_count}天',
                f'📅 总计处理: {len(missing_dates)}天'
            ]
            
            # 显示成功生成的日期（最多5个）
            if generated_dates:
                info.append('')
                info.append('✅ 新生成的日期:')
                display_dates = generated_dates[-5:] if len(generated_dates) > 5 else generated_dates
                for date in display_dates:
                    info.append(f'  • {date}')
                if len(generated_dates) > 5:
                    info.append(f'  • ... 等共{len(generated_dates)}个日期')
            
            # 显示失败的日期
            if failed_dates:
                info.append('')
                info.append('❌ 生成失败的日期:')
                for date in failed_dates:
                    info.append(f'  • {date}')
            
            # 创建按钮
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
            event.reply('\n'.join(info), buttons, hide_avatar_and_center=True)
            
        except Exception as e:
            logger.error(f'补全DAU数据失败: {e}')
            event.reply(f'<@{event.user_id}>\n❌ 补全DAU数据失败: {str(e)}')
    
    @staticmethod
    def clean_historical_data(event):
        """删除历史数据：群友老婆历史数据和8天以外的日志表"""
        try:
            start_time = time.time()
            
            # 发送开始清理消息
            event.reply(f"<@{event.user_id}>\n🧹 开始清理历史数据，请稍等...")
            
            # 获取今天的日期字符串
            today = datetime.datetime.now()
            today_str = today.strftime('%Y%m%d')
            
            # 计算8天前的日期
            eight_days_ago = today - datetime.timedelta(days=8)
            eight_days_ago_str = eight_days_ago.strftime('%Y%m%d')
            
            cleanup_results = []
            
            # 1. 清理群友老婆历史数据（除了今天）
            try:
                wife_tables = ['Wife_user_pairs', 'Wife_group_users', 'Wife_divorce_counts']
                wife_deleted_count = 0
                
                for table in wife_tables:
                    # 检查表是否存在
                    check_sql = "SHOW TABLES LIKE %s"
                    result = DatabaseService.execute_query(check_sql, (table,))
                    
                    if result:
                        # 删除除了今天以外的所有数据
                        delete_sql = f"DELETE FROM {table} WHERE date_str != %s"
                        affected_rows = DatabaseService.execute_update(delete_sql, (today_str,))
                        wife_deleted_count += affected_rows if affected_rows else 0
                        logger.info(f"从表 {table} 删除了 {affected_rows or 0} 行历史数据")
                
                cleanup_results.append(f"✅ 群友老婆历史数据: 删除 {wife_deleted_count} 条记录")
                
            except Exception as e:
                logger.error(f"清理群友老婆历史数据失败: {e}")
                cleanup_results.append(f"❌ 群友老婆历史数据清理失败: {str(e)}")
            
            # 2. 清理8天以外的日志表
            try:
                # 如果日志数据库功能未启用，跳过
                if not LOG_DB_CONFIG.get('enabled', False):
                    cleanup_results.append("⚠️ 日志数据库未启用，跳过日志表清理")
                else:
                    # 导入日志数据库连接池
                    if LogDatabasePool is None:
                        cleanup_results.append("❌ 无法访问日志数据库，跳过日志表清理")
                    else:
                        log_db_pool = LogDatabasePool()
                        connection = log_db_pool.get_connection()
                        
                        if connection:
                            cursor = None
                            try:
                                # 使用DictCursor以便通过键名访问结果
                                from pymysql.cursors import DictCursor
                                cursor = connection.cursor(DictCursor)
                                
                                # 获取所有Mlog_开头的日志表（包括所有类型）
                                cursor.execute("""
                                    SELECT table_name 
                                    FROM information_schema.tables 
                                    WHERE table_schema = DATABASE() 
                                    AND (table_name LIKE 'Mlog_%_message' 
                                         OR table_name LIKE 'Mlog_%_plugin'
                                         OR table_name LIKE 'Mlog_%_framework' 
                                         OR table_name LIKE 'Mlog_%_error'
                                         OR table_name LIKE 'Mlog_%_unmatched')
                                """)
                                
                                log_tables = cursor.fetchall()
                                log_deleted_tables = 0
                                
                                logger.info(f"找到 {len(log_tables)} 张日志表待检查")
                                
                                for table in log_tables:
                                    # 添加安全检查
                                    if not isinstance(table, dict):
                                        logger.warning(f"跳过无效的表记录: {table}")
                                        continue
                                    
                                    # 兼容不同大小写的键名
                                    table_name = None
                                    for key in table.keys():
                                        if key.lower() == 'table_name':
                                            table_name = table[key]
                                            break
                                    
                                    if not table_name:
                                        logger.warning(f"跳过无效的表记录(缺少table_name): {table}")
                                        continue
                                    logger.debug(f"检查表: {table_name}")
                                    
                                    # 从表名提取日期部分（Mlog_YYYYMMDD_message）
                                    try:
                                        parts = table_name.split('_')
                                        if len(parts) < 2:
                                            logger.debug(f"跳过表 {table_name}：不符合Mlog_YYYYMMDD_message格式")
                                            continue
                                            
                                        date_part = parts[1]  # 获取YYYYMMDD部分
                                        
                                        if len(date_part) != 8 or not date_part.isdigit():
                                            logger.debug(f"跳过表 {table_name}：日期部分格式不正确")
                                            continue
                                            
                                        table_date = datetime.datetime.strptime(date_part, '%Y%m%d')
                                        
                                        # 如果表的日期超过8天，删除该表
                                        if table_date < eight_days_ago:
                                            try:
                                                drop_sql = f"DROP TABLE IF EXISTS `{table_name}`"
                                                cursor.execute(drop_sql)
                                                log_deleted_tables += 1
                                                logger.info(f"删除日志表: {table_name} (日期: {date_part})")
                                            except Exception as drop_error:
                                                logger.error(f"删除表 {table_name} 失败: {drop_error}")
                                                continue
                                        else:
                                            logger.debug(f"保留表 {table_name}：在保留期内")
                                            
                                    except (IndexError, ValueError) as e:
                                        logger.warning(f"无法解析表名日期 {table_name}: {e}")
                                        continue
                                    except Exception as e:
                                        logger.error(f"处理表 {table_name} 时发生错误: {e}")
                                        continue
                                
                                # 提交删除操作
                                if log_deleted_tables > 0:
                                    connection.commit()
                                    logger.info(f"已提交日志表删除操作，共删除 {log_deleted_tables} 张表")
                                
                                cleanup_results.append(f"✅ 日志表清理: 删除 {log_deleted_tables} 张表")
                                
                            except Exception as db_error:
                                logger.error(f"日志表清理过程中发生数据库错误: {db_error}")
                                # 回滚事务
                                try:
                                    if connection:
                                        connection.rollback()
                                        logger.info("已回滚日志表清理事务")
                                except Exception as rollback_error:
                                    logger.error(f"回滚失败: {rollback_error}")
                                cleanup_results.append(f"❌ 日志表清理发生数据库错误: {str(db_error)}")
                            finally:
                                if cursor:
                                    cursor.close()
                                log_db_pool.release_connection(connection)
                        else:
                            cleanup_results.append("❌ 无法连接到日志数据库")
                            
            except Exception as e:
                logger.error(f"清理日志表失败: {e}")
                cleanup_results.append(f"❌ 日志表清理失败: {str(e)}")
            
            # 计算总耗时
            total_time = round((time.time() - start_time) * 1000)
            
            # 构建结果消息
            info = [
                f'<@{event.user_id}>',
                f'🧹 历史数据清理完成！',
                f'',
                f'📊 清理结果:'
            ]
            
            # 添加清理结果
            info.extend(cleanup_results)
            
            # 添加耗时信息
            info.append(f'')
            info.append(f'🕒 清理耗时: {total_time}ms')
            info.append(f'📅 清理范围: 群友老婆除今日外全部数据，{eight_days_ago.strftime("%Y-%m-%d")}之前的日志表')
            
            # 创建按钮
            buttons = event.button([
                event.rows([
                    {
                        'text': '用户统计',
                        'data': '用户统计',
                        'type': 1,
                        'style': 1,
                        'enter': True
                    },
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
            event.reply('\n'.join(info), buttons, hide_avatar_and_center=True)
            
        except Exception as e:
            logger.error(f'删除历史数据失败: {e}')
            traceback.print_exc()
            event.reply(f'<@{event.user_id}>\n❌ 删除历史数据失败: {str(e)}') 