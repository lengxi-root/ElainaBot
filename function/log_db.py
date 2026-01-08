#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os, time, json, queue, threading, logging, datetime, pymysql
from decimal import Decimal
from pymysql.cursors import DictCursor
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from config import LOG_DB_CONFIG

logger = logging.getLogger('ElainaBot.function.log_db')

_DEFAULT_LOG_CONFIG = {
    'enabled': True, 'create_tables': True, 'table_per_day': True,
    'fallback_to_file': True, 'batch_size': 0, 'min_pool_size': 5, 'pool_size': None,
}
_CONFIG = {**_DEFAULT_LOG_CONFIG, **LOG_DB_CONFIG}

_LOG_TYPES = ('message', 'framework', 'error', 'dau', 'id')
_LOG_TYPES_SET = frozenset(_LOG_TYPES)
_NON_DAILY_TYPES = frozenset({'dau', 'id'})
_TABLE_SUFFIX = {'message': 'message', 'framework': 'framework', 'error': 'error', 'dau': 'dau', 'id': 'id'}

_MIN_POOL_SIZE = _CONFIG['min_pool_size']
_IDLE_TIMEOUT = 300
_TABLE_PREFIX = _CONFIG['table_prefix']
_TABLE_PER_DAY = _CONFIG['table_per_day']
_CREATE_TABLES = _CONFIG['create_tables']
_FALLBACK_TO_FILE = _CONFIG['fallback_to_file']
_RETENTION_DAYS = _CONFIG.get('retention_days', 0)
_MAX_RETRY = _CONFIG.get('max_retry', 3)
_RETRY_INTERVAL = _CONFIG.get('retry_interval', 1)
_INSERT_INTERVAL = _CONFIG.get('insert_interval', 10)
_BATCH_SIZE = _CONFIG.get('batch_size', 0)

_DB_HOST = _CONFIG.get('host', 'localhost')
_DB_PORT = _CONFIG.get('port', 3306)
_DB_USER = _CONFIG.get('user', 'root')
_DB_PASSWORD = _CONFIG.get('password', '')
_DB_DATABASE = _CONFIG.get('database', '')

_TABLE_EXISTS_SQL = "SELECT COUNT(*) as count FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = %s"
_CLEANUP_TABLES_SQL = "SELECT TABLE_NAME as table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND TABLE_NAME LIKE %s"

_FALLBACK_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'log')

