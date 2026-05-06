# archiver_ttl.py —— 0.11.9m 过期事件清理
# 职责：DELETE 过期事件 + UPDATE ttl_protected 标记
# 安全：不 import sqlite3，不知道 DB 路径，conn 由 archiver.py 传入
import time


def purge_expired(conn, tbl, cutoff_days=60):
    """清理超过 cutoff_days 天的事件，保护关联诊断引用的记录"""
    protected = set()
    try:
        rows = conn.execute(f"""
            SELECT json_extract(value, '$.event_id') as eid
            FROM {tbl}, json_each({tbl}.evidence)
            WHERE status != 'false_positive'
        """).fetchall()
        protected = {int(r[0]) for r in rows if r[0] is not None}
    except Exception:
        pass

    cutoff = time.time() - cutoff_days * 86400
    if protected:
        placeholders = ",".join("?" * len(protected))
        conn.execute(
            f"""
            DELETE FROM events WHERE timestamp < ? AND ttl_protected = 0 AND id NOT IN ({placeholders})
        """,
            [cutoff] + list(protected),
        )
        conn.execute(
            f"""
            UPDATE events SET ttl_protected = 1 WHERE id IN ({placeholders})
        """,
            list(protected),
        )
    else:
        conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
    conn.commit()
