import mysql.connector
from mysql.connector import pooling
import json
from concurrent.futures import ThreadPoolExecutor
from config import DB_CONFIG
import time

class Database:
    _instance = None
    _pool = None
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
            if self._pool is None:
                self._pool = mysql.connector.pooling.MySQLConnectionPool(
                    **DB_CONFIG
                )
            if self._thread_pool is None:
                self._thread_pool = ThreadPoolExecutor(max_workers=5)
            
            self.initialize_tables()
            self._initialized = True
        except Exception as e:
            print(f"数据库初始化失败: {e}")
            self._pool = None
            self._thread_pool = None

    def get_connection(self):
        """获取数据库连接，带重试机制"""
        max_retries = 3
        retry_delay = 1
        
        for i in range(max_retries):
            try:
                if self._pool is None:
                    self._pool = mysql.connector.pooling.MySQLConnectionPool(
                        **DB_CONFIG
                    )
                return self._pool.get_connection()
            except Exception as e:
                print(f"获取数据库连接失败 (尝试 {i+1}/{max_retries}): {e}")
                if i < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    raise

    def initialize_tables(self):
        """初始化数据库表"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 创建用户表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_users (
                    user_id VARCHAR(255) NOT NULL UNIQUE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # 创建群组表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_groups (
                    group_id VARCHAR(255) NOT NULL UNIQUE
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            # 创建群组成员表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS groups_users (
                    group_id VARCHAR(255) NOT NULL UNIQUE,
                    users JSON DEFAULT NULL,
                    PRIMARY KEY (group_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """)

            conn.commit()
        except Exception as e:
            print(f"初始化数据库表失败: {e}")
        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()

    def add_user(self, user_id):
        """添加用户到数据库"""
        if self._thread_pool:
            self._thread_pool.submit(self._add_user, user_id)

    def _add_user(self, user_id):
        """实际添加用户的数据库操作"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT IGNORE INTO bot_users (user_id) VALUES (%s)",
                (user_id,)
            )
            conn.commit()
        except Exception as e:
            print(f"添加用户失败: {e}")
        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()

    def add_group(self, group_id):
        """添加群组到数据库"""
        if self._thread_pool:
            self._thread_pool.submit(self._add_group, group_id)

    def _add_group(self, group_id):
        """实际添加群组的数据库操作"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT IGNORE INTO bot_groups (group_id) VALUES (%s)",
                (group_id,)
            )
            conn.commit()
        except Exception as e:
            print(f"添加群组失败: {e}")
        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()

    def add_user_to_group(self, group_id, user_id):
        """添加用户到群组"""
        if self._thread_pool:
            self._thread_pool.submit(self._add_user_to_group, group_id, user_id)

    def _add_user_to_group(self, group_id, user_id):
        """实际添加用户到群组的数据库操作"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # 检查群组是否存在
            cursor.execute(
                "SELECT users FROM groups_users WHERE group_id = %s",
                (group_id,)
            )
            result = cursor.fetchone()
            
            if result is None:
                # 群组不存在，创建新记录
                users = [{"value": 1, "userid": user_id}]
                cursor.execute(
                    "INSERT INTO groups_users (group_id, users) VALUES (%s, %s)",
                    (group_id, json.dumps(users))
                )
            else:
                # 群组存在，更新用户列表
                users = json.loads(result[0]) if result[0] else []
                if not any(u.get("userid") == user_id for u in users):
                    users.append({"value": 1, "userid": user_id})
                    cursor.execute(
                        "UPDATE groups_users SET users = %s WHERE group_id = %s",
                        (json.dumps(users), group_id)
                    )
            
            conn.commit()
        except Exception as e:
            print(f"添加用户到群组失败: {e}")
        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()

    def get_user_count(self):
        """获取用户总数"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM bot_users")
            count = cursor.fetchone()[0]
            return count
        except Exception as e:
            print(f"获取用户数量失败: {e}")
            return 0
        finally:
            cursor.close()
            conn.close()

    def get_group_count(self):
        """获取群组总数"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM bot_groups")
            count = cursor.fetchone()[0]
            return count
        except Exception as e:
            print(f"获取群组数量失败: {e}")
            return 0
        finally:
            cursor.close()
            conn.close()

    def get_group_member_count(self, group_id):
        """获取群组成员数量"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT users FROM groups_users WHERE group_id = %s",
                (group_id,)
            )
            result = cursor.fetchone()
            if result and result[0]:
                users = json.loads(result[0])
                return len(users)
            return 0
        except Exception as e:
            print(f"获取群组成员数量失败: {e}")
            return 0
        finally:
            cursor.close()
            conn.close() 