def _decimal_converter(obj):
    if isinstance(obj, Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

class LogDatabasePool:
    __slots__ = ('_thread_pool', '_pool', '_busy_connections', '_initialized')
    _instance = None
    _init_lock = threading.Lock()
    
    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._pool = []
        self._busy_connections = {}
        self._thread_pool = ThreadPoolExecutor(max_workers=None, thread_name_prefix="LogDBPool")
        self._init_pool()
        threading.Thread(target=self._maintain_pool, daemon=True, name="LogDBPoolMaintenance").start()
        self._initialized = True

    def _create_connection(self):
        delay = _RETRY_INTERVAL
        for i in range(_MAX_RETRY):
            try:
                return pymysql.connect(
                    host=_DB_HOST, port=_DB_PORT, user=_DB_USER, password=_DB_PASSWORD,
                    database=_DB_DATABASE, charset='utf8mb4', cursorclass=DictCursor,
                    connect_timeout=3, read_timeout=3, write_timeout=3, autocommit=False
                )
            except:
                if i < _MAX_RETRY - 1:
                    time.sleep(delay)
                    delay *= 2
        return None
    
    def _init_pool(self):
        current_time = time.time()
        for _ in range(_MIN_POOL_SIZE):
            conn = self._create_connection()
            if conn:
                self._pool.append({'connection': conn, 'created_at': current_time, 'last_used': current_time})
    
    def _check_connection(self, connection):
        try:
            connection.ping(reconnect=True)
            return True
        except:
            return False
    
    def _close_connection(self, connection):
        try:
            connection.close()
        except:
            pass
    
    def get_connection(self):
        conn_id = threading.get_ident()
        busy = self._busy_connections.get(conn_id)
        if busy:
            return busy['connection']
        current_time = time.time()
        while self._pool:
            try:
                info = self._pool.pop(0)
                conn = info['connection']
                if self._check_connection(conn):
                    self._busy_connections[conn_id] = {'connection': conn, 'acquired_at': current_time, 'created_at': info.get('created_at', current_time)}
                    return conn
                self._close_connection(conn)
            except IndexError:
                break
        conn = self._create_connection()
        if conn:
            self._busy_connections[conn_id] = {'connection': conn, 'acquired_at': current_time, 'created_at': current_time}
        return conn
    
    def release_connection(self, connection=None):
        conn_id = threading.get_ident()
        if connection is not None:
            for cid, info in list(self._busy_connections.items()):
                if info['connection'] is connection:
                    conn_id = cid
                    break
            else:
                self._close_connection(connection)
                return
        info = self._busy_connections.pop(conn_id, None)
        if not info:
            return
        conn = info['connection']
        if self._check_connection(conn):
            self._pool.append({'connection': conn, 'created_at': info.get('created_at', time.time()), 'last_used': time.time()})
        else:
            self._close_connection(conn)
    
    def _maintain_pool(self):
        while True:
            try:
                time.sleep(60)
                current_time = time.time()
                if self._pool:
                    kept = []
                    pool_size = len(self._pool)
                    cleaned = 0
                    for info in self._pool:
                        if current_time - info['last_used'] > _IDLE_TIMEOUT and pool_size - cleaned > _MIN_POOL_SIZE:
                            self._close_connection(info['connection'])
                            cleaned += 1
                        else:
                            kept.append(info)
                    self._pool[:] = kept
                needed = _MIN_POOL_SIZE - len(self._pool)
                for _ in range(max(0, needed)):
                    conn = self._create_connection()
                    if conn:
                        self._pool.append({'connection': conn, 'created_at': current_time, 'last_used': current_time})
                    else:
                        break
            except:
                time.sleep(5)

class LogDatabaseManager:
    __slots__ = ('pool', 'tables_created', 'log_queues', 'id_cache', 'id_cache_lock',
                 '_sql_templates', '_field_extractors', '_table_schemas', '_stop_event')
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance
    
    def __init__(self):
        if hasattr(self, 'pool') and self.pool:
            return
        self.pool = LogDatabasePool()
        self.tables_created = set()
        self.log_queues = {t: queue.Queue() for t in _LOG_TYPES}
        self.id_cache = {}
        self.id_cache_lock = threading.Lock()
        self._init_sql_templates()
        self._init_table_schemas()
        if _CREATE_TABLES:
            for t in _LOG_TYPES:
                self._create_table(t)
        self._stop_event = threading.Event()
        threading.Thread(target=self._periodic_save, daemon=True, name="LogDBSaveThread").start()
        if _RETENTION_DAYS > 0:
            threading.Thread(target=self._periodic_cleanup, daemon=True, name="LogDBCleanupThread").start()

    def _init_sql_templates(self):
        self._sql_templates = {
            'message': "INSERT INTO `{table_name}` (timestamp, type, user_id, group_id, content, raw_message, plugin_name) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            'error': "INSERT INTO `{table_name}` (timestamp, content, traceback, resp_obj, send_payload, raw_message) VALUES (%s, %s, %s, %s, %s, %s)",
            'dau': """INSERT INTO `{table_name}` (`date`, `active_users`, `active_groups`, `total_messages`, `private_messages`, 
                `group_join_count`, `group_leave_count`, `group_count_change`, `friend_add_count`, `friend_remove_count`, `friend_count_change`,
                `message_stats_detail`, `user_stats_detail`, `command_stats_detail`) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE `active_users` = VALUES(`active_users`), `active_groups` = VALUES(`active_groups`),
                `total_messages` = VALUES(`total_messages`), `private_messages` = VALUES(`private_messages`),
                `group_join_count` = `group_join_count` + VALUES(`group_join_count`), `group_leave_count` = `group_leave_count` + VALUES(`group_leave_count`),
                `group_count_change` = `group_count_change` + VALUES(`group_count_change`), `friend_add_count` = `friend_add_count` + VALUES(`friend_add_count`),
                `friend_remove_count` = `friend_remove_count` + VALUES(`friend_remove_count`), `friend_count_change` = `friend_count_change` + VALUES(`friend_count_change`),
                `message_stats_detail` = VALUES(`message_stats_detail`), `user_stats_detail` = VALUES(`user_stats_detail`), `command_stats_detail` = VALUES(`command_stats_detail`)""",
            'id': "INSERT INTO `{table_name}` (chat_type, chat_id, last_message_id) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE last_message_id = VALUES(last_message_id), timestamp = CURRENT_TIMESTAMP",
            'default': "INSERT INTO `{table_name}` (timestamp, content) VALUES (%s, %s)"
        }
        self._field_extractors = {
            'message': lambda l: (l.get('timestamp'), l.get('type', 'received'), l.get('user_id', '未知用户' if l.get('type', 'received') == 'received' else ''), l.get('group_id', 'c2c'), l.get('content', ''), l.get('raw_message', ''), l.get('plugin_name', '')),
            'error': lambda l: (l.get('timestamp'), l.get('content'), l.get('traceback', ''), l.get('resp_obj', ''), l.get('send_payload', ''), l.get('raw_message', '')),
            'id': lambda l: (l.get('chat_type'), l.get('chat_id'), l.get('last_message_id')),
            'default': lambda l: (l.get('timestamp'), l.get('content'))
        }

    def _init_table_schemas(self):
        self._table_schemas = {
            'message': ('standard', """`type` varchar(20) NOT NULL DEFAULT 'received', `user_id` varchar(255) NOT NULL, 
                `group_id` varchar(255) DEFAULT 'c2c', `content` text NOT NULL, `raw_message` text DEFAULT NULL, `plugin_name` varchar(255) DEFAULT NULL,""", 'standard'),
            'error': ('standard', "`content` text NOT NULL, `traceback` text, `resp_obj` text, `send_payload` text, `raw_message` text,", 'standard'),
            'dau': ('special', """`date` date NOT NULL PRIMARY KEY, `active_users` int(11) DEFAULT 0, `active_groups` int(11) DEFAULT 0,
                `total_messages` int(11) DEFAULT 0, `private_messages` int(11) DEFAULT 0, `group_join_count` int(11) DEFAULT 0,
                `group_leave_count` int(11) DEFAULT 0, `group_count_change` int(11) DEFAULT 0, `friend_add_count` int(11) DEFAULT 0,
                `friend_remove_count` int(11) DEFAULT 0, `friend_count_change` int(11) DEFAULT 0, `message_stats_detail` json,
                `user_stats_detail` json, `command_stats_detail` json,""", 'dau'),
            'id': ('special', "`chat_type` varchar(10) NOT NULL, `chat_id` varchar(255) NOT NULL, `last_message_id` varchar(255) NOT NULL, PRIMARY KEY (`chat_type`, `chat_id`),", 'id'),
            'default': ('standard', "`content` text NOT NULL,", 'standard')
        }

    def _extract_log_data(self, log_type, logs):
        if log_type == 'dau':
            return self._process_dau_data(logs)
        extractor = self._field_extractors.get(log_type, self._field_extractors['default'])
        return [extractor(l) for l in logs]
        
    def _process_dau_data(self, logs):
        if not logs:
            return []
        values = []
        for l in logs:
            ms, us, cs = l.get('message_stats_detail'), l.get('user_stats_detail'), l.get('command_stats_detail')
            values.append((
                l.get('date'), l.get('active_users', 0), l.get('active_groups', 0), l.get('total_messages', 0), l.get('private_messages', 0),
                l.get('group_join_count', 0), l.get('group_leave_count', 0), l.get('group_count_change', 0),
                l.get('friend_add_count', 0), l.get('friend_remove_count', 0), l.get('friend_count_change', 0),
                json.dumps(ms, default=_decimal_converter, ensure_ascii=False) if ms else None,
                json.dumps(us, default=_decimal_converter, ensure_ascii=False) if us else None,
                json.dumps(cs, default=_decimal_converter, ensure_ascii=False) if cs else None
            ))
        return values

    @contextmanager
    def _with_cursor(self, cursor_class=DictCursor):
        conn = self.pool.get_connection()
        if not conn:
            raise Exception("无法获取数据库连接")
        cursor = None
        try:
            cursor = conn.cursor(cursor_class)
            yield cursor, conn
        finally:
            if cursor:
                cursor.close()
            self.pool.release_connection(conn)

    def _get_table_name(self, log_type):
        suffix = _TABLE_SUFFIX.get(log_type, log_type)
        if log_type in _NON_DAILY_TYPES:
            return f"{_TABLE_PREFIX}{suffix}"
        if _TABLE_PER_DAY:
            return f"{_TABLE_PREFIX}{datetime.datetime.now().strftime('%Y%m%d')}_{suffix}"
        return f"{_TABLE_PREFIX}{suffix}"

    def _get_create_table_sql(self, table_name, log_type):
        base, fields, end = self._table_schemas.get(log_type, self._table_schemas['default'])
        if base == 'special':
            sql = f"CREATE TABLE IF NOT EXISTS `{table_name}` ({fields}"
        else:
            sql = f"CREATE TABLE IF NOT EXISTS `{table_name}` (`id` bigint(20) NOT NULL AUTO_INCREMENT, `timestamp` datetime NOT NULL, {fields}"
        ends = {
            'standard': "`created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (`id`)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
            'dau': "`updated_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
            'id': "`timestamp` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        }
        return sql + ends[end]

    def _create_table(self, log_type):
        table_name = self._get_table_name(log_type)
        if table_name in self.tables_created:
            return True
        try:
            with self._with_cursor() as (cursor, conn):
                cursor.execute(_TABLE_EXISTS_SQL, (table_name,))
                if cursor.fetchone().get('count', 0) > 0:
                    self.tables_created.add(table_name)
                    return True
                cursor.execute(self._get_create_table_sql(table_name, log_type))
                if log_type not in _NON_DAILY_TYPES:
                    cursor.execute(f"CREATE INDEX idx_{table_name}_time ON {table_name} (timestamp)")
                if log_type == 'message':
                    for col in ('type', 'user_id', 'group_id', 'plugin_name'):
                        cursor.execute(f"CREATE INDEX idx_{table_name}_{col} ON {table_name} ({col})")
                conn.commit()
                self.tables_created.add(table_name)
                return True
        except:
            return False
    
    def add_log(self, log_type, log_data):
        if log_type not in _LOG_TYPES_SET:
            return False
        self.log_queues[log_type].put(log_data)
        if _INSERT_INTERVAL == 0:
            self._save_logs_to_db()
        return True
    
    def update_id_cache(self, chat_type, chat_id, message_id):
        if not message_id:
            return False
        with self.id_cache_lock:
            self.id_cache[(chat_type, chat_id)] = message_id
        return True
    
    def _save_id_cache_to_db(self):
        if not self.id_cache:
            return
        with self.id_cache_lock:
            cache = self.id_cache.copy()
            self.id_cache.clear()
        if not cache:
            return
        for (chat_type, chat_id), msg_id in cache.items():
            self.log_queues['id'].put({'chat_type': chat_type, 'chat_id': chat_id, 'last_message_id': msg_id})
        self._save_log_type_to_db('id')
    
    def _periodic_save(self):
        while not self._stop_event.is_set():
            try:
                if _INSERT_INTERVAL > 0:
                    self._stop_event.wait(_INSERT_INTERVAL)
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
                target = now.replace(hour=1, minute=0, second=0, microsecond=0)
                if now >= target:
                    target += datetime.timedelta(days=1)
                if self._stop_event.wait((target - now).total_seconds()):
                    break
                self._cleanup_expired_tables()
            except:
                time.sleep(3600)
    
    def _cleanup_expired_tables(self):
        if _RETENTION_DAYS <= 0:
            return
        try:
            cutoff = datetime.datetime.now() - datetime.timedelta(days=_RETENTION_DAYS)
            with self._with_cursor() as (cursor, conn):
                cursor.execute(_CLEANUP_TABLES_SQL, (f"{_TABLE_PREFIX}%",))
                for row in cursor.fetchall():
                    name = row.get('table_name', '')
                    if not name or name in (f"{_TABLE_PREFIX}dau", f"{_TABLE_PREFIX}id"):
                        continue
                    try:
                        parts = name[len(_TABLE_PREFIX):].split('_')
                        if parts and parts[0].isdigit() and len(parts[0]) == 8:
                            if datetime.datetime.strptime(parts[0], '%Y%m%d') < cutoff:
                                cursor.execute(f"DROP TABLE IF EXISTS `{name}`")
                    except:
                        continue
                conn.commit()
        except Exception as e:
            logger.error(f"清理过期日志表失败: {e}")
    
    def _save_logs_to_db(self):
        for t in _LOG_TYPES:
            self._save_log_type_to_db(t)
    
    def _save_log_type_to_db(self, log_type):
        q = self.log_queues[log_type]
        size = q.qsize()
        if size == 0:
            return
        batch = size if _BATCH_SIZE == 0 else min(size, _BATCH_SIZE)
        if not self._create_table(log_type):
            return
        table_name = self._get_table_name(log_type)
        logs = []
        for _ in range(batch):
            try:
                logs.append(q.get_nowait())
            except queue.Empty:
                break
        if not logs:
            return
        try:
            with self._with_cursor(cursor_class=None) as (cursor, conn):
                sql = self._sql_templates.get(log_type, self._sql_templates['default']).format(table_name=table_name)
                cursor.executemany(sql, self._extract_log_data(log_type, logs))
                conn.commit()
        except:
            if _FALLBACK_TO_FILE:
                self._fallback_to_file(log_type, logs)
        finally:
            for _ in range(len(logs)):
                q.task_done()
    
    def _fallback_to_file(self, log_type, logs):
        try:
            os.makedirs(_FALLBACK_DIR, exist_ok=True)
            path = os.path.join(_FALLBACK_DIR, f"{log_type}_{datetime.datetime.now().strftime('%Y-%m-%d')}.log")
            with open(path, 'a', encoding='utf-8') as f:
                f.write(f"\n--- {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
                for l in logs:
                    f.write(f"[{l.get('timestamp', '')}] {l.get('content', '')}\n")
                    if log_type == 'received':
                        f.write(f"用户ID: {l.get('user_id', '未知用户')}, 群聊ID: {l.get('group_id', 'c2c')}\n")
                        if l.get('raw_message'):
                            f.write(f"原始消息: {l['raw_message']}\n")
                    elif log_type == 'error':
                        for k in ('traceback', 'resp_obj', 'send_payload', 'raw_message'):
                            if l.get(k):
                                f.write(f"{k}: {l[k]}\n")
        except:
            pass

    def _cleanup_old_ids(self):
        with self._with_cursor() as (cursor, conn):
            table_name = self._get_table_name('id')
            cutoff = (datetime.datetime.now() - datetime.timedelta(days=3)).replace(hour=0, minute=0, second=0, microsecond=0)
            cursor.execute(f"DELETE FROM `{table_name}` WHERE `timestamp` < %s", (cutoff,))
            conn.commit()

    def shutdown(self):
        self._stop_event.set()
        self._save_logs_to_db()
        self._save_id_cache_to_db()

log_db_manager = LogDatabaseManager()

def add_log_to_db(log_type, log_data):
    if not isinstance(log_data, dict):
        return False
    if log_type == 'dau':
        log_data.setdefault('date', datetime.datetime.now().strftime('%Y-%m-%d'))
    else:
        log_data.setdefault('timestamp', datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        if 'content' not in log_data:
            return False
    return log_db_manager.add_log(log_type, log_data)

_EVENT_MAP = {
    'group_join': ('group_join_count', 'group_count_change', 1),
    'group_leave': ('group_leave_count', 'group_count_change', -1),
    'friend_add': ('friend_add_count', 'friend_count_change', 1),
    'friend_remove': ('friend_remove_count', 'friend_count_change', -1)
}

def add_dau_event_to_db(event_type, count=1, date=None):
    date = date or datetime.datetime.now().strftime('%Y-%m-%d')
    dau = {'date': date, 'active_users': 0, 'active_groups': 0, 'total_messages': 0, 'private_messages': 0,
           'group_join_count': 0, 'group_leave_count': 0, 'group_count_change': 0,
           'friend_add_count': 0, 'friend_remove_count': 0, 'friend_count_change': 0}
    if event_type in _EVENT_MAP:
        primary, change, sign = _EVENT_MAP[event_type]
        dau[primary] = count
        dau[change] = count * sign
    return add_log_to_db('dau', dau)

def save_daily_dau_data(date, active_users, active_groups, total_messages, private_messages, 
                       message_stats_detail=None, user_stats_detail=None, command_stats_detail=None):
    return add_log_to_db('dau', {
        'date': date, 'active_users': active_users, 'active_groups': active_groups,
        'total_messages': total_messages, 'private_messages': private_messages,
        'group_join_count': 0, 'group_leave_count': 0, 'group_count_change': 0,
        'friend_add_count': 0, 'friend_remove_count': 0, 'friend_count_change': 0,
        'message_stats_detail': message_stats_detail, 'user_stats_detail': user_stats_detail, 'command_stats_detail': command_stats_detail
    })

def save_complete_dau_data(d):
    ms, us, cs = d.get('message_stats', {}), d.get('user_stats', {}), d.get('command_stats', [])
    return add_log_to_db('dau', {
        'date': d.get('date'), 'active_users': ms.get('active_users', 0), 'active_groups': ms.get('active_groups', 0),
        'total_messages': ms.get('total_messages', 0), 'private_messages': ms.get('private_messages', 0),
        'group_join_count': 0, 'group_leave_count': 0, 'group_count_change': 0,
        'friend_add_count': 0, 'friend_remove_count': 0, 'friend_count_change': 0,
        'message_stats_detail': ms, 'user_stats_detail': us, 'command_stats_detail': cs
    })

def record_last_message_id(chat_type, chat_id, message_id):
    return log_db_manager.update_id_cache(chat_type, chat_id, message_id)

_CHAT_MAP = {'group': lambda cid: ('ZFC2G', cid), 'user': lambda cid: (cid, 'ZFC2C')}

def add_sent_message_to_db(chat_type, chat_id, content, raw_message=None, timestamp=None):
    if not content or chat_type not in _CHAT_MAP:
        return False
    user_id, group_id = _CHAT_MAP[chat_type](chat_id)
    log_db_manager.log_queues['message'].put({
        'timestamp': timestamp or datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'type': 'received', 'user_id': user_id, 'group_id': group_id, 
        'content': content, 'raw_message': raw_message or '', 'plugin_name': ''
    })
    log_db_manager._save_log_type_to_db('message')
    return True

def cleanup_old_ids():
    log_db_manager._cleanup_old_ids()
    return True

def cleanup_expired_log_tables():
    log_db_manager._cleanup_expired_tables()
    return True
