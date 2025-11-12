#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os, time, json, queue, threading, logging, datetime, pymysql
from decimal import Decimal
from pymysql.cursors import DictCursor
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from config import LOG_DB_CONFIG, DB_CONFIG

# 移除 basicConfig 调用，使用框架统一的日志配置
logger = logging.getLogger('ElainaBot.function.log_db')

def decimal_converter(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

DEFAULT_LOG_CONFIG = {
    'create_tables': True,
    'table_per_day': True,  # 默认按日期自动分表
    'fallback_to_file': True,  # 默认回退到文件记录
    'batch_size': 0,
    'min_pool_size': 5,
    'pool_size': None,
}

MERGED_LOG_CONFIG = {**DEFAULT_LOG_CONFIG, **LOG_DB_CONFIG}

DEFAULT_BATCH_SIZE = 100
DEFAULT_INSERT_INTERVAL = 10
LOG_TYPES = ['message', 'framework', 'error', 'dau', 'id']
TABLE_SUFFIX = {
    'message': 'message',
    'framework': 'framework',
    'error': 'error',
    'dau': 'dau',
    'id': 'id'
}

class LogDatabasePool:
    _instance = None
    _init_lock = threading.Lock()
    _pool = []
    _busy_connections = {}
    _initialized = False
    _min_connections = MERGED_LOG_CONFIG['min_pool_size']
    _idle_timeout = 300
    
    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super(LogDatabasePool, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        with self._init_lock:
            if not self._initialized:
                self._thread_pool = ThreadPoolExecutor(max_workers=None, thread_name_prefix="LogDBPool")
                self._init_pool()
                self._maintenance_thread = threading.Thread(target=self._maintain_pool, daemon=True, name="LogDBPoolMaintenance")
                self._maintenance_thread.start()
                self._initialized = True

    def _create_connection_with_retry(self):
        db_config = DB_CONFIG if MERGED_LOG_CONFIG['use_main_db'] else MERGED_LOG_CONFIG
        retry_count = MERGED_LOG_CONFIG['max_retry']
        retry_delay = MERGED_LOG_CONFIG['retry_interval']
        for i in range(retry_count):
            try:
                return pymysql.connect(
                    host=db_config.get('host', 'localhost'), port=db_config.get('port', 3306),
                    user=db_config.get('user', 'root'), password=db_config.get('password', ''),
                    database=db_config.get('database', ''), charset=db_config.get('charset', 'utf8mb4'),
                    cursorclass=DictCursor, connect_timeout=3,  # 写死连接超时为3秒
                    read_timeout=3, write_timeout=3,  # 写死读写超时为3秒
                    autocommit=False  # 写死不自动提交事务
                )
            except:
                if i < retry_count - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
        return None
    
    def _init_pool(self):
        for _ in range(self._min_connections):
            connection = self._create_connection_with_retry()
            if connection:
                self._pool.append({'connection': connection, 'created_at': time.time(), 'last_used': time.time()})
    
    def _create_connection(self):
        return self._create_connection_with_retry()
    
    def _check_connection(self, connection):
        try:
            connection.ping(reconnect=True)
            return True
        except:
            return False
    
    def _close_connection_safely(self, connection):
        try:
            connection.close()
        except:
            pass
    
    def get_connection(self):
        connection_id = threading.get_ident()
        if connection_id in self._busy_connections:
            return self._busy_connections[connection_id]['connection']
        connection = self._get_pooled_connection_atomic(connection_id)
        if connection:
            return connection
        connection = self._create_connection()
        if connection:
            self._busy_connections[connection_id] = {'connection': connection, 'acquired_at': time.time(), 'created_at': time.time()}
            return connection
        return None
    
    def _get_pooled_connection_atomic(self, connection_id):
        if not self._pool:
            return None
        current_time = time.time()
        while self._pool:
            try:
                conn_info = self._pool.pop(0)
                connection = conn_info['connection']
                if self._check_connection(connection):
                    self._busy_connections[connection_id] = {'connection': connection, 'acquired_at': current_time, 'created_at': conn_info.get('created_at', current_time)}
                    return connection
                else:
                    self._close_connection_safely(connection)
            except IndexError:
                break
        return None
    
    def release_connection(self, connection=None):
        connection_id = threading.get_ident()
        if connection is not None:
            found_id = None
            for conn_id, conn_info in list(self._busy_connections.items()):
                if conn_info['connection'] is connection:
                    found_id = conn_id
                    break
            if found_id:
                connection_id = found_id
            else:
                self._close_connection_safely(connection)
                return
        conn_info = self._busy_connections.pop(connection_id, None)
        if not conn_info:
            return
        connection = conn_info['connection']
        if self._check_connection(connection):
            self._pool.append({'connection': connection, 'created_at': conn_info.get('created_at', time.time()), 'last_used': time.time()})
        else:
            self._close_connection_safely(connection)
    
    def _maintain_pool(self):
        maintenance_cycle = 0
        while True:
            try:
                time.sleep(60)
                maintenance_cycle += 1
                current_time = time.time()
                self._cleanup_idle_connections(current_time)
                self._ensure_min_log_connections()
                if maintenance_cycle % 10 == 0:
                    self._log_stats(current_time)
            except:
                time.sleep(5)
    
    def _cleanup_idle_connections(self, current_time):
        if not self._pool:
            return
        kept_connections = []
        cleaned_count = 0
        current_pool_size = len(self._pool)
        for conn_info in self._pool:
            idle_time = current_time - conn_info['last_used']
            should_remove = (idle_time > self._idle_timeout and current_pool_size - cleaned_count > self._min_connections)
            if should_remove:
                self._close_connection_safely(conn_info['connection'])
                cleaned_count += 1
            else:
                kept_connections.append(conn_info)
        self._pool[:] = kept_connections
    
    def _ensure_min_log_connections(self):
        needed = max(0, self._min_connections - len(self._pool))
        if needed > 0:
            for _ in range(needed):
                conn = self._create_connection()
                if conn:
                    self._pool.append({'connection': conn, 'created_at': time.time(), 'last_used': time.time()})
                else:
                    break
    
    def _log_stats(self, current_time):
        pass

class LogDatabaseManager:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(LogDatabaseManager, cls).__new__(cls)
            return cls._instance
    
    def __init__(self):
        self.pool = LogDatabasePool()
        self.tables_created = set()
        self.log_queues = {log_type: queue.Queue() for log_type in LOG_TYPES}
        self.id_cache = {}
        self.id_cache_lock = threading.Lock()
        self._init_sql_templates()
        self._table_schemas = self._init_table_schemas()
        if MERGED_LOG_CONFIG['create_tables']:
            self._create_all_tables()
        self._save_interval = MERGED_LOG_CONFIG['insert_interval']
        self._batch_size = MERGED_LOG_CONFIG['batch_size']
        self._stop_event = threading.Event()
        self._save_thread = threading.Thread(target=self._periodic_save, daemon=True, name="LogDBSaveThread")
        self._save_thread.start()
        # 根据retention_days判断是否需要自动清理，retention_days > 0时启用清理
        if MERGED_LOG_CONFIG.get('retention_days', 0) > 0:
            self._cleanup_thread = threading.Thread(target=self._periodic_cleanup, daemon=True, name="LogDBCleanupThread")
            self._cleanup_thread.start()

    def _init_sql_templates(self):
        message_sql = "INSERT INTO `{table_name}` (timestamp, type, user_id, group_id, content, raw_message, plugin_name) VALUES (%s, %s, %s, %s, %s, %s, %s)"
        
        self._sql_templates = {
            'message': message_sql,
            'error': "INSERT INTO `{table_name}` (timestamp, content, traceback, resp_obj, send_payload, raw_message) VALUES (%s, %s, %s, %s, %s, %s)",
            'dau': """
                INSERT INTO `{table_name}` 
                (`date`, `active_users`, `active_groups`, `total_messages`, `private_messages`, 
                 `group_join_count`, `group_leave_count`, `group_count_change`, 
                 `friend_add_count`, `friend_remove_count`, `friend_count_change`,
                 `message_stats_detail`, `user_stats_detail`, `command_stats_detail`) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                `active_users` = VALUES(`active_users`),
                `active_groups` = VALUES(`active_groups`),
                `total_messages` = VALUES(`total_messages`),
                `private_messages` = VALUES(`private_messages`),
                `group_join_count` = `group_join_count` + VALUES(`group_join_count`),
                `group_leave_count` = `group_leave_count` + VALUES(`group_leave_count`),
                `group_count_change` = `group_count_change` + VALUES(`group_count_change`),
                `friend_add_count` = `friend_add_count` + VALUES(`friend_add_count`),
                `friend_remove_count` = `friend_remove_count` + VALUES(`friend_remove_count`),
                `friend_count_change` = `friend_count_change` + VALUES(`friend_count_change`),
                `message_stats_detail` = VALUES(`message_stats_detail`),
                `user_stats_detail` = VALUES(`user_stats_detail`),
                `command_stats_detail` = VALUES(`command_stats_detail`)
            """,
            'id': """INSERT INTO `{table_name}` (chat_type, chat_id, last_message_id) VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE last_message_id = VALUES(last_message_id), timestamp = CURRENT_TIMESTAMP""",
            'default': "INSERT INTO `{table_name}` (timestamp, content) VALUES (%s, %s)"
        }
        
        # 消息字段提取器
        def extract_message_fields(log):
            # 从log数据中提取type信息
            msg_type = log.get('type', 'received')  # 默认为received类型
            
            return (
                log.get('timestamp'),
                msg_type,
                log.get('user_id', '未知用户' if msg_type == 'received' else ''),
                log.get('group_id', 'c2c'),
                log.get('content', ''),
                log.get('raw_message', ''),
                log.get('plugin_name', '')
            )
        
        self._field_extractors = {
            'message': extract_message_fields,
            'error': lambda log: (log.get('timestamp'), log.get('content'), log.get('traceback', ''), log.get('resp_obj', ''), log.get('send_payload', ''), log.get('raw_message', '')),
            'id': lambda log: (log.get('chat_type'), log.get('chat_id'), log.get('last_message_id')),
            'default': lambda log: (log.get('timestamp'), log.get('content'))
        }
        
        self._batch_optimized_types = {'message', 'error', 'id', 'default'}

    def _extract_log_data_optimized(self, log_type, logs):
        if log_type == 'dau':
            return self._process_dau_data(logs)
        if log_type in self._batch_optimized_types:
            extractor = self._field_extractors.get(log_type, self._field_extractors.get('default'))
            return [extractor(log) for log in logs]
        extractor = self._field_extractors.get(log_type, self._field_extractors['default'])
        return [extractor(log) for log in logs]
        
    def _process_dau_data(self, logs):
        if not logs:
            return []
        values = []
        log_get = lambda log, key, default=0: log.get(key, default)
        for log in logs:
            message_detail = log.get('message_stats_detail')
            user_detail = log.get('user_stats_detail')
            command_detail = log.get('command_stats_detail')
            values.append((
                log.get('date'), log_get(log, 'active_users'), log_get(log, 'active_groups'),
                log_get(log, 'total_messages'), log_get(log, 'private_messages'),
                log_get(log, 'group_join_count'), log_get(log, 'group_leave_count'), log_get(log, 'group_count_change'),
                log_get(log, 'friend_add_count'), log_get(log, 'friend_remove_count'), log_get(log, 'friend_count_change'),
                json.dumps(message_detail, default=decimal_converter, ensure_ascii=False) if message_detail else None,
                json.dumps(user_detail, default=decimal_converter, ensure_ascii=False) if user_detail else None,
                json.dumps(command_detail, default=decimal_converter, ensure_ascii=False) if command_detail else None
            ))
        return values

    @contextmanager
    def _with_cursor(self, cursor_class=DictCursor):
        connection = self.pool.get_connection()
        if not connection:
            raise Exception("无法获取数据库连接")
        cursor = None
        try:
            cursor = connection.cursor(cursor_class)
            yield cursor, connection
        finally:
            if cursor:
                cursor.close()
            self.pool.release_connection(connection)

    def _format_timestamp(self, dt=None):
        if dt is None:
            dt = datetime.datetime.now()
        return dt.strftime('%Y-%m-%d %H:%M:%S')

    def _get_table_name(self, log_type):
        prefix = MERGED_LOG_CONFIG['table_prefix']
        suffix = TABLE_SUFFIX.get(log_type, log_type)
        if log_type in ('dau', 'id'):
            return f"{prefix}{suffix}"
        if MERGED_LOG_CONFIG['table_per_day']:
            today = datetime.datetime.now().strftime('%Y%m%d')
            return f"{prefix}{today}_{suffix}"
        else:
            return f"{prefix}{suffix}"

    def _table_exists(self, table_name, cursor):
        cursor.execute("SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s", (table_name,))
        result = cursor.fetchone()
        return result and result['count'] > 0
        
    def _init_table_schemas(self):
        unified_message_fields = """`type` varchar(20) NOT NULL DEFAULT 'received', 
        `user_id` varchar(255) NOT NULL, 
        `group_id` varchar(255) DEFAULT 'c2c', 
        `content` text NOT NULL, 
        `raw_message` text DEFAULT NULL, 
        `plugin_name` varchar(255) DEFAULT NULL,"""
        
        return {
            'message': {'base': 'standard', 'fields': unified_message_fields, 'end': 'standard'},
            'error': {
                'base': 'standard',
                'fields': "`content` text NOT NULL, `traceback` text, `resp_obj` text, `send_payload` text, `raw_message` text,",
                'end': 'standard'
            },
            'dau': {
                'base': 'special',
                'fields': """
                `date` date NOT NULL COMMENT '日期' PRIMARY KEY,
                `active_users` int(11) DEFAULT 0 COMMENT '活跃用户数',
                `active_groups` int(11) DEFAULT 0 COMMENT '活跃群聊数',
                `total_messages` int(11) DEFAULT 0 COMMENT '消息总数',
                `private_messages` int(11) DEFAULT 0 COMMENT '私聊消息总数',
                `group_join_count` int(11) DEFAULT 0 COMMENT '今日进群数',
                `group_leave_count` int(11) DEFAULT 0 COMMENT '今日退群数',
                `group_count_change` int(11) DEFAULT 0 COMMENT '群数量增减',
                `friend_add_count` int(11) DEFAULT 0 COMMENT '今日加好友数',
                `friend_remove_count` int(11) DEFAULT 0 COMMENT '今日删好友数',
                `friend_count_change` int(11) DEFAULT 0 COMMENT '好友数增减',
                `message_stats_detail` json COMMENT '详细消息统计数据(JSON)',
                `user_stats_detail` json COMMENT '详细用户统计数据(JSON)',
                `command_stats_detail` json COMMENT '详细命令统计数据(JSON)',
                """,
                'end': 'dau'
            },
            'id': {
                'base': 'special',
                'fields': "`chat_type` varchar(10) NOT NULL, `chat_id` varchar(255) NOT NULL, `last_message_id` varchar(255) NOT NULL, PRIMARY KEY (`chat_type`, `chat_id`),",
                'end': 'id'
            },
            'default': {'base': 'standard', 'fields': "`content` text NOT NULL,", 'end': 'standard'}
        }
    
    def _get_create_table_sql(self, table_name, log_type):
        schema = self._table_schemas.get(log_type, self._table_schemas['default'])
        base_sql = f"CREATE TABLE IF NOT EXISTS `{table_name}` (" if schema['base'] == 'special' else \
                   f"CREATE TABLE IF NOT EXISTS `{table_name}` (`id` bigint(20) NOT NULL AUTO_INCREMENT, `timestamp` datetime NOT NULL,"
        end_templates = {
            'standard': "`created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
            'dau': "`updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
            'id': "`timestamp` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        }
        return base_sql + schema['fields'] + end_templates[schema['end']]

    def _create_table(self, log_type):
        table_name = self._get_table_name(log_type)
        if table_name in self.tables_created:
            return True
        try:
            with self._with_cursor() as (cursor, connection):
                if self._table_exists(table_name, cursor):
                    self.tables_created.add(table_name)
                    return True
                create_table_sql = self._get_create_table_sql(table_name, log_type)
                cursor.execute(create_table_sql)
                if log_type not in ('dau', 'id'):
                    cursor.execute(f"CREATE INDEX idx_{table_name}_time ON {table_name} (timestamp)")
                if log_type == 'message':
                    cursor.execute(f"CREATE INDEX idx_{table_name}_type ON {table_name} (type)")
                    cursor.execute(f"CREATE INDEX idx_{table_name}_user ON {table_name} (user_id)")
                    cursor.execute(f"CREATE INDEX idx_{table_name}_group ON {table_name} (group_id)")
                    cursor.execute(f"CREATE INDEX idx_{table_name}_plugin ON {table_name} (plugin_name)")
                connection.commit()
                self.tables_created.add(table_name)
                return True
        except:
            return False
    
    def _create_all_tables(self):
        for log_type in LOG_TYPES:
            self._create_table(log_type)
    
    def add_log(self, log_type, log_data):
        
        if log_type not in LOG_TYPES:
            return False
        
        self.log_queues[log_type].put(log_data)
        if self._save_interval == 0:
            self._save_logs_to_db()
        return True
    
    def update_id_cache(self, chat_type, chat_id, message_id):
        if not message_id:
            return False
        with self.id_cache_lock:
            cache_key = (chat_type, chat_id)
            self.id_cache[cache_key] = message_id
        return True
    
    def _save_id_cache_to_db(self):
        if not self.id_cache:
            return
        with self.id_cache_lock:
            cache_to_save = self.id_cache.copy()
            self.id_cache.clear()
        if not cache_to_save:
            return
        for (chat_type, chat_id), message_id in cache_to_save.items():
            id_data = {'chat_type': chat_type, 'chat_id': chat_id, 'last_message_id': message_id}
            self.log_queues['id'].put(id_data)
        self._save_log_type_to_db('id')
    
    def _periodic_save(self):
        while not self._stop_event.is_set():
            try:
                if self._save_interval > 0:
                    self._stop_event.wait(self._save_interval)
                    if self._stop_event.is_set():
                        break
                self._save_logs_to_db()
                self._save_id_cache_to_db()
            except:
                time.sleep(5)
    
    def _periodic_cleanup(self):
        while not self._stop_event.is_set():
            try:
                now = datetime.datetime.now()
                today_1am = now.replace(hour=1, minute=0, second=0, microsecond=0)
                if now >= today_1am:
                    next_run = today_1am + datetime.timedelta(days=1)
                else:
                    next_run = today_1am
                wait_seconds = (next_run - now).total_seconds()
                if self._stop_event.wait(wait_seconds):
                    break
                self._cleanup_expired_tables()
            except Exception as e:
                logger.error(f"定期清理线程异常: {e}")
                time.sleep(3600)
    
    def _cleanup_expired_tables(self):
        try:
            retention_days = MERGED_LOG_CONFIG.get('retention_days', 0)
            if retention_days <= 0:
                return
            cutoff_date = datetime.datetime.now() - datetime.timedelta(days=retention_days)
            prefix = MERGED_LOG_CONFIG['table_prefix']
            with self._with_cursor() as (cursor, connection):
                cursor.execute("""
                    SELECT TABLE_NAME as table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = DATABASE() 
                    AND TABLE_NAME LIKE %s
                """, (f"{prefix}%",))
                tables = cursor.fetchall()
                deleted_count = 0
                for table_info in tables:
                    table_name = table_info.get('table_name', '')
                    if not table_name:
                        continue
                    if table_name in (f"{prefix}dau", f"{prefix}id"):
                        continue
                    try:
                        name_without_prefix = table_name[len(prefix):]
                        parts = name_without_prefix.split('_')
                        if not parts or not parts[0].isdigit():
                            continue
                        date_str = parts[0]
                        if len(date_str) != 8:
                            continue
                        table_date = datetime.datetime.strptime(date_str, '%Y%m%d')
                        if table_date < cutoff_date:
                            cursor.execute(f"DROP TABLE IF EXISTS `{table_name}`")
                            deleted_count += 1
                    except (ValueError, IndexError):
                        continue
                connection.commit()
        except Exception as e:
            logger.error(f"清理过期日志表失败: {e}")
    
    def _save_logs_to_db(self):
        for log_type in LOG_TYPES:
            self._save_log_type_to_db(log_type)

    def _build_insert_sql_and_values(self, log_type, table_name, logs):
        sql_template = self._sql_templates.get(log_type, self._sql_templates['default'])
        sql = sql_template.format(table_name=table_name)
        values = self._extract_log_data_optimized(log_type, logs)
        return sql, values
    
    def _save_log_type_to_db(self, log_type):
        queue_size = self.log_queues[log_type].qsize()
        if queue_size == 0:
            return
        batch_size = queue_size if self._batch_size == 0 else min(queue_size, self._batch_size)
        if not self._create_table(log_type):
            return
        table_name = self._get_table_name(log_type)
        logs_to_insert = []
        for _ in range(batch_size):
            try:
                log_data = self.log_queues[log_type].get_nowait()
                logs_to_insert.append(log_data)
            except queue.Empty:
                break
        if not logs_to_insert:
            return
        try:
            with self._with_cursor(cursor_class=None) as (cursor, connection):
                sql, values = self._build_insert_sql_and_values(log_type, table_name, logs_to_insert)
                cursor.executemany(sql, values)
                connection.commit()
        except:
            if MERGED_LOG_CONFIG['fallback_to_file']:
                self._fallback_to_file(log_type, logs_to_insert)
        finally:
            for _ in range(len(logs_to_insert)):
                self.log_queues[log_type].task_done()
    
    def _fallback_to_file(self, log_type, logs):
        try:
            fallback_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'log')
            os.makedirs(fallback_dir, exist_ok=True)
            today = datetime.datetime.now().strftime('%Y-%m-%d')
            fallback_file = os.path.join(fallback_dir, f"{log_type}_{today}.log")
            with open(fallback_file, 'a', encoding='utf-8') as f:
                f.write(f"\n--- {self._format_timestamp()} ---\n")
                for log in logs:
                    f.write(f"[{log.get('timestamp', '')}] {log.get('content', '')}\n")
                    if log_type == 'received':
                        f.write(f"用户ID: {log.get('user_id', '未知用户')}, 群聊ID: {log.get('group_id', 'c2c')}\n")
                        if log.get('raw_message'):
                            f.write(f"原始消息: {log['raw_message']}\n")
                    elif log_type == 'error':
                        for key in ['traceback', 'resp_obj', 'send_payload', 'raw_message']:
                            if log.get(key):
                                f.write(f"{key}: {log[key]}\n")
        except:
            pass
    

    def _cleanup_yesterday_ids(self):
        with self._with_cursor() as (cursor, connection):
            table_name = self._get_table_name('id')
            yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
            cursor.execute(f"DELETE FROM `{table_name}` WHERE DATE(`timestamp`) = %s", (yesterday,))
            connection.commit()

    def shutdown(self):
        self._stop_event.set()
        self._save_logs_to_db()
        self._save_id_cache_to_db()
        if hasattr(self, '_save_thread') and self._save_thread.is_alive():
            self._save_thread.join(timeout=5)
        if hasattr(self, '_cleanup_thread') and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=2)

log_db_manager = LogDatabaseManager()

def add_log_to_db(log_type, log_data):
    if not log_db_manager or not isinstance(log_data, dict):
        return False
    if log_type == 'dau':
        log_data.setdefault('date', datetime.datetime.now().strftime('%Y-%m-%d'))
    else:
        log_data.setdefault('timestamp', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if 'content' not in log_data:
            return False
    return log_db_manager.add_log(log_type, log_data)

def add_dau_event_to_db(event_type, count=1, date=None):
    if not log_db_manager:
        return False
    date = date or datetime.datetime.now().strftime('%Y-%m-%d')
    event_map = {
        'group_join': ('group_join_count', 'group_count_change'),
        'group_leave': ('group_leave_count', 'group_count_change'),
        'friend_add': ('friend_add_count', 'friend_count_change'),
        'friend_remove': ('friend_remove_count', 'friend_count_change')
    }
    dau_data = {'date': date, 'active_users': 0, 'active_groups': 0, 'total_messages': 0, 'private_messages': 0,
                'group_join_count': 0, 'group_leave_count': 0, 'group_count_change': 0,
                'friend_add_count': 0, 'friend_remove_count': 0, 'friend_count_change': 0}
    if event_type in event_map:
        primary, change = event_map[event_type]
        dau_data[primary] = count
        dau_data[change] = count if 'join' in event_type or 'add' in event_type else -count
    return add_log_to_db('dau', dau_data)

def save_daily_dau_data(date, active_users, active_groups, total_messages, private_messages, 
                       message_stats_detail=None, user_stats_detail=None, command_stats_detail=None):
    if not log_db_manager:
        return False
    dau_data = {
        'date': date, 'active_users': active_users, 'active_groups': active_groups,
        'total_messages': total_messages, 'private_messages': private_messages,
        'group_join_count': 0, 'group_leave_count': 0, 'group_count_change': 0,
        'friend_add_count': 0, 'friend_remove_count': 0, 'friend_count_change': 0,
        'message_stats_detail': message_stats_detail,
        'user_stats_detail': user_stats_detail,
        'command_stats_detail': command_stats_detail
    }
    return add_log_to_db('dau', dau_data)

def save_complete_dau_data(dau_data_dict):
    if not log_db_manager:
        return False
    ms, us, cs = dau_data_dict.get('message_stats', {}), dau_data_dict.get('user_stats', {}), dau_data_dict.get('command_stats', [])
    return add_log_to_db('dau', {
        'date': dau_data_dict.get('date'),
        'active_users': ms.get('active_users', 0), 'active_groups': ms.get('active_groups', 0),
        'total_messages': ms.get('total_messages', 0), 'private_messages': ms.get('private_messages', 0),
        'group_join_count': 0, 'group_leave_count': 0, 'group_count_change': 0,
        'friend_add_count': 0, 'friend_remove_count': 0, 'friend_count_change': 0,
        'message_stats_detail': ms, 'user_stats_detail': us, 'command_stats_detail': cs
    })

def record_last_message_id(chat_type, chat_id, message_id):
    if not log_db_manager:
        return False
    return log_db_manager.update_id_cache(chat_type, chat_id, message_id)

def add_sent_message_to_db(chat_type, chat_id, content, raw_message=None, timestamp=None):
    if not log_db_manager or not content:
        return False
    chat_map = {'group': ('ZFC2G', chat_id), 'user': (chat_id, 'ZFC2C')}
    if chat_type not in chat_map:
        return False
    user_id, group_id = chat_map[chat_type]
    log_db_manager.log_queues['message'].put({
        'timestamp': timestamp or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'type': 'received',
        'user_id': user_id, 'group_id': group_id, 'content': content, 
        'raw_message': raw_message or '', 'plugin_name': ''
    })
    log_db_manager._save_log_type_to_db('message')
    return True

def cleanup_yesterday_ids():
    if not log_db_manager:
        return False
    log_db_manager._cleanup_yesterday_ids()
    return True

def cleanup_expired_log_tables():
    if not log_db_manager:
        return False
    log_db_manager._cleanup_expired_tables()
    return True
