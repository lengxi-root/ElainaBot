import json, logging, threading
from concurrent.futures import ThreadPoolExecutor
from function.httpx_pool import get_json
from config import LOG_DB_CONFIG, DB_CONFIG, appid

logger = logging.getLogger('ElainaBot.function.database')

class Database:
    _instance = None
    _thread_pool = None
    _enabled = DB_CONFIG.get('enabled', True)  # 默认启用
    _cursor_lock = threading.Lock()  # 保护游标获取的线程锁

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
        prefix = LOG_DB_CONFIG['table_prefix']
        return f"{prefix}{base_name}"

    def _init_database(self):
        if self._thread_pool is None:
            self._thread_pool = ThreadPoolExecutor(max_workers=None, thread_name_prefix="Database")
        self._db_pool = None  # 延迟初始化连接池
        self._initialize_tables()
    
    def _get_db_pool(self):
        """获取数据库连接池（延迟初始化）"""
        if not self._enabled:
            logger.warning("主数据库未启用，无法获取连接池")
            return None
        if self._db_pool is None:
            from function.log_db import LogDatabasePool
            self._db_pool = LogDatabasePool()
        return self._db_pool
    
    def _get_cursor(self):
        """获取数据库连接和游标（需要手动管理资源）"""
        pool = self._get_db_pool()
        if pool is None:
            return None, None

        connection = pool.get_connection()
        if not connection:
            logger.error("无法获取数据库连接")
            return None, None

        try:
            cursor = connection.cursor()
            return cursor, connection
        except Exception as e:
            logger.error(f"创建游标异常: {e}")
            if connection:
                pool.release_connection(connection)
            return None, None

    def _initialize_tables(self):
        tables = {
            self.get_table_name('users'): f"CREATE TABLE IF NOT EXISTS {self.get_table_name('users')} (user_id VARCHAR(255) NOT NULL UNIQUE, name VARCHAR(255) DEFAULT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
            self.get_table_name('groups_users'): f"CREATE TABLE IF NOT EXISTS {self.get_table_name('groups_users')} (group_id VARCHAR(255) NOT NULL UNIQUE, users JSON DEFAULT NULL, PRIMARY KEY (group_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
            self.get_table_name('members'): f"CREATE TABLE IF NOT EXISTS {self.get_table_name('members')} (user_id VARCHAR(255) NOT NULL UNIQUE, PRIMARY KEY (user_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        }
        try:
            cursor, connection = self._get_cursor()
            if not cursor:
                logger.error("无法获取数据库游标，无法初始化表")
                return

            try:
                for sql in tables.values():
                    cursor.execute(sql)

                # 检查并添加 name 列
                self._add_name_column_if_not_exists(cursor, connection)
                connection.commit()
                logger.info("数据库表初始化完成")
            finally:
                if cursor:
                    cursor.close()
                if connection:
                    self._get_db_pool().release_connection(connection)
        except Exception as e:
            import traceback
            logger.error(f"初始化数据库表失败: {type(e).__name__}: {e}\n{traceback.format_exc()}")

    def _add_name_column_if_not_exists(self, cursor, connection):
        """检查并添加 name 列到 users 表"""
        users_table = self.get_table_name('users')
        database_name = LOG_DB_CONFIG.get('database', 'log')
        
        cursor.execute(
            "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = 'name'",
            (database_name, users_table)
        )
        result = cursor.fetchone()
        
        # 根据游标类型处理返回值
        if isinstance(result, dict):
            column_count = result.get('COUNT(*)', 0)
        elif isinstance(result, (list, tuple)) and len(result) > 0:
            column_count = result[0]
        else:
            column_count = 0
        
        if column_count == 0:
            cursor.execute(f"ALTER TABLE {users_table} ADD COLUMN name VARCHAR(255) DEFAULT NULL")
            logger.info(f"已为表 {users_table} 添加 name 列")

    def _async_execute(self, func, *args):
        if self._thread_pool:
            self._thread_pool.submit(func, *args)

    def _execute_query(self, sql, params=None):
        """执行查询并返回结果"""
        cursor, connection = self._get_cursor()
        if not cursor:
            return None

        try:
            cursor.execute(sql, params or ())
            return cursor.fetchone()
        except Exception as e:
            logger.error(f"数据库查询失败: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if connection:
                self._get_db_pool().release_connection(connection)

    def _execute_update(self, sql, params=None):
        """执行更新操作"""
        cursor, connection = self._get_cursor()
        if not cursor:
            return False

        try:
            cursor.execute(sql, params or ())
            connection.commit()
            return True
        except Exception as e:
            logger.error(f"数据库更新失败: {e}")
            return False
        finally:
            if cursor:
                cursor.close()
            if connection:
                self._get_db_pool().release_connection(connection)

    def add_user(self, user_id):
        self._async_execute(self._add_user, user_id)

    def _add_user(self, user_id):
        self._execute_update(
            f"INSERT IGNORE INTO {self.get_table_name('users')} (user_id) VALUES (%s)", 
            (user_id,)
        )
        self._update_user_name(user_id)

    def get_user_count(self):
        result = self._execute_query(f"SELECT COUNT(*) AS count FROM {self.get_table_name('users')}")
        return result.get('count', 0) if result else 0

    def exists_user(self, user_id):
        result = self._execute_query(
            f"SELECT user_id FROM {self.get_table_name('users')} WHERE user_id = %s", 
            (user_id,)
        )
        return bool(result)

    def get_group_count(self):
        """通过groups_users表统计群组数量"""
        result = self._execute_query(f"SELECT COUNT(*) AS count FROM {self.get_table_name('groups_users')}")
        return result.get('count', 0) if result else 0

    def add_user_to_group(self, group_id, user_id):
        self._async_execute(self._add_user_to_group, group_id, user_id)

    def _add_user_to_group(self, group_id, user_id):
        groups_users_table = self.get_table_name('groups_users')
        result = self._execute_query(
            f"SELECT users FROM {groups_users_table} WHERE group_id = %s", 
            (group_id,)
        )
        
        try:
            if result and result.get('users'):
                try:
                    users = json.loads(result.get('users'))
                    if not isinstance(users, list):
                        users = []
                except (json.JSONDecodeError, TypeError):
                    users = []
                
                if not any(u.get("userid") == user_id for u in users if isinstance(u, dict)):
                    users.append({"value": 1, "userid": str(user_id)})
                    users_json = json.dumps(users, ensure_ascii=False)
                    self._execute_update(
                        f"INSERT INTO {groups_users_table} (group_id, users) VALUES (%s, %s) ON DUPLICATE KEY UPDATE users = %s", 
                        (group_id, users_json, users_json)
                    )
            else:
                users_json = json.dumps([{"value": 1, "userid": str(user_id)}], ensure_ascii=False)
                self._execute_update(
                    f"INSERT INTO {groups_users_table} (group_id, users) VALUES (%s, %s) ON DUPLICATE KEY UPDATE users = %s",
                    (group_id, users_json, users_json)
                )
        except Exception as e:
            logger.error(f"添加用户到群组失败: {e}, group_id: {group_id}, user_id: {user_id}")

    def get_group_member_count(self, group_id):
        result = self._execute_query(
            f"SELECT users FROM {self.get_table_name('groups_users')} WHERE group_id = %s", 
            (group_id,)
        )
        if result and result.get('users'):
            return len(json.loads(result.get('users')))
        return 0
            
    def add_member(self, user_id):
        self._async_execute(self._add_member, user_id)
            
    def _add_member(self, user_id):
        self._execute_update(
            f"INSERT IGNORE INTO {self.get_table_name('members')} (user_id) VALUES (%s)", 
            (user_id,)
        )

    def get_member_count(self):
        result = self._execute_query(f"SELECT COUNT(*) AS count FROM {self.get_table_name('members')}")
        return result.get('count', 0) if result else 0

    def fetch_user_name_from_api(self, user_id):
        """从 API 获取用户昵称"""
        try:
            url = f"http://127.0.0.1:65535/api/bot/xx.php?openid={user_id}&appid={appid}"
            response = get_json(url, timeout=3, verify=False)
            if isinstance(response, dict):
                name = response.get('名字') or response.get('name') or response.get('nickname')
                # 确保返回的是字符串类型，而不是字典或其他类型
                if name and isinstance(name, str):
                    return name
                elif name:
                    # 如果不是字符串，尝试转换为字符串
                    return str(name) if not isinstance(name, dict) else None
            return None
        except Exception as e:
            logger.debug(f"获取用户昵称失败: {e}")
            return None

    def update_user_name(self, user_id, name=None):
        """更新用户昵称，如果不提供 name 则从 API 获取"""
        self._async_execute(self._update_user_name, user_id, name)

    def _update_user_name(self, user_id, name=None):
        if name is None:
            existing_name = self.get_user_name(user_id)
            if existing_name:
                return
            name = self.fetch_user_name_from_api(user_id)
        
        # 确保name是字符串类型，并且不为空
        if name and isinstance(name, str) and name.strip():
            # 对name进行额外的安全检查，避免包含非法字符
            name = str(name).strip()
            users_table = self.get_table_name('users')
            self._execute_update(
                f"INSERT INTO {users_table} (user_id, name) VALUES (%s, %s) ON DUPLICATE KEY UPDATE name = %s",
                (user_id, name, name)
            )

    def get_user_name(self, user_id):
        """获取用户昵称"""
        result = self._execute_query(
            f"SELECT name FROM {self.get_table_name('users')} WHERE user_id = %s", 
            (user_id,)
        )
        return result.get('name') if result else None

def get_table_name(base_name):
    prefix = LOG_DB_CONFIG['table_prefix']
    return f"{prefix}{base_name}"

USERS_TABLE = get_table_name('users')
GROUPS_USERS_TABLE = get_table_name('groups_users')
MEMBERS_TABLE = get_table_name('members')
