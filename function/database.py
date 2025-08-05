import json
import time
from concurrent.futures import ThreadPoolExecutor
import logging

from function.db_pool import ConnectionManager, DatabaseService

logger = logging.getLogger('database')

class Database:
    _instance = None
    _thread_pool = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance._init_database()
        return cls._instance

    def _init_database(self):
        """初始化数据库"""
        try:
            if self._thread_pool is None:
                self._thread_pool = ThreadPoolExecutor(max_workers=3)
            self._initialize_tables()
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            self._thread_pool = None

    def _initialize_tables(self):
        """初始化数据库表"""
        tables = {
            'M_users': "CREATE TABLE IF NOT EXISTS M_users (user_id VARCHAR(255) NOT NULL UNIQUE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
            'M_groups': "CREATE TABLE IF NOT EXISTS M_groups (group_id VARCHAR(255) NOT NULL UNIQUE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci", 
            'M_groups_users': """CREATE TABLE IF NOT EXISTS M_groups_users (
                group_id VARCHAR(255) NOT NULL UNIQUE,
                users JSON DEFAULT NULL,
                PRIMARY KEY (group_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci""",
            'M_members': """CREATE TABLE IF NOT EXISTS M_members (
                user_id VARCHAR(255) NOT NULL UNIQUE,
                PRIMARY KEY (user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"""
        }
        
        try:
            with ConnectionManager() as manager:
                if not manager.connection:
                    logger.error("无法获取数据库连接")
                    return
                    
                for table_name, sql in tables.items():
                    manager.execute(sql)
                manager.commit()
        except Exception as e:
            logger.error(f"初始化数据库表失败: {e}")

    def _safe_execute(self, func, *args, **kwargs):
        """安全执行数据库操作"""
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logger.error(f"数据库操作失败: {e}")
            return None

    def _async_execute(self, func, *args):
        """异步执行数据库操作"""
        if self._thread_pool:
            self._thread_pool.submit(func, *args)

    def _simple_insert(self, table, column, value):
        """通用的简单插入操作"""
        sql = f"INSERT IGNORE INTO {table} ({column}) VALUES (%s)"
        return DatabaseService.execute_update(sql, (value,))

    def _count_records(self, table, condition=None, params=None):
        """通用的记录计数查询"""
        sql = f"SELECT COUNT(*) AS count FROM {table}"
        if condition:
            sql += f" WHERE {condition}"
        result = DatabaseService.execute_query(sql, params)
        return result.get('count', 0) if result else 0

    def _record_exists(self, table, column, value):
        """检查记录是否存在"""
        sql = f"SELECT {column} FROM {table} WHERE {column} = %s"
        result = DatabaseService.execute_query(sql, (value,))
        return bool(result)

    # === 用户管理 ===
    def add_user(self, user_id):
        """添加用户"""
        self._async_execute(self._add_user, user_id)

    def _add_user(self, user_id):
        """执行添加用户"""
        self._safe_execute(self._simple_insert, 'M_users', 'user_id', user_id)

    def get_user_count(self):
        """获取用户总数"""
        return self._safe_execute(self._count_records, 'M_users') or 0

    def exists_user(self, user_id):
        """检查用户是否存在"""
        return self._safe_execute(self._record_exists, 'M_users', 'user_id', user_id) or False

    # === 群组管理 ===
    def add_group(self, group_id):
        """添加群组"""
        self._async_execute(self._add_group, group_id)

    def _add_group(self, group_id):
        """执行添加群组"""
        self._safe_execute(self._simple_insert, 'M_groups', 'group_id', group_id)

    def get_group_count(self):
        """获取群组总数"""
        return self._safe_execute(self._count_records, 'M_groups') or 0

    def add_user_to_group(self, group_id, user_id):
        """添加用户到群组"""
        self._async_execute(self._add_user_to_group, group_id, user_id)

    def _add_user_to_group(self, group_id, user_id):
        """执行添加用户到群组"""
        try:
            sql = "SELECT users FROM M_groups_users WHERE group_id = %s"
            result = DatabaseService.execute_query(sql, (group_id,))
            
            if not result:
                # 创建新群组记录
                users = [{"value": 1, "userid": user_id}]
                sql = "INSERT INTO M_groups_users (group_id, users) VALUES (%s, %s)"
                DatabaseService.execute_update(sql, (group_id, json.dumps(users)))
            else:
                # 更新现有群组
                users = json.loads(result.get('users')) if result.get('users') else []
                if not any(u.get("userid") == user_id for u in users):
                    users.append({"value": 1, "userid": user_id})
                    sql = "UPDATE M_groups_users SET users = %s WHERE group_id = %s"
                    DatabaseService.execute_update(sql, (json.dumps(users), group_id))
                    
        except Exception as e:
            logger.error(f"添加用户到群组失败: {e}")

    def get_group_member_count(self, group_id):
        """获取群组成员数量"""
        try:
            sql = "SELECT users FROM M_groups_users WHERE group_id = %s"
            result = DatabaseService.execute_query(sql, (group_id,))
            if result and result.get('users'):
                users = json.loads(result.get('users'))
                return len(users)
            return 0
        except Exception as e:
            logger.error(f"获取群组成员数量失败: {e}")
            return 0

    # === 成员管理 ===
    def add_member(self, user_id):
        """添加没有群聊的用户"""
        self._async_execute(self._add_member, user_id)
            
    def _add_member(self, user_id):
        """执行添加成员"""
        self._safe_execute(self._simple_insert, 'M_members', 'user_id', user_id)

    def get_member_count(self):
        """获取没有群聊的用户数量"""
        return self._safe_execute(self._count_records, 'M_members') or 0 