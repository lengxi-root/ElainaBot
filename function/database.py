import json, logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from function.httpx_pool import get_json
from config import LOG_DB_CONFIG, DB_CONFIG, appid

logger = logging.getLogger('ElainaBot.function.database')

_TABLE_PREFIX = LOG_DB_CONFIG['table_prefix']
_USERS_TABLE = f"{_TABLE_PREFIX}users"
_GROUPS_USERS_TABLE = f"{_TABLE_PREFIX}groups_users"
_MEMBERS_TABLE = f"{_TABLE_PREFIX}members"
_DATABASE_NAME = LOG_DB_CONFIG.get('database', 'log')
_API_URL_TEMPLATE = f"http://127.0.0.1:65535/api/bot/xx.php?openid={{user_id}}&appid={appid}"
_NAME_KEYS = ('名字', 'name', 'nickname')

_SQL_INSERT_USER = f"INSERT IGNORE INTO {_USERS_TABLE} (user_id) VALUES (%s)"
_SQL_COUNT_USERS = f"SELECT COUNT(*) AS count FROM {_USERS_TABLE}"
_SQL_SELECT_USER = f"SELECT user_id FROM {_USERS_TABLE} WHERE user_id = %s"
_SQL_SELECT_USER_NAME = f"SELECT name FROM {_USERS_TABLE} WHERE user_id = %s"
_SQL_UPSERT_USER_NAME = f"INSERT INTO {_USERS_TABLE} (user_id, name) VALUES (%s, %s) ON DUPLICATE KEY UPDATE name = %s"
_SQL_COUNT_GROUPS = f"SELECT COUNT(*) AS count FROM {_GROUPS_USERS_TABLE}"
_SQL_SELECT_GROUP_USERS = f"SELECT users FROM {_GROUPS_USERS_TABLE} WHERE group_id = %s"
_SQL_UPSERT_GROUP_USERS = f"INSERT INTO {_GROUPS_USERS_TABLE} (group_id, users) VALUES (%s, %s) ON DUPLICATE KEY UPDATE users = %s"
_SQL_INSERT_MEMBER = f"INSERT IGNORE INTO {_MEMBERS_TABLE} (user_id) VALUES (%s)"
_SQL_COUNT_MEMBERS = f"SELECT COUNT(*) AS count FROM {_MEMBERS_TABLE}"

