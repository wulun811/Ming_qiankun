# cluster_pool.py —— 0.11.9m 轻量 MySQL 连接池
# 职责：线程安全连接池，默认 5 连接/项目
# 依赖：pymysql, queue

try:
    import pymysql
except ImportError:
    pymysql = None
import queue
import logging

logger = logging.getLogger(__name__)


class SimplePool:
    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        max_conn: int = 5,
    ):
        if pymysql is None:
            raise ImportError("Cluster 模式需要 pymysql 依赖: pip install pymysql")
        self._queue = queue.Queue(maxsize=max_conn)
        self._conn_args = {
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "database": database,
            "charset": "utf8mb4",
            "autocommit": False,
            "connect_timeout": 5,
        }
        conns_created = []
        try:
            for _ in range(max_conn):
                conn = pymysql.connect(**self._conn_args)
                conns_created.append(conn)
                self._queue.put(conn)
        except Exception:
            for c in conns_created:
                try:
                    c.close()
                except Exception:
                    pass
            raise

    def get(self, timeout: int = 5):
        """获取一个连接，如果池空则返回 None"""
        try:
            conn = self._queue.get(timeout=timeout)
            try:
                conn.ping(reconnect=True)
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = pymysql.connect(**self._conn_args)
            return conn
        except queue.Empty:
            return None

    def put(self, conn, timeout: int = 1):
        if conn:
            try:
                self._queue.put(conn, timeout=timeout)
            except queue.Full:
                try:
                    conn.close()
                except Exception:
                    pass

    def close_all(self):
        while True:
            try:
                self._queue.get_nowait().close()
            except queue.Empty:
                break
