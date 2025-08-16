#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import json
import time
import datetime
import logging
import threading
import schedule
import re
from typing import Dict, List, Any, Optional
from function.log_db import LogDatabasePool
from function.db_pool import DatabaseService
from function.database import USERS_TABLE, GROUPS_TABLE, MEMBERS_TABLE, GROUPS_USERS_TABLE

try:
    from core.plugin.PluginManager import PluginManager
    _plugin_manager_available = True
except ImportError:
    _plugin_manager_available = False

logger = logging.getLogger('dau_analytics')

class DAUAnalytics:
    def __init__(self):
        self.is_running = False
        self.scheduler_thread = None

    def _with_log_db_cursor(self, operation):
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        if not connection:
            return None
            
        try:
            from pymysql.cursors import DictCursor
            cursor = connection.cursor(DictCursor)
            return operation(cursor)
        finally:
            if 'cursor' in locals():
                cursor.close()
            log_db_pool.release_connection(connection)

    def _table_exists(self, cursor, table_name):
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM information_schema.tables 
            WHERE table_schema = DATABASE() 
            AND table_name = %s
        """, (table_name,))
        result = cursor.fetchone()
        return result and result['count'] > 0

    def _format_date(self, date_obj, format_type='display'):
        formats = {
            'display': '%Y-%m-%d',
            'table': '%Y%m%d',
            'iso': lambda d: d.isoformat()
        }
        fmt = formats.get(format_type, formats['display'])
        return fmt(date_obj) if callable(fmt) else date_obj.strftime(fmt)



    def start_scheduler(self):
        if self.is_running:
            return
            
        self.is_running = True
        schedule.clear()
        schedule.every().day.at("00:10").do(self._daily_dau_task)
        schedule.every().day.at("01:00").do(self._daily_id_cleanup_task)
        
        self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.scheduler_thread.start()

    def stop_scheduler(self):
        self.is_running = False
        schedule.clear()

    def _run_scheduler(self):
        while self.is_running:
            if schedule.jobs:
                schedule.run_pending()
            time.sleep(60)

    def _daily_dau_task(self):
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        
        # 直接收集并保存数据，不检查是否已存在
        # 数据库会保留原有的加群等事件数据，只更新消息统计
        dau_data = self.collect_dau_data(yesterday)
        if dau_data:
            self.save_dau_data(dau_data, yesterday)
            logger.info(f"DAU数据已生成: {self._format_date(yesterday)}")
        else:
            logger.warning(f"DAU数据生成失败: {self._format_date(yesterday)}")

    def _daily_id_cleanup_task(self):
        from function.log_db import cleanup_yesterday_ids
        success = cleanup_yesterday_ids()
        
        if success:
            yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            logger.info(f"ID清理任务完成: {yesterday}")
        else:
            logger.warning("ID清理任务执行失败")

    def collect_dau_data(self, target_date: datetime.datetime) -> Optional[Dict[str, Any]]:
        date_str = self._format_date(target_date, 'table')
        display_date = self._format_date(target_date)
        
        message_stats = self._get_message_stats(date_str)
        if not message_stats:
            return None
            
        user_stats = self._get_user_stats()
        command_stats = self._get_command_stats(date_str)
        
        return {
            'date': display_date,
            'date_str': date_str,
            'generated_at': self._format_date(datetime.datetime.now(), 'iso'),
            'message_stats': message_stats,
            'user_stats': user_stats,
            'command_stats': command_stats,
            'version': '1.0'
        }

    def _get_message_stats(self, date_str: str) -> Optional[Dict[str, Any]]:
        def get_stats(cursor):
            table_name = f"Mlog_{date_str}_message"
            
            if not self._table_exists(cursor, table_name):
                return None
            
            queries = {
                'total_messages': f"SELECT COUNT(*) as count FROM {table_name}",
                'active_users': f"""SELECT COUNT(DISTINCT user_id) as count 
                                    FROM {table_name} 
                                    WHERE user_id IS NOT NULL AND user_id != ''""",
                'active_groups': f"""SELECT COUNT(DISTINCT group_id) as count 
                                     FROM {table_name} 
                                     WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''""",
                'private_messages': f"""SELECT COUNT(*) as count 
                                        FROM {table_name} 
                                        WHERE group_id = 'c2c'""",
                'peak_hour': f"""SELECT HOUR(timestamp) as hour, COUNT(*) as count 
                                 FROM {table_name} 
                                 GROUP BY HOUR(timestamp) 
                                 ORDER BY count DESC 
                                 LIMIT 1""",
                'top_groups': f"""SELECT group_id, COUNT(*) as msg_count 
                                  FROM {table_name} 
                                  WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''
                                  GROUP BY group_id 
                                  ORDER BY msg_count DESC 
                                  LIMIT 10""",
                'top_users': f"""SELECT user_id, COUNT(*) as msg_count 
                                 FROM {table_name} 
                                 WHERE user_id IS NOT NULL AND user_id != ''
                                 GROUP BY user_id 
                                 ORDER BY msg_count DESC 
                                 LIMIT 10"""
            }
            
            results = {}
            for key, query in queries.items():
                cursor.execute(query)
                if key in ['top_groups', 'top_users']:
                    results[key] = cursor.fetchall()
                else:
                    result = cursor.fetchone()
                    if key == 'peak_hour':
                        results['peak_hour'] = result['hour'] if result else 0
                        results['peak_hour_count'] = result['count'] if result else 0
                    else:
                        results[key] = result['count'] if result else 0
            
            results['top_groups'] = [
                {'group_id': g['group_id'], 'message_count': g['msg_count']} 
                for g in results['top_groups']
            ]
            results['top_users'] = [
                {'user_id': u['user_id'], 'message_count': u['msg_count']} 
                for u in results['top_users']
            ]
            
            return results
        
        return self._with_log_db_cursor(get_stats)

    def _get_user_stats(self) -> Dict[str, Any]:
        queries = [
            (f"SELECT COUNT(*) as count FROM {USERS_TABLE}", None, False),
            (f"SELECT COUNT(*) as count FROM {GROUPS_TABLE}", None, False),
            (f"SELECT COUNT(*) as count FROM {MEMBERS_TABLE}", None, False),
            (f"""SELECT group_id, JSON_LENGTH(users) as member_count
                FROM {GROUPS_USERS_TABLE}
                ORDER BY member_count DESC
                LIMIT 3""", None, True)
        ]
        
        results = DatabaseService.execute_concurrent_queries(queries)
        
        top_large_groups = []
        if results[3] and isinstance(results[3], list):
            top_large_groups = [
                {'group_id': g.get('group_id', ''), 'member_count': g.get('member_count', 0)}
                for g in results[3]
            ]
                
        return {
            'total_users': results[0]['count'] if results[0] else 0,
            'total_groups': results[1]['count'] if results[1] else 0,
            'total_friends': results[2]['count'] if results[2] else 0,
            'top_large_groups': top_large_groups
        }

    def _get_command_stats(self, date_str: str) -> List[Dict[str, Any]]:
        def get_stats(cursor):
            table_name = f"Mlog_{date_str}_message"
            
            if not self._table_exists(cursor, table_name):
                return []
                
            loaded_commands = self._get_loaded_plugin_commands()
            
            cursor.execute(f"""
                SELECT content 
                FROM {table_name} 
                WHERE content IS NOT NULL AND content != ''
            """)
            contents = cursor.fetchall()
            
            command_counts = {}
            for row in contents:
                content = row.get('content', '').strip()
                if content:
                    matched_command = self._match_content_to_plugin(content, loaded_commands)
                    if matched_command:
                        command_counts[matched_command] = command_counts.get(matched_command, 0) + 1
                        
            command_stats = [
                {'command': cmd, 'count': count} 
                for cmd, count in command_counts.items()
            ]
            command_stats.sort(key=lambda x: x['count'], reverse=True)
            
            return command_stats[:3]
        
        return self._with_log_db_cursor(get_stats) or []

    def save_dau_data(self, dau_data: Dict[str, Any], target_date: datetime.datetime):
        from function.log_db import save_complete_dau_data
        
        date_str = self._format_date(target_date)
        success = save_complete_dau_data(dau_data)
        
        if success:
            logger.info(f"DAU数据已保存到数据库: {date_str}")
        else:
            logger.error(f"DAU数据保存失败: {date_str}")

    def load_dau_data(self, target_date: datetime.datetime) -> Optional[Dict[str, Any]]:
        date_str = self._format_date(target_date)
        
        def parse_json_field(field, default):
            if field and isinstance(field, str):
                try:
                    return json.loads(field)
                except:
                    return default
            return field or default
        
        def get_dau_data(cursor):
            table_name = "Mlog_dau"
            
            if not self._table_exists(cursor, table_name):
                return None
            
            cursor.execute(f"""
                SELECT * FROM {table_name} 
                WHERE date = %s
            """, (date_str,))
            
            result = cursor.fetchone()
            if not result:
                return None
            
            message_stats_detail = parse_json_field(result.get('message_stats_detail'), {})
            user_stats_detail = parse_json_field(result.get('user_stats_detail'), {})
            command_stats_detail = parse_json_field(result.get('command_stats_detail'), [])

            return {
                'date': date_str,
                'generated_at': result.get('updated_at', '').isoformat() if result.get('updated_at') else '',
                'message_stats': message_stats_detail if message_stats_detail else {
                    'active_users': result.get('active_users', 0),
                    'active_groups': result.get('active_groups', 0),
                    'total_messages': result.get('total_messages', 0),
                    'private_messages': result.get('private_messages', 0)
                },
                'user_stats': user_stats_detail,
                'command_stats': command_stats_detail,
                'event_stats': {
                    'group_join_count': result.get('group_join_count', 0),
                    'group_leave_count': result.get('group_leave_count', 0),
                    'group_count_change': result.get('group_count_change', 0),
                    'friend_add_count': result.get('friend_add_count', 0),
                    'friend_remove_count': result.get('friend_remove_count', 0),
                    'friend_count_change': result.get('friend_count_change', 0)
                },
                'version': '2.0'
            }
        
        dau_data = self._with_log_db_cursor(get_dau_data)
        if dau_data:
            return dau_data
        
        return None

    def get_recent_dau_data(self, days: int = 7) -> List[Dict[str, Any]]:
        def parse_json_field(field, default):
            if field and isinstance(field, str):
                try:
                    return json.loads(field)
                except:
                    return default
            return field or default
        
        def get_recent_data(cursor):
            table_name = "Mlog_dau"
            
            if not self._table_exists(cursor, table_name):
                return []
            
            cursor.execute(f"""
                SELECT * FROM {table_name} 
                ORDER BY date DESC 
                LIMIT %s
            """, (days,))
            
            results = []
            for row in cursor.fetchall():
                message_stats_detail = parse_json_field(row.get('message_stats_detail'), {})
                user_stats_detail = parse_json_field(row.get('user_stats_detail'), {})
                command_stats_detail = parse_json_field(row.get('command_stats_detail'), [])
                
                results.append({
                    'date': row['date'].strftime('%Y-%m-%d'),
                    'generated_at': row.get('updated_at', '').isoformat() if row.get('updated_at') else '',
                    'message_stats': message_stats_detail if message_stats_detail else {
                        'active_users': row.get('active_users', 0),
                        'active_groups': row.get('active_groups', 0),
                        'total_messages': row.get('total_messages', 0),
                        'private_messages': row.get('private_messages', 0)
                    },
                    'user_stats': user_stats_detail,
                    'command_stats': command_stats_detail,
                    'event_stats': {
                        'group_join_count': row.get('group_join_count', 0),
                        'group_leave_count': row.get('group_leave_count', 0),
                        'group_count_change': row.get('group_count_change', 0),
                        'friend_add_count': row.get('friend_add_count', 0),
                        'friend_remove_count': row.get('friend_remove_count', 0),
                        'friend_count_change': row.get('friend_count_change', 0)
                    },
                    'version': '2.0'
                })
            return results
        
        db_results = self._with_log_db_cursor(get_recent_data)
        if db_results:
            return db_results
        
        results = []
        today = datetime.datetime.now()
        
        for i in range(days):
            target_date = today - datetime.timedelta(days=i+1)
            dau_data = self.load_dau_data(target_date)
            if dau_data:
                results.append(dau_data)
                
        return results

    def manual_generate_dau(self, target_date: datetime.datetime) -> bool:
        dau_data = self.collect_dau_data(target_date)
        if dau_data:
            self.save_dau_data(dau_data, target_date)
            return True
        return False

    def generate_all_dau_data(self, start_date: datetime.datetime = None, end_date: datetime.datetime = None) -> Dict[str, Any]:
        if not start_date:
            start_date = datetime.datetime.now() - datetime.timedelta(days=30)
        if not end_date:
            end_date = datetime.datetime.now() - datetime.timedelta(days=1)
            
        if start_date > end_date:
            start_date, end_date = end_date, start_date
            
        total_days = 0
        generated_days = 0
        skipped_days = 0
        failed_days = 0
        generated_dates = []
        skipped_dates = []
        failed_dates = []
        
        current_date = start_date
        while current_date <= end_date:
            total_days += 1
            date_str = self._format_date(current_date)
            
            existing_data = self.load_dau_data(current_date)
            if existing_data:
                skipped_days += 1
                skipped_dates.append(date_str)
            else:
                dau_data = self.collect_dau_data(current_date)
                
                if dau_data:
                    self.save_dau_data(dau_data, current_date)
                    generated_days += 1
                    generated_dates.append(date_str)
                else:
                    failed_days += 1
                    failed_dates.append(date_str)
                    
            current_date += datetime.timedelta(days=1)
            
        return {
            'total_days': total_days,
            'generated_days': generated_days,
            'skipped_days': skipped_days,
            'failed_days': failed_days,
            'generated_dates': generated_dates,
            'skipped_dates': skipped_dates,
            'failed_dates': failed_dates,
            'start_date': self._format_date(start_date),
            'end_date': self._format_date(end_date)
        }

    def _get_loaded_plugin_commands(self) -> Dict[str, str]:
        if not _plugin_manager_available:
            return {}
            
        try:
            loaded_commands = {}
            
            if hasattr(PluginManager, '_regex_handlers'):
                for pattern, handler_info in PluginManager._regex_handlers.items():
                    if isinstance(handler_info, dict):
                        plugin_class = handler_info.get('class')
                        if plugin_class:
                            plugin_name = plugin_class.__name__
                            simplified_pattern = self._simplify_regex_pattern(pattern)
                            if simplified_pattern:
                                loaded_commands[simplified_pattern] = plugin_name
            
            return loaded_commands
        except:
            return {}

    def _simplify_regex_pattern(self, pattern: str) -> str:
        try:
            pattern = pattern.lstrip('^').rstrip('$')
            
            if '|' in pattern:
                pattern = pattern.split('|')[0]
            
            pattern = re.sub(r'\\[swn][\*\+\?]?', '', pattern)
            pattern = re.sub(r'[\(\)\[\]\{\}\*\+\?\.\$\^]', '', pattern)
            pattern = pattern.replace('\\', '')
            
            pattern = re.sub(r'[^\u4e00-\u9fff\w]', '', pattern)
            
            if len(pattern) > 20:
                pattern = pattern[:20]
            
            return pattern.strip() if pattern.strip() and len(pattern.strip()) > 0 else None
        except:
            return None

    def _match_content_to_plugin(self, content: str, loaded_commands: Dict[str, str]) -> str:
        if not content or not loaded_commands:
            return None
            
        clean_content = content.strip()
        if clean_content.startswith('/'):
            clean_content = clean_content[1:].strip()
        
        for command_pattern, plugin_name in loaded_commands.items():
            if not command_pattern:
                continue
                
            if (clean_content.lower() == command_pattern.lower() or
                clean_content.lower().startswith(command_pattern.lower() + ' ') or
                clean_content.lower().startswith(command_pattern.lower())):
                return command_pattern
        
        return None


# 全局DAU分析器实例
dau_analytics = DAUAnalytics()

def start_dau_analytics():
    dau_analytics.start_scheduler()
    
def stop_dau_analytics():
    dau_analytics.stop_scheduler()
    
def get_dau_analytics() -> DAUAnalytics:
    return dau_analytics