import json
import time
from concurrent.futures import ThreadPoolExecutor
import logging

# 导入优化后的数据库连接池
from db_pool import ConnectionManager, DatabaseService

# 配置日志
logger = logging.getLogger('database')

class Database:
    _instance = None
    _thread_pool = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Database, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
            
        try:
            # 使用线程池执行异步操作
            if self._thread_pool is None:
                self._thread_pool = ThreadPoolExecutor(max_workers=3)
            
            # 初始化表结构
            self.initialize_tables()
            self._initialized = True
            logger.info("Database类初始化成功")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            self._thread_pool = None

    def initialize_tables(self):
        """初始化数据库表"""
        try:
            with ConnectionManager() as manager:
                if not manager.connection:
                    logger.error("初始化表结构失败：无法获取数据库连接")
                    return
                    
                # 创建用户表
                manager.execute("""
                    CREATE TABLE IF NOT EXISTS M_users (
                        user_id VARCHAR(255) NOT NULL UNIQUE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)

                # 创建群组表
                manager.execute("""
                    CREATE TABLE IF NOT EXISTS M_groups (
                        group_id VARCHAR(255) NOT NULL UNIQUE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)

                # 创建群组成员表
                manager.execute("""
                    CREATE TABLE IF NOT EXISTS M_groups_users (
                        group_id VARCHAR(255) NOT NULL UNIQUE,
                        users JSON DEFAULT NULL,
                        PRIMARY KEY (group_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)
                
                # 创建没有群聊的用户表
                manager.execute("""
                    CREATE TABLE IF NOT EXISTS M_members (
                        user_id VARCHAR(255) NOT NULL UNIQUE,
                        PRIMARY KEY (user_id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                """)

                manager.commit()
                logger.info("数据库表结构初始化成功")
        except Exception as e:
            logger.error(f"初始化数据库表失败: {e}")

    def add_user(self, user_id):
        """添加用户到数据库"""
        if self._thread_pool:
            self._thread_pool.submit(self._add_user, user_id)

    def _add_user(self, user_id):
        """实际添加用户的数据库操作"""
        try:
            sql = "INSERT IGNORE INTO M_users (user_id) VALUES (%s)"
            DatabaseService.execute_update(sql, (user_id,))
        except Exception as e:
            logger.error(f"添加用户失败: {e}")

    def add_group(self, group_id):
        """添加群组到数据库"""
        if self._thread_pool:
            self._thread_pool.submit(self._add_group, group_id)

    def _add_group(self, group_id):
        """实际添加群组的数据库操作"""
        try:
            sql = "INSERT IGNORE INTO M_groups (group_id) VALUES (%s)"
            DatabaseService.execute_update(sql, (group_id,))
        except Exception as e:
            logger.error(f"添加群组失败: {e}")

    def add_user_to_group(self, group_id, user_id):
        """添加用户到群组"""
        if self._thread_pool:
            self._thread_pool.submit(self._add_user_to_group, group_id, user_id)

    def _add_user_to_group(self, group_id, user_id):
        """实际添加用户到群组的数据库操作"""
        try:
            # 检查群组是否存在
            sql = "SELECT users FROM M_groups_users WHERE group_id = %s"
            result = DatabaseService.execute_query(sql, (group_id,))
            
            if not result:
                # 群组不存在，创建新记录
                users = [{"value": 1, "userid": user_id}]
                sql = "INSERT INTO M_groups_users (group_id, users) VALUES (%s, %s)"
                DatabaseService.execute_update(sql, (group_id, json.dumps(users)))
            else:
                # 群组存在，更新用户列表
                users = json.loads(result.get('users')) if result.get('users') else []
                if not any(u.get("userid") == user_id for u in users):
                    users.append({"value": 1, "userid": user_id})
                    sql = "UPDATE M_groups_users SET users = %s WHERE group_id = %s"
                    DatabaseService.execute_update(sql, (json.dumps(users), group_id))
            
        except Exception as e:
            logger.error(f"添加用户到群组失败: {e}")
            
    def add_member(self, user_id):
        """添加没有群聊的用户到M_members表"""
        if self._thread_pool:
            self._thread_pool.submit(self._add_member, user_id)
            
    def _add_member(self, user_id):
        """实际添加没有群聊用户的数据库操作"""
        try:
            sql = "INSERT IGNORE INTO M_members (user_id) VALUES (%s)"
            DatabaseService.execute_update(sql, (user_id,))
        except Exception as e:
            logger.error(f"添加没有群聊的用户失败: {e}")

    def get_user_count(self):
        """获取用户总数"""
        try:
            sql = "SELECT COUNT(*) AS count FROM M_users"
            result = DatabaseService.execute_query(sql)
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f"获取用户数量失败: {e}")
            return 0

    def get_group_count(self):
        """获取群组总数"""
        try:
            sql = "SELECT COUNT(*) AS count FROM M_groups"
            result = DatabaseService.execute_query(sql)
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f"获取群组数量失败: {e}")
            return 0

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
            
    def get_member_count(self):
        """获取没有群聊的用户数量"""
        try:
            sql = "SELECT COUNT(*) AS count FROM M_members"
            result = DatabaseService.execute_query(sql)
            return result.get('count', 0) if result else 0
        except Exception as e:
            logger.error(f"获取没有群聊的用户数量失败: {e}")
            return 0

    def exists_user(self, user_id):
        """判断用户是否已存在于M_users表"""
        try:
            sql = "SELECT user_id FROM M_users WHERE user_id = %s"
            result = DatabaseService.execute_query(sql, (user_id,))
            return bool(result)
        except Exception as e:
            logger.error(f"检查用户是否存在失败: {e}")
            return False 