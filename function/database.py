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
            # 对于主键重复错误，只记录警告而不是错误
            if "1062" in str(e) and "Duplicate entry" in str(e):
                logger.warning(f"数据库记录已存在（忽略）: {e}")
                return 0  # 返回0表示没有新记录被插入，但这是正常的
            else:
                logger.error(f"数据库操作失败: {e}")
            return None

    def _async_execute(self, func, *args):
        """异步执行数据库操作"""
        if self._thread_pool:
            def safe_wrapper(*args):
                try:
                    return func(*args)
                except Exception as e:
                    # 对于异步操作，将主键重复错误降级为警告
                    if "1062" in str(e) and "Duplicate entry" in str(e):
                        logger.warning(f"异步操作中记录已存在（忽略）: {e}")
                    else:
                        logger.error(f"异步数据库操作失败: {e}")
            
            self._thread_pool.submit(safe_wrapper, *args)

    def _simple_insert(self, table, column, value):
        """通用的简单插入操作（并发安全）"""
        try:
            sql = f"INSERT IGNORE INTO {table} ({column}) VALUES (%s)"
            result = DatabaseService.execute_update(sql, (value,))
            return result
        except Exception as e:
            # 特别处理主键重复错误
            if "1062" in str(e) and "Duplicate entry" in str(e):
                logger.debug(f"记录已存在，跳过插入: {table}.{column} = {value}")
                return 0  # 返回0表示没有新记录被插入，但这是预期的
            else:
                logger.error(f"插入操作失败: {table}.{column} = {value}, 错误: {e}")
                raise e

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
        """执行添加用户到群组（并发安全版本）"""
        try:
            # 先查询当前群组的用户列表
            sql = "SELECT users FROM M_groups_users WHERE group_id = %s"
            result = DatabaseService.execute_query(sql, (group_id,))
            
            if result and result.get('users'):
                # 群组已存在，检查用户是否已在群组中
                users = json.loads(result.get('users'))
                if not any(u.get("userid") == user_id for u in users):
                    # 用户不在群组中，添加用户
                    users.append({"value": 1, "userid": user_id})
                    users_json = json.dumps(users)
                    
                    # 使用 ON DUPLICATE KEY UPDATE 避免并发冲突
                    sql = """INSERT INTO M_groups_users (group_id, users) VALUES (%s, %s)
                             ON DUPLICATE KEY UPDATE users = %s"""
                    DatabaseService.execute_update(sql, (group_id, users_json, users_json))
            else:
                # 群组不存在或用户列表为空，创建新的用户列表
                users = [{"value": 1, "userid": user_id}]
                users_json = json.dumps(users)
                
                # 使用 INSERT IGNORE 避免主键重复错误
                sql = "INSERT IGNORE INTO M_groups_users (group_id, users) VALUES (%s, %s)"
                affected_rows = DatabaseService.execute_update(sql, (group_id, users_json))
                
                # 如果插入失败（说明记录已存在），则更新记录
                if affected_rows == 0:
                    sql = "UPDATE M_groups_users SET users = %s WHERE group_id = %s"
                    DatabaseService.execute_update(sql, (users_json, group_id))
                    
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