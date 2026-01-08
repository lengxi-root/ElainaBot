#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json, time, datetime, logging, threading, schedule, re
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from function.log_db import LogDatabasePool
from config import LOG_DB_CONFIG

try:
    from core.plugin.PluginManager import PluginManager
    _plugin_manager_available = True
except:
    _plugin_manager_available = False

logger = logging.getLogger('ElainaBot.function.dau_analytics')

BATCH_SIZE = 1000
MAX_WORKERS = 4
QUERY_TIMEOUT = 30
CACHE_TTL = 300
_COMMAND_CACHE_TTL = 300
_ONE_DAY = datetime.timedelta(days=1)

_SIMPLIFY_PATTERNS = (
    (re.compile(r'\\[swn][\*\+\?]?'), ''),
    (re.compile(r'[\(\)\[\]\{\}\*\+\?\.\$\^]'), ''),
    (re.compile(r'[^\u4e00-\u9fff\w]'), ''),
)

_DATE_FORMATS = {'display': '%Y-%m-%d', 'table': '%Y%m%d'}
_TABLE_EXISTS_SQL = "SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s"

class DAUAnalytics:
    __slots__ = ('is_running', 'scheduler_thread', 'log_table_prefix', '_thread_pool', 
                 '_query_cache', '_cache_timestamps', '_compiled_commands', '_commands_cache_time',
                 '_dau_table', '_users_table', '_groups_users_table', '_members_table')
    
    def __init__(self):
        self.is_running = False
        self.scheduler_thread = None
        self.log_table_prefix = LOG_DB_CONFIG.get('table_prefix', 'Mlog_')
        self._thread_pool = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="DAU")
        self._query_cache = {}
        self._cache_timestamps = {}
        self._compiled_commands = None
        self._commands_cache_time = 0
        self._dau_table = f"{self.log_table_prefix}dau"
        self._users_table = f"{self.log_table_prefix}users"
        self._groups_users_table = f"{self.log_table_prefix}groups_users"
        self._members_table = f"{self.log_table_prefix}members"

    def _with_log_db_cursor(self, operation, timeout=QUERY_TIMEOUT):
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        if not connection:
            return None
        cursor = None
        try:
            from pymysql.cursors import DictCursor
            cursor = connection.cursor(DictCursor)
            if timeout:
                cursor.execute(f"SET SESSION wait_timeout = {timeout}")
                cursor.execute(f"SET SESSION interactive_timeout = {timeout}")
            return operation(cursor)
        except:
            return None
        finally:
            if cursor:
                cursor.close()
            log_db_pool.release_connection(connection)

    def _table_exists(self, cursor, table_name):
        cursor.execute(_TABLE_EXISTS_SQL, (table_name,))
        result = cursor.fetchone()
        return result and result['count'] > 0

    def _format_date(self, date_obj, format_type='display'):
        if format_type == 'iso':
            return date_obj.isoformat()
        return date_obj.strftime(_DATE_FORMATS.get(format_type, _DATE_FORMATS['display']))
    
    def _get_cached_result(self, cache_key: str) -> Optional[Any]:
        if cache_key not in self._query_cache:
            return None
        if time.time() - self._cache_timestamps.get(cache_key, 0) > CACHE_TTL:
            self._query_cache.pop(cache_key, None)
            self._cache_timestamps.pop(cache_key, None)
            return None
        return self._query_cache[cache_key]
    
    def _set_cache_result(self, cache_key: str, result: Any):
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
        if hasattr(self, '_thread_pool'):
            self._thread_pool.shutdown(wait=True)
        self._query_cache.clear()
        self._cache_timestamps.clear()

    def _run_scheduler(self):
        while self.is_running:
            if schedule.jobs:
                schedule.run_pending()
            time.sleep(60)

    def _daily_dau_task(self):
        yesterday = datetime.datetime.now() - _ONE_DAY
        try:
            dau_data = self.collect_dau_data(yesterday)
            if dau_data:
                self.save_dau_data(dau_data, yesterday)
        except:
            pass
        self._clear_expired_cache()
    
    def _clear_expired_cache(self):
        current_time = time.time()
        expired_keys = [k for k, t in self._cache_timestamps.items() if current_time - t > CACHE_TTL]
        for key in expired_keys:
            self._query_cache.pop(key, None)
            self._cache_timestamps.pop(key, None)

    def _daily_id_cleanup_task(self):
        from function.log_db import cleanup_old_ids
        cleanup_old_ids()

    def collect_dau_data(self, target_date: datetime.datetime) -> Optional[Dict[str, Any]]:
        date_str = self._format_date(target_date, 'table')
        display_date = self._format_date(target_date)
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="DAUCollect") as executor:
            future_message = executor.submit(self._get_message_stats, date_str)
            future_user = executor.submit(self._get_user_stats)
            future_command = executor.submit(self._get_command_stats, date_str)
            try:
                message_stats = future_message.result(timeout=60)
                user_stats = future_user.result(timeout=30)
                command_stats = future_command.result(timeout=45)
            except:
                return None
        if not message_stats:
            return None
        return {
            'date': display_date, 'date_str': date_str,
            'generated_at': self._format_date(datetime.datetime.now(), 'iso'),
            'message_stats': message_stats, 'user_stats': user_stats or {},
            'command_stats': command_stats or [], 'version': '2.0'
        }

    def _get_message_stats(self, date_str: str) -> Optional[Dict[str, Any]]:
        cache_key = f"message_stats|{date_str}"
        cached_result = self._get_cached_result(cache_key)
        if cached_result:
            return cached_result
        
        table_name = f"{self.log_table_prefix}{date_str}_message"
        
        def execute_queries(cursor):
            if not self._table_exists(cursor, table_name):
                return None
            results = {}
            cursor.execute(f"SELECT COUNT(*) as total_messages, COUNT(DISTINCT CASE WHEN user_id IS NOT NULL AND user_id != '' THEN user_id END) as active_users, COUNT(DISTINCT CASE WHEN group_id != 'c2c' AND group_id IS NOT NULL AND group_id != '' THEN group_id END) as active_groups, COUNT(CASE WHEN group_id = 'c2c' THEN 1 END) as private_messages FROM {table_name}")
            basic_result = cursor.fetchone()
            if basic_result:
                results.update(basic_result)
            cursor.execute(f"SELECT HOUR(timestamp) as hour, COUNT(*) as count FROM {table_name} GROUP BY HOUR(timestamp) ORDER BY count DESC LIMIT 1")
            peak_result = cursor.fetchone()
            results['peak_hour'] = peak_result['hour'] if peak_result else 0
            results['peak_hour_count'] = peak_result['count'] if peak_result else 0
            cursor.execute(f"SELECT group_id, COUNT(*) as msg_count FROM {table_name} WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != '' GROUP BY group_id ORDER BY msg_count DESC LIMIT 10")
            results['top_groups'] = [{'group_id': g['group_id'], 'message_count': g['msg_count']} for g in (cursor.fetchall() or [])]
            cursor.execute(f"SELECT user_id, COUNT(*) as msg_count FROM {table_name} WHERE user_id IS NOT NULL AND user_id != '' GROUP BY user_id ORDER BY msg_count DESC LIMIT 10")
            results['top_users'] = [{'user_id': u['user_id'], 'message_count': u['msg_count']} for u in (cursor.fetchall() or [])]
            return results
        
        result = self._with_log_db_cursor(execute_queries)
        if result:
            self._set_cache_result(cache_key, result)
        return result

    def _get_user_stats(self) -> Dict[str, Any]:
        def execute_queries(cursor):
            cursor.execute(f"SELECT COUNT(*) as count FROM {self._users_table}")
            users = cursor.fetchone()
            cursor.execute(f"SELECT COUNT(*) as count FROM {self._groups_users_table}")
            groups = cursor.fetchone()
            cursor.execute(f"SELECT COUNT(*) as count FROM {self._members_table}")
            members = cursor.fetchone()
            cursor.execute(f"SELECT group_id, GREATEST(1, ROUND((CHAR_LENGTH(users) - 2) / 25)) as member_count FROM {self._groups_users_table} WHERE users IS NOT NULL AND users != '[]' ORDER BY member_count DESC LIMIT 3")
            top_groups = cursor.fetchall()
            return {
                'total_users': users['count'] if users else 0,
                'total_groups': groups['count'] if groups else 0,
                'total_friends': members['count'] if members else 0,
                'top_large_groups': [{'group_id': g.get('group_id', ''), 'member_count': g.get('member_count', 0)} for g in (top_groups or [])]
            }
        
        result = self._with_log_db_cursor(execute_queries)
        return result or {'total_users': 0, 'total_groups': 0, 'total_friends': 0, 'top_large_groups': []}

    def _get_command_stats(self, date_str: str) -> List[Dict[str, Any]]:
        cache_key = f"command_stats|{date_str}"
        cached_result = self._get_cached_result(cache_key)
        if cached_result:
            return cached_result
        
        table_name = f"{self.log_table_prefix}{date_str}_message"
        
        def get_stats(cursor):
            if not self._table_exists(cursor, table_name):
                return []
            compiled_commands = self._get_compiled_commands()
            if not compiled_commands:
                return []
            cursor.execute(f"SELECT content, COUNT(*) as count FROM {table_name} WHERE content IS NOT NULL AND content != '' AND (content LIKE '/%' OR content LIKE '%签到%' OR content LIKE '%查%' OR LENGTH(content) < 50) GROUP BY content HAVING count > 0 ORDER BY count DESC LIMIT 1000")
            command_counts = defaultdict(int)
            for row in cursor.fetchall():
                content = row.get('content', '').strip()
                if content:
                    matched = self._match_content_to_plugin_fast(content, compiled_commands)
                    if matched:
                        command_counts[matched] += row.get('count', 1)
            return [{'command': cmd, 'count': cnt} for cmd, cnt in sorted(command_counts.items(), key=lambda x: x[1], reverse=True)[:5]]
        
        result = self._with_log_db_cursor(get_stats) or []
        if result:
            self._set_cache_result(cache_key, result)
        return result
    
    def _get_compiled_commands(self) -> Dict[str, Tuple[str, Any]]:
        current_time = time.time()
        if self._compiled_commands is None or current_time - self._commands_cache_time > _COMMAND_CACHE_TTL:
            self._compiled_commands = {}
            for pattern, handler_info in getattr(PluginManager, '_regex_handlers', {}).items():
                if isinstance(handler_info, dict):
                    plugin_class = handler_info.get('class')
                    if plugin_class:
                        simplified = self._simplify_regex_pattern(pattern)
                        if simplified:
                            try:
                                self._compiled_commands[simplified] = (plugin_class.__name__, re.compile(simplified, re.IGNORECASE))
                            except:
                                self._compiled_commands[simplified] = (plugin_class.__name__, None)
            self._commands_cache_time = current_time
        return self._compiled_commands
    
    def _match_content_to_plugin_fast(self, content: str, compiled_commands: Dict[str, Tuple[str, Any]]) -> Optional[str]:
        if not content or not compiled_commands:
            return None
        clean = content.strip()
        if clean.startswith('/'):
            clean = clean[1:].strip()
        if len(clean) > 100:
            return None
        clean_lower = clean.lower()
        for pattern, (_, compiled_regex) in compiled_commands.items():
            try:
                if compiled_regex:
                    if compiled_regex.match(clean):
                        return pattern
                elif clean_lower == pattern.lower() or clean_lower.startswith(pattern.lower() + ' '):
                    return pattern
            except:
                continue
        return None

    def save_dau_data(self, dau_data: Dict[str, Any], target_date: datetime.datetime):
        from function.log_db import save_complete_dau_data
        save_complete_dau_data(dau_data)

    def _parse_json_field(self, field, default):
        if field and isinstance(field, str):
            try:
                return json.loads(field)
            except:
                return default
        return field or default

    def _build_dau_result(self, row, date_str=None):
        message_stats = self._parse_json_field(row.get('message_stats_detail'), {})
        user_stats = self._parse_json_field(row.get('user_stats_detail'), {})
        command_stats = self._parse_json_field(row.get('command_stats_detail'), [])
        date_val = date_str or (row['date'].strftime('%Y-%m-%d') if hasattr(row.get('date'), 'strftime') else str(row.get('date', '')))
        return {
            'date': date_val,
            'generated_at': row.get('updated_at', '').isoformat() if row.get('updated_at') else '',
            'message_stats': message_stats or {
                'active_users': row.get('active_users', 0), 'active_groups': row.get('active_groups', 0),
                'total_messages': row.get('total_messages', 0), 'private_messages': row.get('private_messages', 0)
            },
            'user_stats': user_stats, 'command_stats': command_stats,
            'event_stats': {
                'group_join_count': row.get('group_join_count', 0), 'group_leave_count': row.get('group_leave_count', 0),
                'group_count_change': row.get('group_count_change', 0), 'friend_add_count': row.get('friend_add_count', 0),
                'friend_remove_count': row.get('friend_remove_count', 0), 'friend_count_change': row.get('friend_count_change', 0)
            },
            'version': '2.0'
        }

    def load_dau_data(self, target_date: datetime.datetime) -> Optional[Dict[str, Any]]:
        date_str = self._format_date(target_date)
        def get_data(cursor):
            if not self._table_exists(cursor, self._dau_table):
                return None
            cursor.execute(f"SELECT * FROM {self._dau_table} WHERE date = %s", (date_str,))
            result = cursor.fetchone()
            return self._build_dau_result(result, date_str) if result else None
        return self._with_log_db_cursor(get_data)

    def get_recent_dau_data(self, days: int = 7) -> List[Dict[str, Any]]:
        def get_data(cursor):
            if not self._table_exists(cursor, self._dau_table):
                return []
            cursor.execute(f"SELECT * FROM {self._dau_table} ORDER BY date DESC LIMIT %s", (days,))
            return [self._build_dau_result(row) for row in cursor.fetchall()]
        
        result = self._with_log_db_cursor(get_data)
        if result:
            return result
        results = []
        today = datetime.datetime.now()
        for i in range(days):
            dau_data = self.load_dau_data(today - datetime.timedelta(days=i+1))
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
        now = datetime.datetime.now()
        if not start_date:
            start_date = now - datetime.timedelta(days=30)
        if not end_date:
            end_date = now - _ONE_DAY
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        total_days = generated_days = skipped_days = failed_days = 0
        generated_dates, skipped_dates, failed_dates = [], [], []
        current_date = start_date
        while current_date <= end_date:
            total_days += 1
            date_str = self._format_date(current_date)
            if self.load_dau_data(current_date):
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
            current_date += _ONE_DAY
        return {
            'total_days': total_days, 'generated_days': generated_days, 'skipped_days': skipped_days, 'failed_days': failed_days,
            'generated_dates': generated_dates, 'skipped_dates': skipped_dates, 'failed_dates': failed_dates,
            'start_date': self._format_date(start_date), 'end_date': self._format_date(end_date)
        }

    def _simplify_regex_pattern(self, pattern: str) -> Optional[str]:
        try:
            pattern = pattern.lstrip('^').rstrip('$')
            if '|' in pattern:
                pattern = pattern.split('|')[0]
            for compiled_pattern, replacement in _SIMPLIFY_PATTERNS:
                pattern = compiled_pattern.sub(replacement, pattern)
            pattern = pattern.replace('\\', '').strip()
            if len(pattern) > 20:
                pattern = pattern[:20]
            return pattern if pattern else None
        except:
            return None

dau_analytics = DAUAnalytics()

def start_dau_analytics():
    dau_analytics.start_scheduler()
    
def stop_dau_analytics():
    dau_analytics.stop_scheduler()
    
def get_dau_analytics() -> DAUAnalytics:
    return dau_analytics