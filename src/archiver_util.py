# archiver_util.py —— 0.11.9m 归档器小工具
# 职责：心跳/错误日志/vacuum/期约更新（纯工具函数）
# 安全：输入输出明确，无隐式 DB 访问
import json, time, sqlite3
from pathlib import Path


def write_heartbeat(heartbeat_path):
    try:
        Path(heartbeat_path).write_text(str(time.time()))
    except Exception:
        pass


def log_error(error_log_path, error, consecutive_count=0):
    try:
        Path(error_log_path).parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "error": str(error),
            "consecutive_count": consecutive_count,
            "type": type(error).__name__,
        }
        with open(error_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        try:
            import sys

            print(f"[archiver] log_error failed: {error}", file=sys.stderr)
        except Exception:
            pass


def run_vacuum(db_path, last_vacuum, vacuum_interval):
    """节流 VACUUM，返回释放的字节数（或 None 跳过）"""
    now = time.time()
    if now - last_vacuum < vacuum_interval:
        return None
    db = Path(db_path)
    try:
        before = db.stat().st_size
    except OSError:
        return None
    # VACUUM 需要约 2x DB 大小的磁盘空间
    try:
        import shutil

        free = shutil.disk_usage(str(db.parent)).free
        if free < before * 2:
            return None  # 磁盘空间不足，跳过
    except Exception:
        pass
    try:
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("VACUUM")
        conn.close()
    except Exception:
        return None
    try:
        after = db.stat().st_size
    except OSError:
        return None
    return before - after


def update_expectation(cursor, system, expected_event):
    if not expected_event:
        return
    cursor.execute(
        "UPDATE expectations SET fulfilled = 1 WHERE system = ? AND expected_event = ? AND fulfilled = 0",
        (system, expected_event),
    )


def diagnoses_query(conn, columns, where="", params=(), order_limit=""):
    """跨年查询 diagnoses 表，自动发现所有 diagnoses_{year} 表并 UNION ALL。
    返回 rows。解决年份边界（1月1日）去年数据不可见的问题。"""
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'diagnoses_%'"
            ).fetchall()
        ]
    except Exception:
        tables = []
    if not tables:
        # fallback: 至少查当前年
        tables = [f"diagnoses_{time.strftime('%Y')}"]
    tables.sort(reverse=True)  # 最新年份优先
    parts = []
    all_params = []
    for tbl in tables:
        w = f" WHERE {where}" if where else ""
        parts.append(f"SELECT {columns} FROM {tbl}{w}")
        all_params.extend(params)  # 每个子查询都需要自己的参数
    sql = " UNION ALL ".join(parts)
    if order_limit:
        sql = f"SELECT * FROM ({sql}) _diag {order_limit}"
    try:
        rows = conn.execute(sql, tuple(all_params)).fetchall()
        return rows
    except Exception:
        return []
