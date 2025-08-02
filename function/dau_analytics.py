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

# 导入插件管理器用于获取已加载的插件信息
try:
    from core.plugin.PluginManager import PluginManager
    _plugin_manager_available = True
except ImportError:
    _plugin_manager_available = False

# 设置日志
logger = logging.getLogger('dau_analytics')

class DAUAnalytics:
    """DAU数据分析和持久化组件"""
    
    def __init__(self):
        self.data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'dau')
        self.ensure_data_dir()
        self.is_running = False
        self.scheduler_thread = None
        
    def ensure_data_dir(self):
        """确保DAU数据目录存在"""
        os.makedirs(self.data_dir, exist_ok=True)
        
    def start_scheduler(self):
        """启动定时任务调度器"""
        if self.is_running:
            return
            
        self.is_running = True
        
        # 清除之前的任务
        schedule.clear()
        
        # 设置每天0点10分执行
        schedule.every().day.at("00:10").do(self._daily_dau_task)
        
        # 启动调度器线程
        self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.scheduler_thread.start()
        
        logger.info("DAU数据分析模块初始化完成")
        
    def stop_scheduler(self):
        """停止定时任务调度器"""
        self.is_running = False
        schedule.clear()
        
    def _run_scheduler(self):
        """运行调度器"""
        while self.is_running:
            try:
                # 检查是否有任务需要执行
                pending_jobs = schedule.jobs
                if pending_jobs:
                    schedule.run_pending()
                    
            except Exception as e:
                logger.error(f"DAU调度器运行异常: {e}")
                logger.error(f"错误详情: {traceback.format_exc()}")
                
            time.sleep(60)  # 每分钟检查一次
            
    def _daily_dau_task(self):
        """每日DAU统计任务"""
        try:
            yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
            date_str = yesterday.strftime('%Y-%m-%d')
            
            # 检查是否已经存在该日期的数据文件
            dau_file = os.path.join(self.data_dir, f"{date_str}.json")
            if os.path.exists(dau_file):
                return
            
            dau_data = self.collect_dau_data(yesterday)
            if dau_data:
                self.save_dau_data(dau_data, yesterday)
                
        except Exception as e:
            logger.error(f"每日DAU统计任务执行失败: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            
    def collect_dau_data(self, target_date: datetime.datetime) -> Optional[Dict[str, Any]]:
        """收集指定日期的DAU数据"""
        try:
            date_str = target_date.strftime('%Y%m%d')
            display_date = target_date.strftime('%Y-%m-%d')
            
            # 获取消息统计数据
            message_stats = self._get_message_stats(date_str)
            if not message_stats:
                return None
                
            # 获取用户统计数据
            user_stats = self._get_user_stats()
            
            # 获取指令使用统计
            command_stats = self._get_command_stats(date_str)
            
            # 组装完整的DAU数据
            dau_data = {
                'date': display_date,
                'date_str': date_str,
                'generated_at': datetime.datetime.now().isoformat(),
                'message_stats': message_stats,
                'user_stats': user_stats,
                'command_stats': command_stats,
                'version': '1.0'
            }
            
            return dau_data
            
        except Exception as e:
            logger.error(f"收集DAU数据失败: {e}")
            return None
            
    def _get_message_stats(self, date_str: str) -> Optional[Dict[str, Any]]:
        """获取消息统计数据"""
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            logger.error("无法连接到日志数据库")
            return None
            
        cursor = None
        try:
            from pymysql.cursors import DictCursor
            cursor = connection.cursor(DictCursor)
            table_name = f"Mlog_{date_str}_message"
            
            # 检查表是否存在
            check_query = """
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """
            cursor.execute(check_query, (table_name,))
            result = cursor.fetchone()
            
            if not result or result['count'] == 0:
                return None
                
            # 查询总消息数
            cursor.execute(f"SELECT COUNT(*) as count FROM {table_name}")
            total_messages = cursor.fetchone()['count']
            
            # 查询活跃用户数（去重）
            cursor.execute(f"""
                SELECT COUNT(DISTINCT user_id) as count 
                FROM {table_name} 
                WHERE user_id IS NOT NULL AND user_id != ''
            """)
            active_users = cursor.fetchone()['count']
            
            # 查询活跃群聊数（去重，不包括私聊）
            cursor.execute(f"""
                SELECT COUNT(DISTINCT group_id) as count 
                FROM {table_name} 
                WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''
            """)
            active_groups = cursor.fetchone()['count']
            
            # 查询私聊消息数
            cursor.execute(f"""
                SELECT COUNT(*) as count 
                FROM {table_name} 
                WHERE group_id = 'c2c'
            """)
            private_messages = cursor.fetchone()['count']
            
            # 按小时统计消息数量，找出最活跃时段
            cursor.execute(f"""
                SELECT HOUR(timestamp) as hour, COUNT(*) as count 
                FROM {table_name} 
                GROUP BY HOUR(timestamp) 
                ORDER BY count DESC 
                LIMIT 1
            """)
            peak_hour_result = cursor.fetchone()
            peak_hour = peak_hour_result['hour'] if peak_hour_result else 0
            peak_hour_count = peak_hour_result['count'] if peak_hour_result else 0
            
            # 获取前10活跃群组
            cursor.execute(f"""
                SELECT group_id, COUNT(*) as msg_count 
                FROM {table_name} 
                WHERE group_id != 'c2c' AND group_id IS NOT NULL AND group_id != ''
                GROUP BY group_id 
                ORDER BY msg_count DESC 
                LIMIT 10
            """)
            top_groups_raw = cursor.fetchall()
            
            # 处理群组数据
            top_groups = []
            for group in top_groups_raw:
                group_id = group['group_id']
                top_groups.append({
                    'group_id': group_id,
                    'message_count': group['msg_count']
                })
                
            # 获取前10活跃用户
            cursor.execute(f"""
                SELECT user_id, COUNT(*) as msg_count 
                FROM {table_name} 
                WHERE user_id IS NOT NULL AND user_id != ''
                GROUP BY user_id 
                ORDER BY msg_count DESC 
                LIMIT 10
            """)
            top_users_raw = cursor.fetchall()
            
            # 处理用户数据
            top_users = []
            for user in top_users_raw:
                user_id = user['user_id']
                top_users.append({
                    'user_id': user_id,
                    'message_count': user['msg_count']
                })
                
            return {
                'total_messages': total_messages,
                'active_users': active_users,
                'active_groups': active_groups,
                'private_messages': private_messages,
                'peak_hour': peak_hour,
                'peak_hour_count': peak_hour_count,
                'top_groups': top_groups,
                'top_users': top_users
            }
            
        except Exception as e:
            logger.error(f"获取消息统计数据失败: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if connection:
                log_db_pool.release_connection(connection)
                
    def _get_user_stats(self) -> Dict[str, Any]:
        """获取用户统计数据"""
        try:
            # 使用DatabaseService执行并发查询
            queries = [
                ("SELECT COUNT(*) as count FROM M_users", None, False),  # 总用户数
                ("SELECT COUNT(*) as count FROM M_groups", None, False),  # 总群组数
                ("SELECT COUNT(*) as count FROM M_members", None, False),  # 总好友数
                ("""
                    SELECT group_id, JSON_LENGTH(users) as member_count
                    FROM M_groups_users
                    ORDER BY member_count DESC
                    LIMIT 3
                """, None, True)  # 前3个最大群
            ]
            
            results = DatabaseService.execute_concurrent_queries(queries)
            
            total_users = results[0]['count'] if results[0] else 0
            total_groups = results[1]['count'] if results[1] else 0
            total_friends = results[2]['count'] if results[2] else 0
            
            # 处理最大群数据
            top_large_groups = []
            if results[3] and isinstance(results[3], list):
                for group in results[3]:
                    group_id = group.get('group_id', '')
                    top_large_groups.append({
                        'group_id': group_id,
                        'member_count': group.get('member_count', 0)
                    })
                    
            return {
                'total_users': total_users,
                'total_groups': total_groups,
                'total_friends': total_friends,
                'top_large_groups': top_large_groups
            }
            
        except Exception as e:
            logger.error(f"获取用户统计数据失败: {e}")
            return {
                'total_users': 0,
                'total_groups': 0,
                'total_friends': 0,
                'top_large_groups': []
            }
            
    def _get_command_stats(self, date_str: str) -> List[Dict[str, Any]]:
        """获取指令使用统计（前3名）- 仅统计被插件加载器加载的插件"""
        log_db_pool = LogDatabasePool()
        connection = log_db_pool.get_connection()
        
        if not connection:
            return []
            
        cursor = None
        try:
            from pymysql.cursors import DictCursor
            cursor = connection.cursor(DictCursor)
            table_name = f"Mlog_{date_str}_message"
            
            # 检查表是否存在
            check_query = """
                SELECT COUNT(*) as count 
                FROM information_schema.tables 
                WHERE table_schema = DATABASE() 
                AND table_name = %s
            """
            cursor.execute(check_query, (table_name,))
            result = cursor.fetchone()
            
            if not result or result['count'] == 0:
                return []
                
            # 获取已加载插件的正则处理器
            loaded_commands = self._get_loaded_plugin_commands()
                
            # 查询所有消息内容
            cursor.execute(f"""
                SELECT content 
                FROM {table_name} 
                WHERE content IS NOT NULL AND content != ''
            """)
            contents = cursor.fetchall()
            
            # 统计指令使用次数
            command_counts = {}
            
            for row in contents:
                content = row.get('content', '').strip()
                if not content:
                    continue
                    
                # 检查消息是否匹配任何已加载的插件
                matched_command = self._match_content_to_plugin(content, loaded_commands)
                if matched_command:
                    command_counts[matched_command] = command_counts.get(matched_command, 0) + 1
                        
            # 转换为列表并排序，只取前3名
            command_stats = [
                {'command': cmd, 'count': count} 
                for cmd, count in command_counts.items()
            ]
            command_stats.sort(key=lambda x: x['count'], reverse=True)
            
            return command_stats[:3]
            
        except Exception as e:
            logger.error(f"获取指令统计数据失败: {e}")
            return []
        finally:
            if cursor:
                cursor.close()
            if connection:
                log_db_pool.release_connection(connection)
                
    def save_dau_data(self, dau_data: Dict[str, Any], target_date: datetime.datetime):
        """保存DAU数据到JSON文件"""
        try:
            filename = f"{target_date.strftime('%Y-%m-%d')}.json"
            filepath = os.path.join(self.data_dir, filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(dau_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.error(f"保存DAU数据失败: {e}")
            
    def load_dau_data(self, target_date: datetime.datetime) -> Optional[Dict[str, Any]]:
        """加载指定日期的DAU数据"""
        try:
            filename = f"{target_date.strftime('%Y-%m-%d')}.json"
            filepath = os.path.join(self.data_dir, filename)
            
            if not os.path.exists(filepath):
                return None
                
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
                
        except Exception as e:
            logger.error(f"加载DAU数据失败: {e}")
            return None
            
    def get_recent_dau_data(self, days: int = 7) -> List[Dict[str, Any]]:
        """获取最近几天的DAU数据"""
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
        try:
            dau_data = self.collect_dau_data(target_date)
            if dau_data:
                self.save_dau_data(dau_data, target_date)
                return True
            else:
                return False
                
        except Exception as e:
            logger.error(f"手动生成DAU数据失败: {e}")
            logger.error(f"错误详情: {traceback.format_exc()}")
            return False
            
    def generate_all_dau_data(self, start_date: datetime.datetime = None, end_date: datetime.datetime = None) -> Dict[str, Any]:
        """生成所有历史DAU数据（跳过已生成的）"""
        if not start_date:
            start_date = datetime.datetime.now() - datetime.timedelta(days=30)
        if not end_date:
            end_date = datetime.datetime.now() - datetime.timedelta(days=1)
            
        # 确保开始日期不晚于结束日期
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
            date_str = current_date.strftime('%Y-%m-%d')
            
            try:
                # 检查是否已存在DAU数据文件
                dau_file = os.path.join(self.data_dir, f"{date_str}.json")
                if os.path.exists(dau_file):
                    skipped_days += 1
                    skipped_dates.append(date_str)
                else:
                    # 生成DAU数据
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
                
            # 移到下一天
            current_date += datetime.timedelta(days=1)
            
        result = {
            'total_days': total_days,
            'generated_days': generated_days,
            'skipped_days': skipped_days,
            'failed_days': failed_days,
            'generated_dates': generated_dates,
            'skipped_dates': skipped_dates,
            'failed_dates': failed_dates,
            'start_date': start_date.strftime('%Y-%m-%d'),
            'end_date': end_date.strftime('%Y-%m-%d')
        }
        
        return result
            
    def _get_loaded_plugin_commands(self) -> Dict[str, str]:
        """获取已加载插件的命令模式映射"""
        if not _plugin_manager_available:
            return {}
            
        try:
            # 从PluginManager获取已加载的正则处理器
            loaded_commands = {}
            
            if hasattr(PluginManager, '_regex_handlers'):
                for pattern, handler_info in PluginManager._regex_handlers.items():
                    if isinstance(handler_info, dict):
                        plugin_class = handler_info.get('class')
                        if plugin_class:
                            plugin_name = plugin_class.__name__
                            # 简化正则模式以便匹配
                            simplified_pattern = self._simplify_regex_pattern(pattern)
                            if simplified_pattern:
                                loaded_commands[simplified_pattern] = plugin_name
            
            return loaded_commands
            
        except Exception as e:
            logger.error(f"获取已加载插件命令失败: {e}")
            return {}
            
    def _simplify_regex_pattern(self, pattern: str) -> str:
        """简化正则表达式模式，提取主要的命令关键词"""
        try:
            # 移除开头的^符号
            if pattern.startswith('^'):
                pattern = pattern[1:]
            
            # 移除结尾的$符号
            if pattern.endswith('$'):
                pattern = pattern[:-1]
            
            # 提取简单的字符串模式（去除复杂的正则符号）
            # 例如: "签到" -> "签到", "菜单|help" -> "菜单", "状态\\s*查看" -> "状态"
            
            # 处理或运算符，取第一个选项
            if '|' in pattern:
                pattern = pattern.split('|')[0]
            
            # 移除转义符和空白符匹配
            pattern = re.sub(r'\\[swn][\*\+\?]?', '', pattern)
            
            # 移除括号和量词
            pattern = re.sub(r'[\(\)\[\]\{\}\*\+\?\.\$\^]', '', pattern)
            
            # 移除反斜杠转义
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
        """检查消息内容是否匹配任何已加载的插件，返回匹配的命令名"""
        if not content or not loaded_commands:
            return None
            
        try:
            # 去除开头的斜杠
            clean_content = content.strip()
            if clean_content.startswith('/'):
                clean_content = clean_content[1:].strip()
            
            # 尝试匹配已加载的插件命令
            for command_pattern, plugin_name in loaded_commands.items():
                if not command_pattern:
                    continue
                    
                # 完全匹配或开头匹配
                if (clean_content == command_pattern or 
                    clean_content.startswith(command_pattern + ' ') or
                    clean_content.startswith(command_pattern)):
                    return command_pattern
                    
                # 不区分大小写匹配
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