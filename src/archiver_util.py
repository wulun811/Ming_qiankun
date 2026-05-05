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
        pass


def run_vacuum(db_path, last_vacuum, vacuum_interval):
    """节流 VACUUM，返回释放的字节数（或 None 跳过）"""
    now = time.time()
    if now - last_vacuum < vacuum_interval:
        return None
    db = Path(db_path)
    before = db.stat().st_size
    try:
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("VACUUM")
        conn.close()
    except Exception:
        return None
    after = db.stat().st_size
    return before - after


def update_expectation(cursor, system, expected_event):
    if not expected_event:
        return
    cursor.execute(
        "UPDATE expectations SET fulfilled = 1 WHERE system = ? AND expected_event = ? AND fulfilled = 0",
        (system, expected_event),
    )
