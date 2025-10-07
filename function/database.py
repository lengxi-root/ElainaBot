import json, logging
from concurrent.futures import ThreadPoolExecutor
from function.db_pool import ConnectionManager, DatabaseService
from function.httpx_pool import get_json
from config import DB_CONFIG, appid

logger = logging.getLogger('database')

class Database:
    _instance = None
    _thread_pool = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance._init_database()
        return cls._instance

    @staticmethod
    def get_table_name(base_name):
        prefix = DB_CONFIG.get('table_prefix', 'M_')
        return f"{prefix}{base_name}"

    def _init_database(self):
        if self._thread_pool is None:
            self._thread_pool = ThreadPoolExecutor(max_workers=None, thread_name_prefix="Database")
        self._initialize_tables()

    def _initialize_tables(self):
        tables = {
            self.get_table_name('users'): f"CREATE TABLE IF NOT EXISTS {self.get_table_name('users')} (user_id VARCHAR(255) NOT NULL UNIQUE, name VARCHAR(255) DEFAULT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
            self.get_table_name('groups'): f"CREATE TABLE IF NOT EXISTS {self.get_table_name('groups')} (group_id VARCHAR(255) NOT NULL UNIQUE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci", 
            self.get_table_name('groups_users'): f"CREATE TABLE IF NOT EXISTS {self.get_table_name('groups_users')} (group_id VARCHAR(255) NOT NULL UNIQUE, users JSON DEFAULT NULL, PRIMARY KEY (group_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
            self.get_table_name('members'): f"CREATE TABLE IF NOT EXISTS {self.get_table_name('members')} (user_id VARCHAR(255) NOT NULL UNIQUE, PRIMARY KEY (user_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
        }
        try:
            with ConnectionManager() as manager:
                if not manager.connection:
                    logger.error("数据库连接失败，无法初始化表")
                    return
                    
                for sql in tables.values():
                    manager.execute(sql)
                
                # 检查并添加 name 列
                self._add_name_column_if_not_exists(manager)
                manager.commit()
                logger.info("数据库表初始化完成")
        except Exception as e:
            import traceback
            logger.error(f"初始化数据库表失败: {type(e).__name__}: {e}\n{traceback.format_exc()}")

    def _add_name_column_if_not_exists(self, manager):
        """检查并添加 name 列到 users 表"""
        users_table = self.get_table_name('users')
        database_name = DB_CONFIG.get('database', 'web')
        
        cursor = manager.connection.cursor()
        try:
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
                manager.execute(f"ALTER TABLE {users_table} ADD COLUMN name VARCHAR(255) DEFAULT NULL")
                logger.info(f"已为表 {users_table} 添加 name 列")
        finally:
            cursor.close()

    def _async_execute(self, func, *args):
        if self._thread_pool:
            self._thread_pool.submit(func, *args)

    def add_user(self, user_id):
        self._async_execute(self._add_user, user_id)

    def _add_user(self, user_id):
        DatabaseService.execute_update(
            f"INSERT IGNORE INTO {self.get_table_name('users')} (user_id) VALUES (%s)", 
            (user_id,)
        )
        self._update_user_name(user_id)

    def get_user_count(self):
        result = DatabaseService.execute_query(f"SELECT COUNT(*) AS count FROM {self.get_table_name('users')}")
        return result.get('count', 0) if result else 0

    def exists_user(self, user_id):
        result = DatabaseService.execute_query(
            f"SELECT user_id FROM {self.get_table_name('users')} WHERE user_id = %s", 
            (user_id,)
        )
        return bool(result)

    def add_group(self, group_id):
        self._async_execute(self._add_group, group_id)

    def _add_group(self, group_id):
        DatabaseService.execute_update(
            f"INSERT IGNORE INTO {self.get_table_name('groups')} (group_id) VALUES (%s)", 
            (group_id,)
        )

    def get_group_count(self):
        result = DatabaseService.execute_query(f"SELECT COUNT(*) AS count FROM {self.get_table_name('groups')}")
        return result.get('count', 0) if result else 0

    def add_user_to_group(self, group_id, user_id):
        self._async_execute(self._add_user_to_group, group_id, user_id)

    def _add_user_to_group(self, group_id, user_id):
        groups_users_table = self.get_table_name('groups_users')
        result = DatabaseService.execute_query(
            f"SELECT users FROM {groups_users_table} WHERE group_id = %s", 
            (group_id,)
        )
        
        if result and result.get('users'):
            users = json.loads(result.get('users'))
            if not any(u.get("userid") == user_id for u in users):
                users.append({"value": 1, "userid": user_id})
                users_json = json.dumps(users)
                DatabaseService.execute_update(
                    f"INSERT INTO {groups_users_table} (group_id, users) VALUES (%s, %s) ON DUPLICATE KEY UPDATE users = %s", 
                    (group_id, users_json, users_json)
                )
        else:
            users_json = json.dumps([{"value": 1, "userid": user_id}])
            DatabaseService.execute_update(
                f"INSERT INTO {groups_users_table} (group_id, users) VALUES (%s, %s) ON DUPLICATE KEY UPDATE users = %s",
                (group_id, users_json, users_json)
            )

    def get_group_member_count(self, group_id):
        result = DatabaseService.execute_query(
            f"SELECT users FROM {self.get_table_name('groups_users')} WHERE group_id = %s", 
            (group_id,)
        )
        if result and result.get('users'):
            return len(json.loads(result.get('users')))
        return 0
            
    def add_member(self, user_id):
        self._async_execute(self._add_member, user_id)
            
    def _add_member(self, user_id):
        DatabaseService.execute_update(
            f"INSERT IGNORE INTO {self.get_table_name('members')} (user_id) VALUES (%s)", 
            (user_id,)
        )

    def get_member_count(self):
        result = DatabaseService.execute_query(f"SELECT COUNT(*) AS count FROM {self.get_table_name('members')}")
        return result.get('count', 0) if result else 0

    def fetch_user_name_from_api(self, user_id):
        """从 API 获取用户昵称"""
        try:
            url = f"http://127.0.0.1:65535/api/bot/xx.php?openid={user_id}&appid={appid}"
            response = get_json(url, timeout=3, verify=False)
            if isinstance(response, dict):
                return response.get('名字') or response.get('name') or response.get('nickname')
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
        
        if name:
            users_table = self.get_table_name('users')
            DatabaseService.execute_update(
                f"INSERT INTO {users_table} (user_id, name) VALUES (%s, %s) ON DUPLICATE KEY UPDATE name = %s",
                (user_id, name, name)
            )

    def get_user_name(self, user_id):
        """获取用户昵称"""
        result = DatabaseService.execute_query(
            f"SELECT name FROM {self.get_table_name('users')} WHERE user_id = %s", 
            (user_id,)
        )
        return result.get('name') if result else None

def get_table_name(base_name):
    prefix = DB_CONFIG.get('table_prefix', 'M_')
    return f"{prefix}{base_name}"

USERS_TABLE = get_table_name('users')
GROUPS_TABLE = get_table_name('groups')
GROUPS_USERS_TABLE = get_table_name('groups_users')
MEMBERS_TABLE = get_table_name('members')
