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
import traceback
from typing import Dict, List, Any, Optional
from function.log_db import LogDatabasePool
from function.db_pool import DatabaseService

try:
    from core.plugin.PluginManager import PluginManager
    _plugin_manager_available = True
except ImportError:
    _plugin_manager_available = False

logger = logging.getLogger('dau_analytics')

class DAUAnalytics:
    """DAU数据分析和持久化组件"""
    
    def __init__(self):
        self.data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'dau')
        os.makedirs(self.data_dir, exist_ok=True)
        self.is_running = False
        self.scheduler_thread = None

    def _safe_execute(self, func, *args, **kwargs):
        """统一的安全执行和错误处理"""
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"操作失败: {e}")
            return None

    def _with_log_db_cursor(self, operation):
        """数据库连接和游标管理的上下文管理器"""
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            logger.error("无法连接到日志数据库")
            return None
            
        cursor = None
        try:
            from pymysql.cursors import DictCursor
            cursor = connection.cursor(DictCursor)
            return operation(cursor)
        except Exception as e:
            logger.error(f"数据库操作失败: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if connection:
                log_db_pool.release_connection(connection)

    def _table_exists(self, cursor, table_name):
        """检查表是否存在"""
        check_query = """
            SELECT COUNT(*) as count 
            FROM information_schema.tables 
            WHERE table_schema = DATABASE() 
            AND table_name = %s
        """
        cursor.execute(check_query, (table_name,))
        result = cursor.fetchone()
        return result and result['count'] > 0

    def _format_date(self, date_obj, format_type='display'):
        """统一的日期格式化"""
        formats = {
            'display': '%Y-%m-%d',
            'table': '%Y%m%d',
            'iso': lambda d: d.isoformat()
        }
        fmt = formats.get(format_type, formats['display'])
        return fmt(date_obj) if callable(fmt) else date_obj.strftime(fmt)

    def _file_operation(self, filepath, mode, operation, data=None):
        """统一的文件操作"""
        try:
            if mode == 'read':
                if not os.path.exists(filepath):
                    return None
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            elif mode == 'write':
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                return True
        except Exception as e:
            logger.error(f"文件操作失败: {e}")
            return None

    def start_scheduler(self):
        """启动定时任务调度器"""
        if self.is_running:
            return
            
        self.is_running = True
        schedule.clear()
        schedule.every().day.at("00:10").do(self._daily_dau_task)
        
        self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.scheduler_thread.start()

    def stop_scheduler(self):
        """停止定时任务调度器"""
        self.is_running = False
        schedule.clear()

    def _run_scheduler(self):
        """运行调度器"""
        while self.is_running:
            try:
                if schedule.jobs:
                    schedule.run_pending()
            except Exception as e:
                logger.error(f"调度器运行异常: {e}")
            time.sleep(60)

    def _daily_dau_task(self):
        """每日DAU统计任务"""
        try:
            yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
            
            # 检查数据库中是否已有昨天的DAU数据
            existing_data = self.load_dau_data(yesterday)
            if existing_data:
                logger.info(f"DAU数据已存在: {self._format_date(yesterday)}")
                return
            
            dau_data = self.collect_dau_data(yesterday)
            if dau_data:
                self.save_dau_data(dau_data, yesterday)
                logger.info(f"DAU数据已生成: {self._format_date(yesterday)}")
                
        except Exception as e:
            logger.error(f"每日DAU统计任务失败: {e}")

    def collect_dau_data(self, target_date: datetime.datetime) -> Optional[Dict[str, Any]]:
        """收集指定日期的DAU数据"""
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
        """获取消息统计数据"""
        def get_stats(cursor):
            table_name = f"Mlog_{date_str}_message"
            
            if not self._table_exists(cursor, table_name):
                return None
            
            # 执行多个统计查询
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
            
            # 处理top数据格式
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
        """获取用户统计数据"""
        try:
            queries = [
                ("SELECT COUNT(*) as count FROM M_users", None, False),
                ("SELECT COUNT(*) as count FROM M_groups", None, False),
                ("SELECT COUNT(*) as count FROM M_members", None, False),
                ("""SELECT group_id, JSON_LENGTH(users) as member_count
                    FROM M_groups_users
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
            
        except Exception as e:
            logger.error(f"获取用户统计数据失败: {e}")
            return {'total_users': 0, 'total_groups': 0, 'total_friends': 0, 'top_large_groups': []}

    def _get_command_stats(self, date_str: str) -> List[Dict[str, Any]]:
        """获取指令使用统计"""
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
        """保存DAU数据到数据库"""
        from function.log_db import save_complete_dau_data
        
        date_str = self._format_date(target_date)
        
        # 使用新的完整数据保存函数
        success = save_complete_dau_data(dau_data)
        
        if success:
            logger.info(f"DAU数据已保存到数据库: {date_str}")
            logger.info(f"  活跃用户: {dau_data.get('message_stats', {}).get('active_users', 0)}")
            logger.info(f"  活跃群聊: {dau_data.get('message_stats', {}).get('active_groups', 0)}")
            logger.info(f"  总消息数: {dau_data.get('message_stats', {}).get('total_messages', 0)}")
            logger.info(f"  命令统计: {len(dau_data.get('command_stats', []))}条")
        else:
            logger.error(f"DAU数据保存失败: {date_str}")
            # 失败时回退到JSON文件存储
            filename = f"{date_str}.json"
            filepath = os.path.join(self.data_dir, filename)
            self._file_operation(filepath, 'write', None, dau_data)

    def load_dau_data(self, target_date: datetime.datetime) -> Optional[Dict[str, Any]]:
        """从数据库加载指定日期的DAU数据"""
        date_str = self._format_date(target_date)
        
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
            
            # 解析JSON字段
            message_stats_detail = result.get('message_stats_detail')
            user_stats_detail = result.get('user_stats_detail')
            command_stats_detail = result.get('command_stats_detail')
            
            # 如果有详细数据，则解析JSON
            if message_stats_detail and isinstance(message_stats_detail, str):
                try:
                    message_stats_detail = json.loads(message_stats_detail)
                except:
                    message_stats_detail = {}
            elif not message_stats_detail:
                message_stats_detail = {}
                
            if user_stats_detail and isinstance(user_stats_detail, str):
                try:
                    user_stats_detail = json.loads(user_stats_detail)
                except:
                    user_stats_detail = {}
            elif not user_stats_detail:
                user_stats_detail = {}
                
            if command_stats_detail and isinstance(command_stats_detail, str):
                try:
                    command_stats_detail = json.loads(command_stats_detail)
                except:
                    command_stats_detail = []
            elif not command_stats_detail:
                command_stats_detail = []
            
            # 转换数据库记录为完整的JSON格式
            data = {
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
            
            return data
        
        dau_data = self._with_log_db_cursor(get_dau_data)
        if dau_data:
            return dau_data
        
        # 如果数据库中没有数据，尝试从JSON文件读取（向后兼容）
        filename = f"{date_str}.json"
        filepath = os.path.join(self.data_dir, filename)
        return self._file_operation(filepath, 'read', None)

    def get_recent_dau_data(self, days: int = 7) -> List[Dict[str, Any]]:
        """从数据库获取最近几天的DAU数据"""
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
                # 解析JSON字段
                message_stats_detail = row.get('message_stats_detail')
                user_stats_detail = row.get('user_stats_detail')
                command_stats_detail = row.get('command_stats_detail')
                
                # 安全解析JSON
                if message_stats_detail and isinstance(message_stats_detail, str):
                    try:
                        message_stats_detail = json.loads(message_stats_detail)
                    except:
                        message_stats_detail = {}
                elif not message_stats_detail:
                    message_stats_detail = {}
                    
                if user_stats_detail and isinstance(user_stats_detail, str):
                    try:
                        user_stats_detail = json.loads(user_stats_detail)
                    except:
                        user_stats_detail = {}
                elif not user_stats_detail:
                    user_stats_detail = {}
                    
                if command_stats_detail and isinstance(command_stats_detail, str):
                    try:
                        command_stats_detail = json.loads(command_stats_detail)
                    except:
                        command_stats_detail = []
                elif not command_stats_detail:
                    command_stats_detail = []
                
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
        
        # 如果数据库中没有数据，回退到文件系统查询（向后兼容）
        results = []
        today = datetime.datetime.now()
        
        for i in range(days):
            target_date = today - datetime.timedelta(days=i+1)
            dau_data = self.load_dau_data(target_date)
            if dau_data:
                results.append(dau_data)
                
        return results

    def manual_generate_dau(self, target_date: datetime.datetime) -> bool:
        """手动生成指定日期的DAU数据"""
        dau_data = self.collect_dau_data(target_date)
        if dau_data:
            self.save_dau_data(dau_data, target_date)
            return True
        return False

    def generate_all_dau_data(self, start_date: datetime.datetime = None, end_date: datetime.datetime = None) -> Dict[str, Any]:
        """生成所有历史DAU数据"""
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
            
            try:
                # 检查数据库中是否已有数据
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
                        
            except Exception as e:
                failed_days += 1
                failed_dates.append(date_str)
                logger.error(f"生成DAU数据时出错 {date_str}: {e}")
                
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
        """获取已加载插件的命令模式映射"""
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
            
        except Exception as e:
            logger.error(f"获取已加载插件命令失败: {e}")
            return {}

    def _simplify_regex_pattern(self, pattern: str) -> str:
        """简化正则表达式模式"""
        try:
            # 移除开头结尾符号
            pattern = pattern.lstrip('^').rstrip('$')
            
            # 处理或运算符，取第一个选项
            if '|' in pattern:
                pattern = pattern.split('|')[0]
            
            # 清理正则符号
            pattern = re.sub(r'\\[swn][\*\+\?]?', '', pattern)
            pattern = re.sub(r'[\(\)\[\]\{\}\*\+\?\.\$\^]', '', pattern)
            pattern = pattern.replace('\\', '')
            
            # 只保留中文、英文和数字
            pattern = re.sub(r'[^\u4e00-\u9fff\w]', '', pattern)
            
            # 限制长度
            if len(pattern) > 20:
                pattern = pattern[:20]
            
            return pattern.strip() if pattern.strip() and len(pattern.strip()) > 0 else None
            
        except Exception:
            return None

    def _match_content_to_plugin(self, content: str, loaded_commands: Dict[str, str]) -> str:
        """检查消息内容是否匹配任何已加载的插件"""
        if not content or not loaded_commands:
            return None
            
        try:
            clean_content = content.strip()
            if clean_content.startswith('/'):
                clean_content = clean_content[1:].strip()
            
            for command_pattern, plugin_name in loaded_commands.items():
                if not command_pattern:
                    continue
                    
                # 完全匹配或开头匹配（不区分大小写）
                if (clean_content.lower() == command_pattern.lower() or
                    clean_content.lower().startswith(command_pattern.lower() + ' ') or
                    clean_content.lower().startswith(command_pattern.lower())):
                    return command_pattern
            
            return None
            
        except Exception as e:
            logger.error(f"匹配内容到插件时出错: {e}")
            return None


# 全局DAU分析器实例
dau_analytics = DAUAnalytics()

def start_dau_analytics():
    """启动DAU分析服务"""
    dau_analytics.start_scheduler()
    
def stop_dau_analytics():
    """停止DAU分析服务"""
    dau_analytics.stop_scheduler()
    
def get_dau_analytics() -> DAUAnalytics:
    """获取DAU分析器实例"""
    return dau_analytics 