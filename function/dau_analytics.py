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
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache, partial
from collections import defaultdict
from function.log_db import LogDatabasePool
from function.db_pool import DatabaseService
from function.database import USERS_TABLE, GROUPS_TABLE, MEMBERS_TABLE, GROUPS_USERS_TABLE
from config import DB_CONFIG, LOG_DB_CONFIG

try:
    from core.plugin.PluginManager import PluginManager
    _plugin_manager_available = True
except ImportError:
    _plugin_manager_available = False

logger = logging.getLogger('dau_analytics')

# 配置常量
BATCH_SIZE = 1000
MAX_WORKERS = 4
QUERY_TIMEOUT = 30
CACHE_TTL = 300  # 5分钟缓存

class DAUAnalytics:
    def __init__(self):
        self.is_running = False
        self.scheduler_thread = None
        # 获取当前框架的表前缀配置
        self.log_table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
        self.main_table_prefix = DB_CONFIG.get('table_prefix', 'M_')
        
        # 组件
        self._thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="DAU")
        self._query_cache = {}
        self._cache_timestamps = {}
        
        # 预编译命令模式缓存
        self._compiled_commands = None
        self._commands_cache_time = 0

    def _with_log_db_cursor(self, operation, timeout=QUERY_TIMEOUT):
        """数据库操作，支持超时和错误处理"""
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        if not connection:
            return None
            
        cursor = None
        try:
            from pymysql.cursors import DictCursor
            cursor = connection.cursor(DictCursor)
            
            # 设置查询超时
            if timeout:
                cursor.execute(f"SET SESSION wait_timeout = {timeout}")
                cursor.execute(f"SET SESSION interactive_timeout = {timeout}")
            
            result = operation(cursor)
            return result
        except Exception as e:
            logger.error(f"数据库操作失败: {str(e)}")
            return None
        finally:
            if cursor:
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
    
    def _get_cache_key(self, *args) -> str:
        """生成缓存键"""
        return "|".join(str(arg) for arg in args)
    
    def _get_cached_result(self, cache_key: str) -> Optional[Any]:
        """获取缓存结果"""
        if cache_key not in self._query_cache:
            return None
            
        cache_time = self._cache_timestamps.get(cache_key, 0)
        if time.time() - cache_time > CACHE_TTL:
            # 缓存过期
            self._query_cache.pop(cache_key, None)
            self._cache_timestamps.pop(cache_key, None)
            return None
            
        return self._query_cache[cache_key]
    
    def _set_cache_result(self, cache_key: str, result: Any):
        """设置缓存结果"""
        self._query_cache[cache_key] = result
        self._cache_timestamps[cache_key] = time.time()

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
        
        # 清理资源
        if hasattr(self, '_thread_pool'):
            self._thread_pool.shutdown(wait=True)
        
        # 清理缓存
        self._query_cache.clear()
        self._cache_timestamps.clear()

    def _run_scheduler(self):
        while self.is_running:
            if schedule.jobs:
                schedule.run_pending()
            time.sleep(60)

    def _daily_dau_task(self):
        """每日DAU任务"""
        yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
        
        try:
            logger.info(f"开始生成DAU数据: {self._format_date(yesterday)}")
            
            # 直接收集并保存数据，不检查是否已存在
            # 数据库会保留原有的加群等事件数据，只更新消息统计
            dau_data = self.collect_dau_data(yesterday)
            if dau_data:
                self.save_dau_data(dau_data, yesterday)
                logger.info(f"DAU数据已生成: {self._format_date(yesterday)}")
            else:
                logger.warning(f"DAU数据生成失败: {self._format_date(yesterday)}")
                
        except Exception as e:
            logger.error(f"DAU任务执行异常: {str(e)}")
            
        # 清理缓存
        self._clear_expired_cache()
    
    def _clear_expired_cache(self):
        """清理过期缓存"""
        current_time = time.time()
        expired_keys = [
            key for key, timestamp in self._cache_timestamps.items()
            if current_time - timestamp > CACHE_TTL
        ]
        for key in expired_keys:
            self._query_cache.pop(key, None)
            self._cache_timestamps.pop(key, None)

    def _daily_id_cleanup_task(self):
        from function.log_db import cleanup_yesterday_ids
        success = cleanup_yesterday_ids()
        
        if success:
            yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            logger.info(f"ID清理任务完成: {yesterday}")
        else:
            logger.warning("ID清理任务执行失败")

    def collect_dau_data(self, target_date: datetime.datetime) -> Optional[Dict[str, Any]]:
        """并发收集DAU数据，大幅提升性能"""
        date_str = self._format_date(target_date, 'table')
        display_date = self._format_date(target_date)
        
        logger.info(f"开始并发收集DAU数据: {display_date}")
        
        # 使用线程池并发执行各种统计任务
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="DAUCollect") as executor:
            # 提交所有任务
            future_message = executor.submit(self._get_message_stats, date_str)
            future_user = executor.submit(self._get_user_stats)
            future_command = executor.submit(self._get_command_stats, date_str)
            
            # 等待所有任务完成
            try:
                message_stats = future_message.result(timeout=60)  # 60秒超时
                user_stats = future_user.result(timeout=30)        # 30秒超时
                command_stats = future_command.result(timeout=45)  # 45秒超时
                
            except Exception as e:
                logger.error(f"并发数据收集失败: {str(e)}")
                return None
        
        # 检查关键数据
        if not message_stats:
            logger.warning(f"消息统计数据为空: {display_date}")
            return None
        
        logger.info(f"DAU数据收集完成: {display_date} - 消息数: {message_stats.get('total_messages', 0)}")
        
        return {
            'date': display_date,
            'date_str': date_str,
            'generated_at': self._format_date(datetime.datetime.now(), 'iso'),
            'message_stats': message_stats,
            'user_stats': user_stats or {},
            'command_stats': command_stats or [],
            'version': '2.0'
        }

    def _get_message_stats(self, date_str: str) -> Optional[Dict[str, Any]]:
        """获取消息统计"""
        cache_key = self._get_cache_key('message_stats', date_str)
        cached_result = self._get_cached_result(cache_key)
        if cached_result:
            return cached_result
        
        def check_table_exists(cursor):
            table_name = f"{self.log_table_prefix}{date_str}_message"
            return self._table_exists(cursor, table_name), table_name
        
        exists_result = self._with_log_db_cursor(check_table_exists)
        if not exists_result or not isinstance(exists_result, (tuple, list)) or len(exists_result) < 2:
            return None
            
        if not exists_result[0]:
            return None
            
        table_name = exists_result[1]
        
        optimized_queries = {
            # 基础统计查询 - 一次性获取多个计数
            'basic_stats': f"""
                SELECT 
                    COUNT(*) as total_messages,
                    COUNT(DISTINCT CASE WHEN user_id IS NOT NULL AND user_id != '' THEN user_id END) as active_users,
                    COUNT(DISTINCT CASE WHEN group_id != 'c2c' AND group_id IS NOT NULL AND group_id != '' THEN group_id END) as active_groups,
                    COUNT(CASE WHEN group_id = 'c2c' THEN 1 END) as private_messages
                FROM {table_name}
            """,
            
            # 高峰时段统计
            'peak_hour': f"""
                SELECT HOUR(timestamp) as hour, COUNT(*) as count 
                FROM {table_name} 
                GROUP BY HOUR(timestamp) 
                ORDER BY count DESC 
                LIMIT 1
            """,
            
            # 顶部群组统计
            'top_groups': f"""
                SELECT group_id, COUNT(*) as msg_count 
                FROM {table_name} 
                WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''
                GROUP BY group_id 
                ORDER BY msg_count DESC 
                LIMIT 10
            """,
            
            # 顶部用户统计
            'top_users': f"""
                SELECT user_id, COUNT(*) as msg_count 
                FROM {table_name} 
                WHERE user_id IS NOT NULL AND user_id != ''
                GROUP BY user_id 
                ORDER BY msg_count DESC 
                LIMIT 10
            """
        }
        
        def execute_concurrent_queries(cursor):
            """并发执行所有查询"""
            results = {}
            
            try:
                # 执行基础统计查询
                cursor.execute(optimized_queries['basic_stats'])
                basic_result = cursor.fetchone()
                if basic_result:
                    results.update(basic_result)
                
                # 执行高峰时段查询
                cursor.execute(optimized_queries['peak_hour'])
                peak_result = cursor.fetchone()
                if peak_result:
                    results['peak_hour'] = peak_result['hour']
                    results['peak_hour_count'] = peak_result['count']
                else:
                    results['peak_hour'] = 0
                    results['peak_hour_count'] = 0
                
                # 执行顶部群组查询
                cursor.execute(optimized_queries['top_groups'])
                top_groups = cursor.fetchall()
                results['top_groups'] = [
                    {'group_id': g['group_id'], 'message_count': g['msg_count']} 
                    for g in (top_groups or [])
                ]
                
                # 执行顶部用户查询
                cursor.execute(optimized_queries['top_users'])
                top_users = cursor.fetchall()
                results['top_users'] = [
                    {'user_id': u['user_id'], 'message_count': u['msg_count']} 
                    for u in (top_users or [])
                ]

                return results
                
            except Exception as e:
                logger.error(f"消息统计查询失败: {str(e)}")
                return None
        
        result = self._with_log_db_cursor(execute_concurrent_queries)
        if result:
            self._set_cache_result(cache_key, result)
        
        return result

    def _get_user_stats(self) -> Dict[str, Any]:
        # 确保使用当前框架的表前缀
        users_table = f"{self.main_table_prefix}users"
        groups_table = f"{self.main_table_prefix}groups"
        members_table = f"{self.main_table_prefix}members"
        groups_users_table = f"{self.main_table_prefix}groups_users"
        
        queries = [
            (f"SELECT COUNT(*) as count FROM {users_table}", None, False),
            (f"SELECT COUNT(*) as count FROM {groups_table}", None, False),
            (f"SELECT COUNT(*) as count FROM {members_table}", None, False),
            (f"""SELECT group_id, 
                        GREATEST(1, ROUND((CHAR_LENGTH(users) - 2) / 25)) as member_count
                FROM {groups_users_table}
                WHERE users IS NOT NULL AND users != '[]'
                ORDER BY member_count DESC
                LIMIT 3""", None, True)
        ]
        
        results = DatabaseService.execute_concurrent_queries(queries)
        
        if not results or not isinstance(results, list):
            results = [None, None, None, None]
        elif len(results) < 4:
            results.extend([None] * (4 - len(results)))
        
        top_large_groups = []
        if len(results) > 3 and results[3] and isinstance(results[3], list):
            top_large_groups = [
                {'group_id': g.get('group_id', ''), 'member_count': g.get('member_count', 0)}
                for g in results[3]
            ]
                
        return {
            'total_users': results[0]['count'] if results[0] and isinstance(results[0], dict) else 0,
            'total_groups': results[1]['count'] if results[1] and isinstance(results[1], dict) else 0,
            'total_friends': results[2]['count'] if results[2] and isinstance(results[2], dict) else 0,
            'top_large_groups': top_large_groups
        }

    def _get_command_stats(self, date_str: str) -> List[Dict[str, Any]]:
        """获取命令统计"""
        cache_key = self._get_cache_key('command_stats', date_str)
        cached_result = self._get_cached_result(cache_key)
        if cached_result:
            return cached_result
        
        def get_optimized_stats(cursor):
            table_name = f"{self.log_table_prefix}{date_str}_message"
            
            if not self._table_exists(cursor, table_name):
                return []
            
            # 获取并缓存编译后的命令模式
            compiled_commands = self._get_compiled_commands()
            if not compiled_commands:
                return []
            
            # 添加初步过滤条件来减少需要处理的数据量
            cursor.execute(f"""
                SELECT content, COUNT(*) as count
                FROM {table_name} 
                WHERE content IS NOT NULL 
                  AND content != '' 
                  AND (content LIKE '/%' OR content LIKE '%签到%' OR content LIKE '%查%' OR LENGTH(content) < 50)
                GROUP BY content
                HAVING count > 0
                ORDER BY count DESC
                LIMIT 1000
            """)
            
            content_counts = cursor.fetchall()
            command_counts = defaultdict(int)
            
            # 使用编译后的正则表达式进行高效匹配
            for row in content_counts:
                content = row.get('content', '').strip()
                count = row.get('count', 1)
                
                if content:
                    matched_command = self._match_content_to_plugin_fast(content, compiled_commands)
                    if matched_command:
                        command_counts[matched_command] += count

            # 返回前5个最热门命令
            command_stats = [
                {'command': cmd, 'count': count} 
                for cmd, count in sorted(command_counts.items(), key=lambda x: x[1], reverse=True)[:5]
            ]
            
            return command_stats
        
        result = self._with_log_db_cursor(get_optimized_stats) or []
        if result:
            self._set_cache_result(cache_key, result)
        
        return result
    
    @lru_cache(maxsize=1)
    def _get_compiled_commands(self) -> Dict[str, Tuple[str, Any]]:
        """获取预编译的命令模式，使用LRU缓存"""
        current_time = time.time()
        
        # 检查缓存是否需要刷新（每5分钟刷新一次）
        if (self._compiled_commands is None or 
            current_time - self._commands_cache_time > 300):
            
            self._compiled_commands = {}
            loaded_commands = self._get_loaded_plugin_commands()
            
            for pattern, plugin_name in loaded_commands.items():
                try:
                    # 编译正则表达式以提高匹配速度
                    compiled_pattern = re.compile(pattern, re.IGNORECASE)
                    self._compiled_commands[pattern] = (plugin_name, compiled_pattern)
                except re.error:
                    # 如果正则编译失败，使用简单字符串匹配
                    self._compiled_commands[pattern] = (plugin_name, None)
            
            self._commands_cache_time = current_time
        
        return self._compiled_commands
    
    def _match_content_to_plugin_fast(self, content: str, compiled_commands: Dict[str, Tuple[str, Any]]) -> Optional[str]:
        """使用预编译正则的快速命令匹配"""
        if not content or not compiled_commands:
            return None
        
        clean_content = content.strip()
        if clean_content.startswith('/'):
            clean_content = clean_content[1:].strip()
        
        # 快速退出：如果内容太长，可能不是命令
        if len(clean_content) > 100:
            return None
        
        for pattern, (plugin_name, compiled_regex) in compiled_commands.items():
            try:
                if compiled_regex:
                    # 使用预编译的正则表达式
                    if compiled_regex.match(clean_content):
                        return pattern
                else:
                    # 使用简单字符串匹配作为备选
                    if (clean_content.lower() == pattern.lower() or
                        clean_content.lower().startswith(pattern.lower() + ' ')):
                        return pattern
            except Exception:
                continue
        
        return None

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
            table_name = f"{self.log_table_prefix}dau"
            
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
            table_name = f"{self.log_table_prefix}dau"
            
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