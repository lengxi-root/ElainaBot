#!/usr/bin/env python
# -*- coding: utf-8 -*-

from core.plugin.PluginManager import Plugin
from function.db_pool import DatabaseService
import json
import logging
import time
import datetime
from config import LOG_DB_CONFIG
from function.database import USERS_TABLE, GROUPS_TABLE, MEMBERS_TABLE, GROUPS_USERS_TABLE
import traceback
from function.httpx_pool import sync_get
from function.database import Database

import os
import sys
import subprocess
import platform

from function.log_db import LogDatabasePool
from core.plugin.PluginManager import PluginManager

logger = logging.getLogger('user_stats')

class system_plugin(Plugin):
    priority = 10
    _restart_status_checked = False
    
    @classmethod
    def _get_restart_status_file(cls):
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(plugin_dir, 'data')
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        return os.path.join(data_dir, 'restart_status.json')
    
    @classmethod
    def _check_restart_status(cls):
        restart_status_file = cls._get_restart_status_file()
        if not os.path.exists(restart_status_file):
            return
        
        with open(restart_status_file, 'r', encoding='utf-8') as f:
            restart_data = json.load(f)
        
        if restart_data.get('completed', True):
            return
            
        restart_time = restart_data.get('restart_time')
        message_id = restart_data.get('message_id')
        if not (restart_time and message_id):
            return
            
        start_time = datetime.datetime.fromisoformat(restart_time)
        duration_ms = int((datetime.datetime.now() - start_time).total_seconds() * 1000)
        
        cls._send_restart_complete_message(restart_data.get('user_id'), restart_data.get('group_id'), message_id, duration_ms)
        
        restart_data.update({'completed': True})
        with open(restart_status_file, 'w', encoding='utf-8') as f:
            json.dump(restart_data, f, ensure_ascii=False)
    
    @classmethod
    def _send_restart_complete_message(cls, user_id, group_id, message_id, duration_ms):
        from function.Access import BOTAPI, Json
        import random
        
        payload = {
            "msg_type": 0,
            "msg_seq": random.randint(10000, 999999),
            "content": f'✅ 重启完成！\n🕒 耗时: {duration_ms}ms',
            "msg_id": message_id
        }
        
        endpoint = f"/v2/groups/{group_id}/messages" if group_id != 'c2c' else f"/v2/users/{user_id}/messages"
        BOTAPI(endpoint, "POST", Json(payload))
    
    @staticmethod
    def mask_id(id_str, mask_char="*"):
        if not id_str or len(id_str) <= 6:
            return id_str
        if len(id_str) <= 3:
            return id_str
        return id_str[:3] + mask_char * 4 + id_str[-3:]
    
    @staticmethod
    def create_buttons(event, button_configs):
        # 按钮功能已禁用
        return None
    
    @staticmethod
    def safe_reply(event, message, buttons=None):
        # 忽略按钮参数，直接发送消息
        event.reply(message)
    
    @classmethod
    def get_regex_handlers(cls):
        if not cls._restart_status_checked:
            cls._restart_status_checked = True
            cls._check_restart_status()
        
        return {
            r'^用户统计$': {'handler': 'get_stats', 'owner_only': True},
            r'^我的id$': {'handler': 'getid', 'owner_only': False},
            r'^dau(?:\s+)?(\d{4})?$': {'handler': 'handle_dau', 'owner_only': True},
            r'^补全dau$': {'handler': 'complete_dau', 'owner_only': True},
            r'^获取全部指令$': {'handler': 'admin_tools', 'owner_only': True},
            r'^关于$': {'handler': 'about_info', 'owner_only': False},
            r'^删除历史数据$': {'handler': 'clean_historical_data', 'owner_only': True},
            r'^dm(.+)$': {'handler': 'send_dm', 'owner_only': True},
            r'^重启$': {'handler': 'restart_bot', 'owner_only': True}
        }
    
    @staticmethod
    def getid(event):
        info_parts = [
            f"用户ID: {event.user_id}",
            f"群组ID: {event.group_id}"
        ]
        
        event.reply("\n".join(info_parts))
    
    @staticmethod
    def send_dm(event):
        content = event.matches[0] if event.matches and event.matches[0] else ""
        
        if not content.strip():
            event.reply(f"❌ 消息内容不能为空\n💡 使用格式：dm+消息内容")
            return
        
        if '\\n' in content or '\\t' in content or '\\r' in content or '\\\\' in content:
            content = content.encode('utf-8').decode('unicode_escape')
        
        event.reply(content)
    
    @classmethod
    def admin_tools(cls, event):
        plugin_manager = PluginManager()
        plugin_manager.load_plugins()
        plugins = list(plugin_manager._plugins.keys())
        
        header = [
            f'📋 所有可用指令列表',
            f'总插件数: {len(plugins)}个'
        ]
        
        code_content = []
        total_commands = 0
        
        for plugin in plugins:
            plugin_name = plugin.__name__
            priority = plugin_manager._plugins[plugin]
            handlers = plugin.get_regex_handlers()
            
            if handlers:
                code_content.append(f'🔧 插件: {plugin_name} (优先级: {priority})')
                commands = []
                
                for pattern, handler_info in handlers.items():
                    total_commands += 1
                    emoji = "👑" if isinstance(handler_info, dict) and handler_info.get('owner_only', False) else "🔹"
                    clean_pattern = pattern.replace('^', '').replace('$', '')
                    commands.append(f"  {emoji} {clean_pattern}")
                
                if commands:
                    code_content.extend(sorted(commands))
                    code_content.append('-' * 30)
        
        code_content.append(f'总命令数: {total_commands}个')
        message = '\n'.join(header) + "\n\n```python\n" + '\n'.join(code_content) + "\n```\n"
        
        event.reply(message)
    
    @classmethod
    def handle_dau(cls, event):
        date_str = event.matches[0] if event.matches and event.matches[0] else None
        
        if date_str:
            cls._handle_specific_date_dau(event, date_str)
        else:
            cls._handle_today_dau(event)
    
    @classmethod
    def _handle_specific_date_dau(cls, event, date_str):
        """处理特定日期的DAU查询"""
        if len(date_str) != 4:
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
    
    @classmethod
    def _handle_today_dau(cls, event):
        """处理今日DAU查询"""
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
        """获取DAU数据"""
        start_time = time.time()
        target_date = datetime.datetime.strptime(date_str, '%Y%m%d')
        today = datetime.datetime.now().date()
        is_today = target_date.date() == today
        
        # 如果不是今日，优先尝试从数据库读取历史DAU数据
        if not is_today:
            try:
                from function.dau_analytics import get_dau_analytics
                dau_analytics = get_dau_analytics()
                dau_data = dau_analytics.load_dau_data(target_date)
                
                if dau_data:
                    cls._send_dau_from_database(event, dau_data, target_date, start_time)
                    return
            except Exception as e:
                logger.warning(f"尝试从数据库读取DAU数据失败: {e}")
            
            display_date = f"{date_str[4:6]}-{date_str[6:8]}"
            event.reply(
                f"<@{event.user_id}>\n"
                f"❌ {display_date} 的DAU数据未生成或无该日期数据\n"
                f"💡 可以发送'补全dau'命令补全DAU记录"
            )
            return
        
        if not LOG_DB_CONFIG.get('enabled', False):
            event.reply("日志数据库未启用，无法获取DAU统计")
            return
            
        try:
            log_db_pool = LogDatabasePool()
            connection = log_db_pool.get_connection()
            
            if not connection:
                event.reply("无法连接到日志数据库，请稍后再试")
                return
            
            cursor = None
            
            try:
                cursor = connection.cursor()
                # 使用配置中的表前缀
                table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
                table_name = f"{table_prefix}{date_str}_message"
                
                check_query = """
                    SELECT COUNT(*) as count 
                    FROM information_schema.tables 
                    WHERE table_schema = DATABASE() 
                    AND table_name = %s
                """
                
                cursor.execute(check_query, (table_name,))
                result = cursor.fetchone()
                if not result or result['count'] == 0:
                    display_date = f"{date_str[4:6]}-{date_str[6:8]}"
                    event.reply(f"该日期({display_date})无消息记录")
                    return
                
                time_condition = ""
                if current_hour is not None and current_minute is not None:
                    time_limit = f"{current_hour:02d}:{current_minute:02d}:00"
                    time_condition = f" WHERE TIME(timestamp) <= '{time_limit}'"
                
                total_messages_query = f"SELECT COUNT(*) as count FROM {table_name}{time_condition}"
                cursor.execute(total_messages_query)
                total_messages_result = cursor.fetchone()
                total_messages = total_messages_result['count'] if total_messages_result else 0
                
                unique_users_query = f"SELECT COUNT(DISTINCT user_id) as count FROM {table_name}{time_condition}"
                unique_users_query += " AND user_id IS NOT NULL AND user_id != ''" if time_condition else " WHERE user_id IS NOT NULL AND user_id != ''"
                cursor.execute(unique_users_query)
                unique_users_result = cursor.fetchone()
                unique_users = unique_users_result['count'] if unique_users_result else 0
                
                unique_groups_query = f"SELECT COUNT(DISTINCT group_id) as count FROM {table_name}{time_condition}"
                unique_groups_query += " AND group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''" if time_condition else " WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''"
                cursor.execute(unique_groups_query)
                unique_groups_result = cursor.fetchone()
                unique_groups = unique_groups_result['count'] if unique_groups_result else 0
                
                private_messages_query = f"SELECT COUNT(*) as count FROM {table_name}{time_condition}"
                private_messages_query += " AND group_id = 'c2c'" if time_condition else " WHERE group_id = 'c2c'"
                cursor.execute(private_messages_query)
                private_messages_result = cursor.fetchone()
                private_messages = private_messages_result['count'] if private_messages_result else 0
                
                # 获取最活跃的2个群组
                active_groups_query = f"""
                    SELECT group_id, COUNT(*) as msg_count 
                    FROM {table_name}{time_condition}
                    """
                active_groups_query += " AND group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''" if time_condition else " WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''"
                active_groups_query += """
                    GROUP BY group_id 
                    ORDER BY msg_count DESC 
                    LIMIT 2
                """
                cursor.execute(active_groups_query)
                active_groups_result = cursor.fetchall()
                
                # 获取最活跃的2个用户
                active_users_query = f"""
                    SELECT user_id, COUNT(*) as msg_count 
                    FROM {table_name}{time_condition}
                    """
                active_users_query += " AND user_id IS NOT NULL AND user_id != ''" if time_condition else " WHERE user_id IS NOT NULL AND user_id != ''"
                active_users_query += """
                    GROUP BY user_id 
                    ORDER BY msg_count DESC 
                    LIMIT 2
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
                
                # 读取DAU表中的事件数据（仅限今日）
                event_stats = {'group_join_count': 0, 'group_leave_count': 0, 'friend_add_count': 0, 'friend_remove_count': 0}
                if is_today:
                    try:
                        dau_table_name = f"{table_prefix}dau"
                        cursor.execute(f"""
                            SELECT COUNT(*) as count 
                            FROM information_schema.tables 
                            WHERE table_schema = DATABASE() 
                            AND table_name = %s
                        """, (dau_table_name,))
                        dau_table_exists = cursor.fetchone()
                        
                        if dau_table_exists and dau_table_exists['count'] > 0:
                            cursor.execute(f"""
                                SELECT group_join_count, group_leave_count, friend_add_count, friend_remove_count
                                FROM {dau_table_name}
                                WHERE date = %s
                            """, (target_date.strftime('%Y-%m-%d'),))
                            dau_result = cursor.fetchone()
                            
                            if dau_result:
                                event_stats['group_join_count'] = dau_result.get('group_join_count', 0)
                                event_stats['group_leave_count'] = dau_result.get('group_leave_count', 0)
                                event_stats['friend_add_count'] = dau_result.get('friend_add_count', 0)
                                event_stats['friend_remove_count'] = dau_result.get('friend_remove_count', 0)
                    except Exception as e:
                        logger.warning(f"读取DAU事件数据失败: {e}")
                
                # 将YYYYMMDD格式转换为更易读的格式
                display_date = f"{date_str[4:6]}-{date_str[6:8]}"
                
                # 如果有昨天的日期，查询昨天同时段的数据进行对比
                yesterday_data = None
                if yesterday_str and current_hour is not None and current_minute is not None:
                    yesterday_table = f"{table_prefix}{yesterday_str}_message"
                    
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
                
                # 添加事件统计（仅限今日）
                if is_today and event_stats and any(event_stats.values()):
                    info.append(f'📈 今日事件统计:')
                    group_join = event_stats["group_join_count"]
                    group_leave = event_stats["group_leave_count"]
                    friend_add = event_stats["friend_add_count"] 
                    friend_remove = event_stats["friend_remove_count"]
                    
                    info.append(f'  👥 加群: {group_join} | 退群: {group_leave}')
                    info.append(f'  👤 加友: {friend_add} | 删友: {friend_remove}')
                    
                    # 计算群组和好友的净增长
                    group_net = group_join - group_leave
                    friend_net = friend_add - friend_remove
                    info.append(f'  📊 群组净增: {group_net:+d} | 好友净增: {friend_net:+d}')
                
                # 添加最活跃群组信息
                if active_groups_result:
                    info.append('🔝 最活跃群组:')
                    idx = 1
                    for group in active_groups_result:
                        group_id = group['group_id']
                        if not group_id:
                            continue  # 跳过空/None
                        masked_group_id = system_plugin.mask_id(group_id)
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
                        masked_user_id = system_plugin.mask_id(user_id)
                        info.append(f"  {idx}. {masked_user_id} ({user['msg_count']}条)")
                        idx += 1
                
                # 计算查询耗时
                query_time = round((time.time() - start_time) * 1000)
                info.append(f'🕒 查询耗时: {query_time}ms')
                info.append(f'📁 数据源: 实时数据库查询')
                
                # 发送消息
                event.reply('\n'.join(info))
                
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
    def _send_dau_from_database(cls, event, dau_data, target_date, start_time):
        """从数据库加载DAU数据并发送"""
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
            
            # 添加事件统计数据（如果有）
            event_stats = dau_data.get('event_stats', {})
            if event_stats and any(event_stats.values()):
                info.append(f'📈 事件统计:')
                group_join = event_stats.get("group_join_count", 0)
                group_leave = event_stats.get("group_leave_count", 0) 
                friend_add = event_stats.get("friend_add_count", 0)
                friend_remove = event_stats.get("friend_remove_count", 0)
                group_net = group_join - group_leave
                friend_net = friend_add - friend_remove
                info.append(f'  👥 加群: {group_join} | 退群: {group_leave} | 净增: {group_net:+d}')
                info.append(f'  👤 加友: {friend_add} | 删友: {friend_remove} | 净增: {friend_net:+d}')
            
            # 添加最活跃群组信息
            top_groups = msg_stats.get("top_groups", [])
            if top_groups:
                info.append('🔝 最活跃群组:')
                idx = 1
                for group in top_groups[:2]:  # 只显示前2个
                    group_id = group.get("group_id", "")
                    if not group_id:
                        continue  # 跳过空/None
                    masked_group_id = system_plugin.mask_id(group_id)
                    info.append(f"  {idx}. {masked_group_id} ({group.get('message_count', 0)}条)")
                    idx += 1
            
            # 添加最活跃用户信息
            top_users = msg_stats.get("top_users", [])
            if top_users:
                info.append('👑 最活跃用户:')
                idx = 1
                for user in top_users[:2]:  # 只显示前2个
                    user_id = user.get("user_id", "")
                    if not user_id:
                        continue  # 跳过空/None
                    masked_user_id = system_plugin.mask_id(user_id)
                    info.append(f"  {idx}. {masked_user_id} ({user.get('message_count', 0)}条)")
                    idx += 1
            
            # 计算查询耗时
            query_time = round((time.time() - start_time) * 1000)
            info.append(f'🕒 查询耗时: {query_time}ms')
            info.append(f'📁 数据源: 数据库')
            
            # 添加生成时间信息
            if dau_data.get('generated_at'):
                try:
                    gen_time = datetime.datetime.fromisoformat(dau_data['generated_at'].replace('Z', '+00:00'))
                    info.append(f'🕒 数据生成时间: {gen_time.strftime("%m-%d %H:%M")}')
                except:
                    pass
            
            # 发送消息
            event.reply('\n'.join(info))
            
        except Exception as e:
            logger.error(f"发送DAU数据库数据失败: {e}")
            # 如果解析数据库数据失败，回退到原始错误消息
            event.reply(f"DAU数据库数据解析失败: {str(e)}")
    
    @classmethod
    def _get_query_params(cls):
        """获取所有查询参数"""
        return [
            # 基础查询
            (f"SELECT COUNT(*) as count FROM {USERS_TABLE}", None, False),  # 用户数量
            (f"SELECT COUNT(*) as count FROM {GROUPS_TABLE}", None, False),  # 群组数量
            (f"SELECT COUNT(*) as count FROM {MEMBERS_TABLE}", None, False),  # 私聊用户数量
            # 最活跃群组查询
            (f"""
                SELECT group_id, JSON_LENGTH(users) as member_count
                FROM {GROUPS_USERS_TABLE}
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
                            (f"SELECT users FROM {GROUPS_USERS_TABLE} WHERE group_id = %s", (group_id,), False),
            # 获取所有群组数据，用于计算排名
            (f"""
                SELECT group_id, JSON_LENGTH(users) as member_count
                FROM {GROUPS_USERS_TABLE}
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
            # 使用统一的脱敏方法
            if group_id != "无数据":
                group_id = system_plugin.mask_id(group_id)
            
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
            
            # 发送消息
            event.reply('\n'.join(info))
            
        except Exception as e:
            logger.error(f'获取统计信息失败: {e}')
            event.reply(f'统计服务暂时不可用，错误信息: {str(e)}')
    
    @staticmethod
    def about_info(event):
        """关于界面"""
        try:
            plugin_manager = PluginManager()
            plugin_manager.load_plugins()
            kernel_count = len(plugin_manager._plugins)
            function_count = len(plugin_manager._regex_handlers)
        except:
            kernel_count = "获取失败"
            function_count = "获取失败"
            
        import platform
        python_version = platform.python_version()
            
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
        system_plugin.safe_reply(event, msg) 
    
    @staticmethod
    def complete_dau(event):
        from function.dau_analytics import get_dau_analytics
        
        dau_analytics = get_dau_analytics()
        today = datetime.datetime.now()
        
        missing_dates = []
        for i in range(1, 31):
            target_date = today - datetime.timedelta(days=i)
            dau_data = dau_analytics.load_dau_data(target_date)
            if not dau_data:
                missing_dates.append(target_date)
        
        if not missing_dates:
            event.reply(f"✅ 近30天DAU数据完整，无需补全！")
            return
        
        event.reply(f"🔧 检测到{len(missing_dates)}天的DAU数据缺失，开始补全...")
        
        generated_count, failed_count = 0, 0
        generated_dates, failed_dates = [], []
        
        for target_date in missing_dates:
            success = dau_analytics.manual_generate_dau(target_date)
            if success:
                generated_count += 1
                generated_dates.append(target_date.strftime('%Y-%m-%d'))
            else:
                failed_count += 1
                failed_dates.append(target_date.strftime('%Y-%m-%d'))
        
        system_plugin._send_dau_complete_result(event, generated_count, failed_count, 
                                               len(missing_dates), generated_dates, failed_dates)
    
    @staticmethod
    def _send_dau_complete_result(event, generated_count, failed_count, total_count, 
                                 generated_dates, failed_dates):
        """发送DAU补全结果"""
        info = [
            f'<@{event.user_id}>',
            f'📊 DAU数据补全完成！',
            f'',
            f'📈 处理结果:',
            f'✅ 成功生成: {generated_count}天',
            f'❌ 生成失败: {failed_count}天',
            f'📅 总计处理: {total_count}天'
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
        
        # 发送消息
        event.reply('\n'.join(info))
    
    @staticmethod
    def clean_historical_data(event):
        """删除历史数据：8天以外的日志表"""
        try:
            start_time = time.time()
            
            # 发送开始清理消息
            event.reply(f"<@{event.user_id}>\n🧹 开始清理历史数据，请稍等...")
            
            # 获取日期
            today = datetime.datetime.now()
            today_str = today.strftime('%Y%m%d')
            eight_days_ago = today - datetime.timedelta(days=8)
            
            cleanup_results = []
            

            
            # 清理日志表
            log_result = system_plugin._clean_log_tables(eight_days_ago)
            cleanup_results.append(log_result)
            
            # 发送结果
            system_plugin._send_cleanup_result(event, cleanup_results, start_time, eight_days_ago)
        except Exception as e:
            logger.error(f'删除历史数据失败: {e}')
            event.reply(f'<@{event.user_id}>\n❌ 删除历史数据失败: {str(e)}')
    

    
    @staticmethod 
    def _clean_log_tables(eight_days_ago):
        """清理8天以外的日志表"""
        try:
            # 检查日志数据库配置
            if not LOG_DB_CONFIG.get('enabled', False):
                return "⚠️ 日志数据库未启用，跳过日志表清理"
            
            log_db_pool = LogDatabasePool()
            connection = log_db_pool.get_connection()
            
            if not connection:
                return "❌ 无法连接到日志数据库"
            
            try:
                deleted_count = system_plugin._delete_old_log_tables(connection, eight_days_ago)
                return f"✅ 日志表清理: 删除 {deleted_count} 张表"
            finally:
                log_db_pool.release_connection(connection)
                
        except Exception as e:
            logger.error(f"清理日志表失败: {e}")
            return f"❌ 日志表清理失败: {str(e)}"
    
    @staticmethod
    def _delete_old_log_tables(connection, eight_days_ago):
        """删除旧的日志表"""
        from pymysql.cursors import DictCursor
        cursor = None
        deleted_count = 0
        
        try:
            cursor = connection.cursor(DictCursor)
            
            # 获取配置中的表前缀
            table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            
            # 获取所有日志表
            cursor.execute(f"""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND (table_name LIKE '{table_prefix}%_message' 
                     OR table_name LIKE '{table_prefix}%_plugin'
                     OR table_name LIKE '{table_prefix}%_framework' 
                     OR table_name LIKE '{table_prefix}%_error'
                     OR table_name LIKE '{table_prefix}%_unmatched')
            """)
            
            log_tables = cursor.fetchall()
            logger.info(f"找到 {len(log_tables)} 张日志表待检查")
            
            for table in log_tables:
                if system_plugin._should_delete_table(table, eight_days_ago):
                    table_name = system_plugin._get_table_name(table)
                    if table_name and system_plugin._drop_table(cursor, table_name):
                        deleted_count += 1
            
            if deleted_count > 0:
                connection.commit()
                logger.info(f"已提交日志表删除操作，共删除 {deleted_count} 张表")
                
        except Exception as e:
            logger.error(f"删除日志表过程中发生错误: {e}")
            if connection:
                connection.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
                
        return deleted_count
    
    @staticmethod
    def _should_delete_table(table, eight_days_ago):
        """判断是否应该删除表"""
        table_name = system_plugin._get_table_name(table)
        if not table_name:
            return False
            
        try:
            # 获取配置中的表前缀
            table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
            
            # 检查表名是否以配置的前缀开始
            if not table_name.startswith(table_prefix):
                return False
                
            # 移除前缀后获取剩余部分
            remaining_part = table_name[len(table_prefix):]
            parts = remaining_part.split('_')
            
            if len(parts) < 2:
                return False
                
            date_part = parts[0]  # 获取YYYYMMDD部分
            
            if len(date_part) != 8 or not date_part.isdigit():
                return False
                
            table_date = datetime.datetime.strptime(date_part, '%Y%m%d')
            return table_date < eight_days_ago
            
        except (IndexError, ValueError) as e:
            logger.warning(f"无法解析表名日期 {table_name}: {e}")
            return False
    
    @staticmethod
    def _get_table_name(table):
        """从表记录中获取表名"""
        if not isinstance(table, dict):
            logger.warning(f"跳过无效的表记录: {table}")
            return None
            
        for key in table.keys():
            if key.lower() == 'table_name':
                return table[key]
        return None
    
    @staticmethod
    def _drop_table(cursor, table_name):
        """删除指定表"""
        try:
            drop_sql = f"DROP TABLE IF EXISTS `{table_name}`"
            cursor.execute(drop_sql)
            logger.info(f"删除日志表: {table_name}")
            return True
        except Exception as e:
            logger.error(f"删除表 {table_name} 失败: {e}")
            return False
    
    @staticmethod
    def _send_cleanup_result(event, cleanup_results, start_time, eight_days_ago):
        """发送清理结果"""
        total_time = round((time.time() - start_time) * 1000)
        
        info = [
            f'<@{event.user_id}>',
            f'🧹 历史数据清理完成！',
            f'',
            f'📊 清理结果:'
        ]
        
        info.extend(cleanup_results)
        info.extend([
            f'',
            f'🕒 清理耗时: {total_time}ms',
            f'📅 清理范围: {eight_days_ago.strftime("%Y-%m-%d")}之前的日志表'
        ])
        
        event.reply('\n'.join(info))
    
    @staticmethod
    def restart_bot(event):
        current_pid = os.getpid()
        current_dir = os.getcwd()
        main_py_path = os.path.join(current_dir, 'main.py')
        
        if not os.path.exists(main_py_path):
            event.reply('❌ main.py文件不存在！')
            return
        
        event.reply('🔄 正在重启机器人...\n⏱️ 预计重启时间: 1秒')
        
        restart_status = {
            'restart_time': datetime.datetime.now().isoformat(),
            'completed': False,
            'message_id': event.message_id,
            'user_id': event.user_id,
            'group_id': event.group_id if event.is_group else 'c2c'
        }
        
        restart_status_file = system_plugin._get_restart_status_file()
        with open(restart_status_file, 'w', encoding='utf-8') as f:
            json.dump(restart_status, f, ensure_ascii=False)
        
        restart_script_content = system_plugin._create_restart_python_script(current_pid, main_py_path)
        restart_script_path = os.path.join(current_dir, 'bot_restarter.py')
        
        with open(restart_script_path, 'w', encoding='utf-8') as f:
            f.write(restart_script_content)
        
        is_windows = platform.system().lower() == 'windows'
        
        if is_windows:
            subprocess.Popen(['python', restart_script_path], cwd=current_dir,
                           creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen([sys.executable, restart_script_path], cwd=current_dir,
                           start_new_session=True)
    
    @staticmethod
    def _create_restart_python_script(current_pid, main_py_path):
        script_content = f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import signal
import platform
import subprocess

def main():
    current_pid = {current_pid}
    main_py_path = r"{main_py_path}"
    try:
        if platform.system().lower() == 'windows':
            subprocess.run(['taskkill', '/PID', str(current_pid), '/F'], 
                         check=False, capture_output=True)
        else:
            try:
                os.kill(current_pid, signal.SIGTERM)
                time.sleep(0.1)
                try:
                    os.kill(current_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass
    except Exception as e:
        pass
    
    time.sleep(0.1)
    try:
        os.chdir(os.path.dirname(main_py_path))
        
        if platform.system().lower() == 'windows':
            subprocess.Popen(
                [sys.executable, main_py_path],
                creationflags=subprocess.CREATE_NEW_CONSOLE,
                cwd=os.path.dirname(main_py_path)
            )
        else:
            try:
                script_path = __file__
                if os.path.exists(script_path):
                    os.remove(script_path)
            except:
                pass
            os.execv(sys.executable, [sys.executable, main_py_path])
        
    except Exception as e:
        sys.exit(1)
    
    if platform.system().lower() == 'windows':
        time.sleep(0.1)
        try:
            script_path = __file__
            if os.path.exists(script_path):
                os.remove(script_path)
        except:
            pass
        sys.exit(0)

if __name__ == "__main__":
    main()
'''
        return script_content
    
 