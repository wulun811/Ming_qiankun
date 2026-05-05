# cluster_reflector.py —— 0.11.9m MySQL 读取适配
# 职责：从中央 MySQL 读取事件，供诊断引擎使用
# 依赖：pymysql

try:
    import pymysql
    import pymysql.cursors
except ImportError:
    pymysql = None


class ClusterReflector:
    def __init__(self, pool, project_id: str = "default", table: str = "wq_events"):
        self.pool = pool
        self.project_id = project_id
        self.table = table

    def query_recent(self, limit: int = 100):
        conn = self.pool.get()
        if not conn:
            return []
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM {self.table} WHERE project_id = %s ORDER BY id DESC LIMIT %s",
                    (self.project_id, limit),
                )
                return cur.fetchall()
        finally:
            self.pool.put(conn)

    def query_by_system(self, system: str, limit: int = 100):
        conn = self.pool.get()
        if not conn:
            return []
        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM {self.table} WHERE project_id = %s AND `system` = %s ORDER BY id DESC LIMIT %s",
                    (self.project_id, system, limit),
                )
                return cur.fetchall()
        finally:
            self.pool.put(conn)