class Database:
    _instance = None
    _thread_pool = None
    _enabled = DB_CONFIG.get('enabled', True)
    _table_cache = {
        'users': _USERS_TABLE,
        'groups_users': _GROUPS_USERS_TABLE,
        'members': _MEMBERS_TABLE
    }

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            if cls._enabled:
                cls._instance._init_database()
            else:
                logger.info("主数据库已禁用（DB_CONFIG['enabled'] = False）")
        return cls._instance

    @staticmethod
    def get_table_name(base_name):
        return Database._table_cache.get(base_name, f"{_TABLE_PREFIX}{base_name}")

    def _init_database(self):
        if self._thread_pool is None:
            self._thread_pool = ThreadPoolExecutor(max_workers=None, thread_name_prefix="Database")
        self._db_pool = None
        self._initialize_tables()
    
    def _get_db_pool(self):
        if not self._enabled:
            return None
        if self._db_pool is None:
            from function.log_db import LogDatabasePool
            self._db_pool = LogDatabasePool()
        return self._db_pool
    
    @contextmanager
    def _get_cursor(self):
        pool = self._get_db_pool()
        if pool is None:
            yield None, None
            return
        connection = pool.get_connection()
        
        if not connection:
            yield None, None
            return
        
        cursor = None
        try:
            cursor = connection.cursor()
            yield cursor, connection
        except Exception as e:
            logger.error(f"数据库操作异常: {e}")
            if connection:
                try:
                    connection.rollback()
                except:
                    pass
            raise
        finally:
            if cursor:
                try:
                    cursor.close()
                except:
                    pass
            if connection:
                pool.release_connection(connection)

    def _initialize_tables(self):
        """在独立线程中初始化表，避免 eventlet 超时"""
        import threading
        
        def do_init():
            tables_sql = [
                f"CREATE TABLE IF NOT EXISTS {_USERS_TABLE} (user_id VARCHAR(255) NOT NULL UNIQUE, name VARCHAR(255) DEFAULT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
                f"CREATE TABLE IF NOT EXISTS {_GROUPS_USERS_TABLE} (group_id VARCHAR(255) NOT NULL UNIQUE, users JSON DEFAULT NULL, PRIMARY KEY (group_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
                f"CREATE TABLE IF NOT EXISTS {_MEMBERS_TABLE} (user_id VARCHAR(255) NOT NULL UNIQUE, PRIMARY KEY (user_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
            ]
            try:
                with self._get_cursor() as (cursor, connection):
                    if not cursor:
                        return
                    for sql in tables_sql:
                        cursor.execute(sql)
                    self._add_name_column_if_not_exists(cursor)
                    connection.commit()
            except Exception as e:
                logger.error(f"初始化数据库表失败: {type(e).__name__}: {e}")
        
        # 使用原生线程执行，绕过 eventlet
        init_thread = threading.Thread(target=do_init, daemon=True)
        init_thread.start()
        init_thread.join(timeout=15)  # 最多等待15秒
        if init_thread.is_alive():
            logger.warning("数据库表初始化超时，将在后台继续")

    def _add_name_column_if_not_exists(self, cursor):
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = 'name'",
            (_DATABASE_NAME, _USERS_TABLE)
        )
        result = cursor.fetchone()
        if isinstance(result, dict):
            column_count = result.get('COUNT(*)', 0)
        elif isinstance(result, (list, tuple)) and result:
            column_count = result[0]
        else:
            column_count = 0
        
        if column_count == 0:
            cursor.execute(f"ALTER TABLE {_USERS_TABLE} ADD COLUMN name VARCHAR(255) DEFAULT NULL")

    def _async_execute(self, func, *args):
        if self._thread_pool:
            self._thread_pool.submit(func, *args)

    def _execute_query(self, sql, params=None):
        try:
            with self._get_cursor() as (cursor, connection):
                if not cursor:
                    return None
                cursor.execute(sql, params or ())
                return cursor.fetchone()
        except Exception as e:
            logger.error(f"数据库查询失败: {e}")
            return None

    def _execute_update(self, sql, params=None):
        try:
            with self._get_cursor() as (cursor, connection):
                if not cursor:
                    return False
                cursor.execute(sql, params or ())
                connection.commit()
                return True
        except Exception as e:
            logger.error(f"数据库更新失败: {e}")
            return False

    def add_user(self, user_id):
        self._async_execute(self._add_user, user_id)

    def _add_user(self, user_id):
        self._execute_update(_SQL_INSERT_USER, (user_id,))
        self._update_user_name(user_id)

    def get_user_count(self):
        result = self._execute_query(_SQL_COUNT_USERS)
        return result.get('count', 0) if result else 0

    def exists_user(self, user_id):
        return bool(self._execute_query(_SQL_SELECT_USER, (user_id,)))

    def get_group_count(self):
        result = self._execute_query(_SQL_COUNT_GROUPS)
        return result.get('count', 0) if result else 0

    def add_user_to_group(self, group_id, user_id):
        self._async_execute(self._add_user_to_group, group_id, user_id)

    def _add_user_to_group(self, group_id, user_id):
        result = self._execute_query(_SQL_SELECT_GROUP_USERS, (group_id,))
        
        try:
            users = []
            if result and result.get('users'):
                try:
                    users = json.loads(result['users'])
                    if not isinstance(users, list):
                        users = []
                except (json.JSONDecodeError, TypeError):
                    users = []
            
            user_id_str = str(user_id)
            if not any(u.get("userid") == user_id_str for u in users if isinstance(u, dict)):
                users.append({"value": 1, "userid": user_id_str})
                users_json = json.dumps(users, ensure_ascii=False)
                self._execute_update(_SQL_UPSERT_GROUP_USERS, (group_id, users_json, users_json))
        except Exception as e:
            logger.error(f"添加用户到群组失败: {e}, group_id: {group_id}, user_id: {user_id}")

    def get_group_member_count(self, group_id):
        result = self._execute_query(_SQL_SELECT_GROUP_USERS, (group_id,))
        if result and result.get('users'):
            try:
                return len(json.loads(result['users']))
            except:
                pass
        return 0
            
    def add_member(self, user_id):
        self._async_execute(self._add_member, user_id)
            
    def _add_member(self, user_id):
        self._execute_update(_SQL_INSERT_MEMBER, (user_id,))

    def get_member_count(self):
        result = self._execute_query(_SQL_COUNT_MEMBERS)
        return result.get('count', 0) if result else 0

    def fetch_user_name_from_api(self, user_id):
        try:
            response = get_json(_API_URL_TEMPLATE.format(user_id=user_id), timeout=3, verify=False)
            if isinstance(response, dict):
                for key in _NAME_KEYS:
                    name = response.get(key)
                    if name:
                        if isinstance(name, str):
                            return name
                        elif not isinstance(name, dict):
                            return str(name)
            return None
        except:
            return None

    def update_user_name(self, user_id, name=None):
        self._async_execute(self._update_user_name, user_id, name)

    def _update_user_name(self, user_id, name=None):
        if name is None:
            if self.get_user_name(user_id):
                return
            name = self.fetch_user_name_from_api(user_id)
        
        if name and isinstance(name, str):
            name = name.strip()
            if name:
                self._execute_update(_SQL_UPSERT_USER_NAME, (user_id, name, name))

    def get_user_name(self, user_id):
        result = self._execute_query(_SQL_SELECT_USER_NAME, (user_id,))
        return result.get('name') if result else None

def get_table_name(base_name):
    return Database._table_cache.get(base_name, f"{_TABLE_PREFIX}{base_name}")

USERS_TABLE = _USERS_TABLE
GROUPS_USERS_TABLE = _GROUPS_USERS_TABLE
MEMBERS_TABLE = _MEMBERS_TABLE
