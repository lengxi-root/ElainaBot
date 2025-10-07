from core.plugin.PluginManager import Plugin
from function.db_pool import DatabaseService
import json
import logging
import time
import datetime
from config import LOG_DB_CONFIG, USE_MARKDOWN
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
        rows = []
        for row_config in button_configs:
            row = []
            for btn_config in row_config:
                button = {
                    'text': btn_config.get('text', ''),
                    'data': btn_config.get('data', ''),
                    'type': btn_config.get('type', 1),
                    'style': btn_config.get('style', 1),
                    'enter': btn_config.get('enter', True)
                }
                row.append(button)
            rows.append(event.rows(row))
        return event.button(rows)
    
    @staticmethod
    def safe_reply(event, message, buttons=None):
        if USE_MARKDOWN and buttons:
            event.reply(message, buttons, hide_avatar_and_center=True)
        else:
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
            r'^关于$': {'handler': 'about_info', 'owner_only': False},
            r'^dm(.+)$': {'handler': 'send_dm', 'owner_only': True},
            r'^重启$': {'handler': 'restart_bot', 'owner_only': True},
            r'^补全昵称$': {'handler': 'fill_user_names', 'owner_only': True}
        }
    
    @staticmethod
    def getid(event):
        info_parts = [f"<@{event.user_id}>"]
        
        qq = system_plugin._get_user_qq(event.user_id)
        if qq:
            masked_qq = system_plugin.mask_id(qq)
            info_parts.append(f"UIN: {masked_qq}")
        else:
            info_parts.append("UIN: 获取失败")
        
        info_parts.extend([
            f"用户ID: {event.user_id}",
            f"群组ID: {event.group_id}"
        ])
        
        perm_str = system_plugin._get_user_permission(event.user_id)
        info_parts.append(f"用户权限：{perm_str}")
        
        system_plugin.safe_reply(event, "\n".join(info_parts))
    
    @staticmethod
    def send_dm(event):
        content = event.matches[0] if event.matches and event.matches[0] else ""
        if not content.strip():
            event.reply(f"❌ 消息内容不能为空\n💡 使用格式：dm+消息内容")
            return
        
        if '\\n' in content:
            content = content.replace('\\n', '\n')
        if '\\t' in content:
            content = content.replace('\\t', '\t')
        if '\\r' in content:
            content = content.replace('\\r', '\r')
        if '\\\\' in content:
            content = content.replace('\\\\', '\\')
        
        if USE_MARKDOWN:
            button_configs = [[
                {'text': '再次重试', 'data': event.content, 'enter': False, 'style': 1, 'type': 2},
                {'text': '重新测试', 'data': 'dm', 'enter': False, 'style': 1, 'type': 2}
            ]]
            buttons = system_plugin.create_buttons(event, button_configs)
            event.reply(content, buttons)
        else:
            event.reply(content)
    
    @staticmethod
    def _get_user_qq(user_id):
        result = DatabaseService.execute_query("SELECT qq FROM M_users WHERE user_id = %s", (user_id,))
        return result.get('qq') if result else None
    
    @staticmethod
    def _get_user_permission(user_id):
        resp = sync_get('https://api.elaina.vin/api/积分/特殊用户.php', timeout=5)
        data = resp.json()
        user_id_str = str(user_id)
        
        for item in data:
            if item.get('openid') == user_id_str or item.get('qq') == user_id_str:
                return item.get('reason', '特殊权限用户')
        return "普通用户"
    
    @classmethod
    def handle_dau(cls, event):
        date_str = event.matches[0] if event.matches and event.matches[0] else None
        
        if date_str:
            cls._handle_specific_date_dau(event, date_str)
        else:
            cls._handle_today_dau(event)
    
    @classmethod
    def _handle_specific_date_dau(cls, event, date_str):
        if len(date_str) != 4:
            event.reply("日期格式错误，请使用MMDD格式，例如：dau0522表示5月22日")
            return
            
        current_year = datetime.datetime.now().year
        month = int(date_str[:2])
        day = int(date_str[2:])
        query_date = datetime.datetime(current_year, month, day)
        
        if query_date > datetime.datetime.now():
            query_date = datetime.datetime(current_year - 1, month, day)
            
        formatted_date = query_date.strftime('%Y%m%d')
        cls._get_dau_data(event, formatted_date)
    
    @classmethod
    def _handle_today_dau(cls, event):
        today = datetime.datetime.now()
        today_str = today.strftime('%Y%m%d')
        yesterday = today - datetime.timedelta(days=1)
        yesterday_str = yesterday.strftime('%Y%m%d')
        current_hour = today.hour
        current_minute = today.minute
        cls._get_dau_data(event, today_str, yesterday_str, current_hour, current_minute)
    
    @classmethod
    def _get_dau_data(cls, event, date_str, yesterday_str=None, current_hour=None, current_minute=None):
        start_time = time.time()
        target_date = datetime.datetime.strptime(date_str, '%Y%m%d')
        today = datetime.datetime.now().date()
        is_today = target_date.date() == today
        
        if not is_today:
            from function.dau_analytics import get_dau_analytics
            dau_analytics = get_dau_analytics()
            dau_data = dau_analytics.load_dau_data(target_date)
            
            if dau_data:
                cls._send_dau_from_database(event, dau_data, target_date, start_time)
                return
            
            display_date = f"{date_str[4:6]}-{date_str[6:8]}"
            event.reply(f"<@{event.user_id}>\n❌ {display_date} 的DAU数据未生成或无该日期数据")
            return
        
        if not LOG_DB_CONFIG.get('enabled', False):
            event.reply("日志数据库未启用，无法获取DAU统计")
            return
            
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            event.reply("无法连接到日志数据库，请稍后再试")
            return
        
        cursor = connection.cursor()
        table_name = f"Mlog_{date_str}_message"
        
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
        
        hourly_stats_query = f"""
            SELECT HOUR(timestamp) as hour, COUNT(*) as count 
            FROM {table_name}{time_condition} 
            GROUP BY HOUR(timestamp) 
            ORDER BY hour
        """
        cursor.execute(hourly_stats_query)
        hourly_stats_result = cursor.fetchall()
        
        hours_data = {i: 0 for i in range(24)}
        if hourly_stats_result:
            for row in hourly_stats_result:
                hour = row['hour']
                count = row['count']
                hours_data[hour] = count
        
        most_active_hour = max(hours_data.items(), key=lambda x: x[1]) if hours_data else (0, 0)
        
        event_stats = {'group_join_count': 0, 'group_leave_count': 0, 'friend_add_count': 0, 'friend_remove_count': 0}
        if is_today:
            dau_table_name = "Mlog_dau"
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
        
        display_date = f"{date_str[4:6]}-{date_str[6:8]}"
        
        yesterday_data = None
        if yesterday_str and current_hour is not None and current_minute is not None:
            yesterday_table = f"Mlog_{yesterday_str}_message"
            cursor.execute(check_query, (yesterday_table,))
            y_result = cursor.fetchone()
            
            if y_result and y_result['count'] > 0:
                time_limit = f"{current_hour:02d}:{current_minute:02d}:00"
                y_time_condition = f" WHERE TIME(timestamp) <= '{time_limit}'"
                yesterday_data = {}
                
                cursor.execute(f"SELECT COUNT(*) as count FROM {yesterday_table}{y_time_condition}")
                y_total = cursor.fetchone()
                yesterday_data['total_messages'] = y_total['count'] if y_total else 0
                
                y_users_query = f"SELECT COUNT(DISTINCT user_id) as count FROM {yesterday_table}{y_time_condition}"
                y_users_query += " AND user_id IS NOT NULL AND user_id != ''"
                cursor.execute(y_users_query)
                y_users = cursor.fetchone()
                yesterday_data['unique_users'] = y_users['count'] if y_users else 0
                
                y_groups_query = f"SELECT COUNT(DISTINCT group_id) as count FROM {yesterday_table}{y_time_condition}"
                y_groups_query += " AND group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''"
                cursor.execute(y_groups_query)
                y_groups = cursor.fetchone()
                yesterday_data['unique_groups'] = y_groups['count'] if y_groups else 0
                
                y_private_query = f"SELECT COUNT(*) as count FROM {yesterday_table}{y_time_condition}"
                y_private_query += " AND group_id = 'c2c'"
                cursor.execute(y_private_query)
                y_private = cursor.fetchone()
                yesterday_data['private_messages'] = y_private['count'] if y_private else 0
        
        info = [
            f'<@{event.user_id}>',
            f'📊 {display_date} 活跃统计' + (f' (截至{current_hour:02d}:{current_minute:02d})' if current_hour is not None else '')
        ]
        
        if yesterday_data:
            y_display_date = f"{yesterday_str[4:6]}-{yesterday_str[6:8]}"
            user_diff = unique_users - yesterday_data['unique_users']
            user_change = f"🔺{user_diff}" if user_diff > 0 else f"🔻{abs(user_diff)}" if user_diff < 0 else "➖0"
            info.append(f'👤 活跃用户数: {unique_users} ({user_change})')
            
            group_diff = unique_groups - yesterday_data['unique_groups']
            group_change = f"🔺{group_diff}" if group_diff > 0 else f"🔻{abs(group_diff)}" if group_diff < 0 else "➖0"
            info.append(f'👥 活跃群聊数: {unique_groups} ({group_change})')
            
            msg_diff = total_messages - yesterday_data['total_messages']
            msg_change = f"🔺{msg_diff}" if msg_diff > 0 else f"🔻{abs(msg_diff)}" if msg_diff < 0 else "➖0"
            info.append(f'💬 消息总数: {total_messages} ({msg_change})')
            
            private_diff = private_messages - yesterday_data['private_messages']
            private_change = f"🔺{private_diff}" if private_diff > 0 else f"🔻{abs(private_diff)}" if private_diff < 0 else "➖0"
            info.append(f'📱 私聊消息: {private_messages} ({private_change})')
        else:
            info.append(f'👤 活跃用户数: {unique_users}')
            info.append(f'👥 活跃群聊数: {unique_groups}')
            info.append(f'💬 消息总数: {total_messages}')
            info.append(f'📱 私聊消息: {private_messages}')
        
        info.append(f'⏰ 最活跃时段: {most_active_hour[0]}点 ({most_active_hour[1]})')
        
        if is_today and event_stats and any(event_stats.values()):
            info.append(f'📈 今日事件统计:')
            group_join = event_stats["group_join_count"]
            group_leave = event_stats["group_leave_count"]
            friend_add = event_stats["friend_add_count"] 
            friend_remove = event_stats["friend_remove_count"]
            info.append(f'👥 群🔺: {group_join} | 🔻: {group_leave}')
            info.append(f'👤 友🔺: {friend_add} | 🔻: {friend_remove}')
            group_net = group_join - group_leave
            friend_net = friend_add - friend_remove
            info.append(f'📊 群净增: {group_net:+d} | 友净增: {friend_net:+d}')
        
        if active_groups_result:
            info.append('🔝 最活跃群组:')
            idx = 1
            for group in active_groups_result:
                group_id = group['group_id']
                if not group_id:
                    continue
                masked_group_id = system_plugin.mask_id(group_id)
                info.append(f"  {idx}. {masked_group_id} ({group['msg_count']}条)")
                idx += 1
        
        if active_users_result:
            info.append('👑 最活跃用户:')
            idx = 1
            for user in active_users_result:
                user_id = user['user_id']
                if not user_id:
                    continue
                masked_user_id = system_plugin.mask_id(user_id)
                info.append(f"  {idx}. {masked_user_id} ({user['msg_count']}条)")
                idx += 1
        
        query_time = round((time.time() - start_time) * 1000)
        info.append(f'🕒 查询耗时: {query_time}ms')
        info.append(f'📁 数据源: 实时数据库查询')
        
        if USE_MARKDOWN:
            button_configs = [
                [
                    {'text': '查询dau', 'data': 'dau', 'type': 2, 'enter': False},
                    {'text': '今日DAU', 'data': 'dau'}
                ],
                [{'text': '用户统计', 'data': '用户统计'}]
            ]
            buttons = system_plugin.create_buttons(event, button_configs)
            event.reply('\n'.join(info), buttons, hide_avatar_and_center=True)
        else:
            event.reply('\n'.join(info))
        
        if cursor:
            cursor.close()
        if connection:
            log_db_pool.release_connection(connection)
    
    @classmethod
    def _send_dau_from_database(cls, event, dau_data, target_date, start_time):
        msg_stats = dau_data.get('message_stats', {})
        
        info = [
            f'<@{event.user_id}>',
            f'📊 {target_date.strftime("%m-%d")} 活跃统计'
        ]
        
        info.append(f'👤 活跃用户数: {msg_stats.get("active_users", 0)}')
        info.append(f'👥 活跃群聊数: {msg_stats.get("active_groups", 0)}')
        info.append(f'💬 消息总数: {msg_stats.get("total_messages", 0)}')
        info.append(f'📱 私聊消息: {msg_stats.get("private_messages", 0)}')
        
        peak_hour = msg_stats.get("peak_hour", 0)
        peak_hour_count = msg_stats.get("peak_hour_count", 0)
        info.append(f'⏰ 最活跃时段: {peak_hour}点 ({peak_hour_count}条)')
        
        event_stats = dau_data.get('event_stats', {})
        if event_stats and any(event_stats.values()):
            info.append(f'📈 事件统计:')
            group_join = event_stats.get("group_join_count", 0)
            group_leave = event_stats.get("group_leave_count", 0) 
            friend_add = event_stats.get("friend_add_count", 0)
            friend_remove = event_stats.get("friend_remove_count", 0)
            group_net = group_join - group_leave
            friend_net = friend_add - friend_remove
            info.append(f'👥 群数🔺: {group_join} |🔻: {group_leave}')
            info.append(f'👤 友数🔺: {friend_add} |🔻: {friend_remove}\n📊群净增: {group_net:+d} | 友净增: {friend_net:+d}')
        
        top_groups = msg_stats.get("top_groups", [])
        if top_groups:
            info.append('🔝 最活跃群组:')
            idx = 1
            for group in top_groups[:2]:
                group_id = group.get("group_id", "")
                if not group_id:
                    continue
                masked_group_id = system_plugin.mask_id(group_id)
                info.append(f"  {idx}. {masked_group_id} ({group.get('message_count', 0)}条)")
                idx += 1
        
        top_users = msg_stats.get("top_users", [])
        if top_users:
            info.append('👑 最活跃用户:')
            idx = 1
            for user in top_users[:2]:
                user_id = user.get("user_id", "")
                if not user_id:
                    continue
                masked_user_id = system_plugin.mask_id(user_id)
                info.append(f"  {idx}. {masked_user_id} ({user.get('message_count', 0)}条)")
                idx += 1
        
        query_time = round((time.time() - start_time) * 1000)
        info.append(f'🕒 查询耗时: {query_time}ms')
        info.append(f'📁 数据源: 数据库')
        
        if dau_data.get('generated_at'):
            gen_time = datetime.datetime.fromisoformat(dau_data['generated_at'].replace('Z', '+00:00'))
            info.append(f'🕒 数据生成时间: {gen_time.strftime("%m-%d %H:%M")}')
        
        if USE_MARKDOWN:
            button_configs = [
                [
                    {'text': '查询dau', 'data': 'dau', 'type': 2, 'enter': False},
                    {'text': '今日DAU', 'data': 'dau'}
                ],
                [{'text': '用户统计', 'data': '用户统计'}]
            ]
            buttons = system_plugin.create_buttons(event, button_configs)
            event.reply('\n'.join(info), buttons, hide_avatar_and_center=True)
        else:
            event.reply('\n'.join(info))
    
    @classmethod
    def _get_query_params(cls):
        return [
            ("SELECT COUNT(*) as count FROM M_users", None, False),
            ("SELECT COUNT(*) as count FROM M_groups", None, False),
            ("SELECT COUNT(*) as count FROM M_members", None, False),
            ("""
                SELECT group_id, JSON_LENGTH(users) as member_count
                FROM M_groups_users
                ORDER BY member_count DESC
                LIMIT 1
            """, None, False),
            ("SELECT 1 as placeholder", None, False)
        ]
    
    @classmethod
    def _get_group_info_params(cls, group_id):
        return [
            ("SELECT users FROM M_groups_users WHERE group_id = %s", (group_id,), False),
            ("""
                SELECT group_id, JSON_LENGTH(users) as member_count
                FROM M_groups_users
                ORDER BY member_count DESC
            """, None, True)
        ]
    
    @classmethod
    def _process_result(cls, results):
        user_count = results[0]['count'] if results[0] else 0
        group_count = results[1]['count'] if results[1] else 0
        private_users_count = results[2]['count'] if results[2] else 0
        
        most_active_result = results[3]
        if most_active_result:
            group_id = most_active_result.get('group_id', "无数据")
            if group_id != "无数据":
                group_id = system_plugin.mask_id(group_id)
            
            most_active_group = {
                'group_id': group_id,
                'member_count': most_active_result.get('member_count', 0)
            }
        else:
            most_active_group = {'group_id': "无数据", 'member_count': 0}
        
        uin_success = 64019
        
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
        group_members = 0
        if results[0] and results[0].get('users'):
            users = results[0]['users']
            if isinstance(users, str):
                users = json.loads(users)
            group_members = len(users)
        
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
        start_time = time.time()
        db = DatabaseService()
        query_params = cls._get_query_params()
        
        group_results = None
        if event.group_id:
            group_query_params = cls._get_group_info_params(event.group_id)
            group_results = db.execute_concurrent_queries(group_query_params)
        
        results = db.execute_concurrent_queries(query_params)
        stats = cls._process_result(results)
        
        info = [
            f'<@{event.user_id}>',
            f'📊 统计信息',
        ]
        
        if event.group_id and group_results:
            group_info = cls._process_group_results(group_results, event.group_id)
            info.append(f'👥 当前群成员: {group_info["member_count"]}')
        
        info.append(f'👤 好友总数量: {stats["private_users_count"]}')
        info.append(f'👥 群组总数量: {stats["group_count"]}')
        info.append(f'👥 所有用户总数量: {stats["user_count"]}')
        info.append(f'🔝 最大群: {stats["most_active_group"]["group_id"]} (群员: {stats["most_active_group"]["member_count"]})')
        info.append(f'✅ UIN成功获取: {stats["uin_stats"]["success"]}')
        
        if event.group_id and group_results:
            group_info = cls._process_group_results(group_results, event.group_id)
            info.append(f'📈 当前群排名: 第{group_info["rank"]}名')
        
        query_time = round((time.time() - start_time) * 1000)
        info.append(f'🕒 查询耗时: {query_time}ms')
        
        if USE_MARKDOWN:
            button_configs = [[{'text': 'DAU查询', 'data': 'dau'}]]
            buttons = system_plugin.create_buttons(event, button_configs)
            event.reply('\n'.join(info), buttons, hide_avatar_and_center=True)
        else:
            event.reply('\n'.join(info))
    
    @staticmethod
    def about_info(event):
        PluginManager.load_plugins()
        kernel_count = len(PluginManager._plugins)
        function_count = len(PluginManager._regex_handlers)
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
        if USE_MARKDOWN:
            button_configs = [[
                {'text': '菜单', 'data': '/菜单'},
                {'text': '娱乐菜单', 'data': '/娱乐菜单'}
            ]]
            btn = system_plugin.create_buttons(event, button_configs)
            system_plugin.safe_reply(event, msg, btn)
        else:
            event.reply(msg) 
    
    @staticmethod
    def restart_bot(event):
        import psutil
        import importlib.util
        
        current_pid = os.getpid()
        current_dir = os.getcwd()
        main_py_path = os.path.join(current_dir, 'main.py')
        
        if not os.path.exists(main_py_path):
            event.reply('❌ main.py文件不存在！')
            return
        
        config_path = os.path.join(current_dir, 'config.py')
        config = None
        if os.path.exists(config_path):
            spec = importlib.util.spec_from_file_location("config", config_path)
            config = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(config)
        
        is_dual_process = False
        main_port = 5001
        web_port = 5002
        
        if config and hasattr(config, 'SERVER_CONFIG'):
            server_config = config.SERVER_CONFIG
            is_dual_process = server_config.get('web_dual_process', False)
            main_port = server_config.get('port', 5001)
            web_port = server_config.get('web_port', 5002)
        
        restart_mode = "独立进程模式" if is_dual_process else "单进程模式"
        event.reply(f'🔄 正在重启机器人... ({restart_mode})\n⏱️ 预计重启时间: 1秒')
        
        restart_status = {
            'restart_time': datetime.datetime.now().isoformat(),
            'completed': False,
            'message_id': event.message_id,
            'user_id': event.user_id,
            'group_id': event.group_id if hasattr(event, 'is_group') and event.is_group else 'c2c'
        }
        
        restart_status_file = system_plugin._get_restart_status_file()
        with open(restart_status_file, 'w', encoding='utf-8') as f:
            json.dump(restart_status, f, ensure_ascii=False)
        
        restart_script_content = system_plugin._create_restart_python_script(
            main_py_path, is_dual_process, main_port, web_port, current_pid
        )
        restart_script_path = os.path.join(current_dir, 'bot_restarter.py')
        
        with open(restart_script_path, 'w', encoding='utf-8') as f:
            f.write(restart_script_content)
        
        if is_dual_process:
            main_pids = system_plugin._find_processes_by_port(main_port)
            web_pids = system_plugin._find_processes_by_port(web_port)
            logger.info(f"[重启] 主程序端口: {main_port}, Web面板端口: {web_port}")
            logger.info(f"[重启] 主程序进程: {main_pids}, Web面板进程: {web_pids}")
        
        is_windows = platform.system().lower() == 'windows'
        
        if is_windows:
            subprocess.Popen(['python', restart_script_path], cwd=current_dir,
                           creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen([sys.executable, restart_script_path], cwd=current_dir,
                           start_new_session=True)
    
    @staticmethod
    def _find_processes_by_port(port):
        import psutil
        pids = []
        for conn in psutil.net_connections():
            if conn.laddr.port == port and conn.status == 'LISTEN':
                try:
                    proc = psutil.Process(conn.pid)
                    pids.append(conn.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        return pids
    
    @staticmethod
    def _create_restart_python_script(main_py_path, is_dual_process=False, main_port=5001, web_port=5002, current_python_pid=None):
        if current_python_pid is None:
            current_python_pid = os.getpid()
            
        if is_dual_process:
            kill_ports_code = f"""
        ports_to_kill = [{main_port}, {web_port}]
        pids_to_kill = []
        
        for port in ports_to_kill:
            for conn in psutil.net_connections():
                if conn.laddr.port == port and conn.status == 'LISTEN':
                    try:
                        proc = psutil.Process(conn.pid)
                        pids_to_kill.append(conn.pid)
                        print(f"找到端口{{port}}的进程: PID {{conn.pid}}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
        
        pids_to_kill = list(set(pids_to_kill))
        
        for pid in pids_to_kill:
            try:
                if platform.system().lower() == 'windows':
                    result = subprocess.run(['taskkill', '/PID', str(pid), '/F'], 
                                         check=False, capture_output=True)
                    print(f"Windows: 杀死进程 PID {{pid}}, 返回码: {{result.returncode}}")
                else:
                    proc = psutil.Process(pid)
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                        print(f"Linux: 进程 PID {{pid}} 已正常终止")
                    except psutil.TimeoutExpired:
                        proc.kill()
                        print(f"Linux: 强制杀死进程 PID {{pid}}")
            except Exception as e:
                print(f"杀死进程{{pid}}失败: {{e}}")
        
        # 快速验证进程终止
        time.sleep(0.3)
        
        # 快速验证关键进程是否已终止
        for pid in pids_to_kill[:2]:  # 只检查前2个进程
            try:
                proc = psutil.Process(pid)
                if proc.is_running():
                    print(f"快速强杀进程: PID {{pid}}")
                    if platform.system().lower() == 'windows':
                        subprocess.run(['taskkill', '/PID', str(pid), '/F', '/T'], check=False, timeout=1)
                    else:
                        proc.kill()
            except (psutil.NoSuchProcess, subprocess.TimeoutExpired):
                pass
            except Exception:
                pass
                """
        else:
            kill_ports_code = f"""
        target_pid = {current_python_pid}
        try:
            proc = psutil.Process(target_pid)
            print(f"准备杀死Python进程: PID {{target_pid}}")
            
            if platform.system().lower() == 'windows':
                subprocess.run(['taskkill', '/PID', str(target_pid), '/F', '/T'], 
                             check=False, capture_output=True, timeout=2)
                print(f"Windows: 已杀死进程 PID {{target_pid}}")
            else:
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                    print(f"Linux: 进程 PID {{target_pid}} 已正常终止")
                except psutil.TimeoutExpired:
                    proc.kill()
                    print(f"Linux: 强制杀死进程 PID {{target_pid}}")
        except psutil.NoSuchProcess:
            print(f"进程 {{target_pid}} 不存在或已终止")
        except Exception as e:
            print(f"杀死进程{{target_pid}}失败: {{e}}")
        
        time.sleep(0.2)
                """
        
        script_content = f'''#!/usr/bin/env python3

import os
import sys
import time
import signal
import platform
import subprocess
import psutil

def main():
    main_py_path = r"{main_py_path}"
    
    try:{kill_ports_code}
    except Exception as e:
        print(f"杀死进程过程中出错: {{e}}")
    
    time.sleep(0.1)
    
    ports_to_check = [{main_port}, {web_port}] if {str(is_dual_process)} else [5001]
    max_wait = 2
    wait_count = 0
    while wait_count < max_wait:
        ports_still_occupied = False
        try:
            for conn in psutil.net_connections():
                if conn.laddr.port in ports_to_check and conn.status == 'LISTEN':
                    ports_still_occupied = True
                    break
        except:
            pass
            
        if not ports_still_occupied:
            print("端口已释放")
            break
        else:
            time.sleep(0.2)
            wait_count += 0.2
    
    try:
        os.chdir(os.path.dirname(main_py_path))
        
        print(f"正在重新启动主程序: {{main_py_path}}")
        
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
        
        print("重启命令已执行")
        
    except Exception as e:
        print(f"重启失败: {{e}}")
        sys.exit(1)
    
    if platform.system().lower() == 'windows':
        time.sleep(1)
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
    
    @staticmethod
    def fill_user_names(event):
        start_time = time.time()
        db = Database()
        users_table = db.get_table_name('users')
        
        query = f"SELECT user_id FROM {users_table} WHERE name IS NULL OR name = ''"
        result = DatabaseService.execute_query(query, None, fetch_all=True)
        
        if not result or not isinstance(result, list):
            event.reply(f"<@{event.user_id}>\n✅ 所有用户都已有昵称，无需补全！")
            return
        
        total_users = len(result)
        event.reply(f"<@{event.user_id}>\n🔄 开始补全昵称...\n📊 需要处理: {total_users} 个用户")
        
        success_count = 0
        failed_count = 0
        
        for i, user_row in enumerate(result, 1):
            user_id = user_row.get('user_id')
            if not user_id:
                continue
            
            name = db.fetch_user_name_from_api(user_id)
            
            if name:
                sql = f"UPDATE {users_table} SET name = %s WHERE user_id = %s"
                DatabaseService.execute_update(sql, (name, user_id))
                success_count += 1
            else:
                failed_count += 1
            
            if i % 200000 == 0:
                progress = (i / total_users) * 100
                event.reply(f"⏳ 处理进度: {i}/{total_users} ({progress:.1f}%)\n✅ 成功: {success_count} | ❌ 失败: {failed_count}")
        
        total_time = round((time.time() - start_time) * 1000)
        info = [
            f'<@{event.user_id}>',
            f'✅ 昵称补全完成！',
            f'👤 处理总数: {total_users} 个',
            f'✅ 成功补全: {success_count} 个',
            f'❌ 获取失败: {failed_count} 个',
            f'🕒 总耗时: {total_time}ms'
        ]
        
        if USE_MARKDOWN:
            button_configs = [[
                {'text': '用户统计', 'data': '用户统计'},
                {'text': 'DAU查询', 'data': 'dau'}
            ]]
            buttons = system_plugin.create_buttons(event, button_configs)
            event.reply('\n'.join(info), buttons, hide_avatar_and_center=True)
        else:
            event.reply('\n'.join(info))
 