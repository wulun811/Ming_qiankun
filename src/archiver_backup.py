# archiver_backup.py —— 0.11.9m 数据库备份
# 职责：只读连接原库 → sqlite3.backup() → .bak 文件
# 安全：打开原库用 read_only=True，只写 .bak 副本，不碰原库
import sqlite3
import time
from pathlib import Path

BACKUP_INTERVAL = 3600


def run_backup(db_path, last_backup, log_error_fn=None):
    """备份数据库到 .bak 文件（默认 1 小时节流）"""
    now = time.time()
    if now - last_backup < BACKUP_INTERVAL:
        return last_backup

    db = Path(db_path)
    try:
        bak = db.parent / f"ming_{time.strftime('%Y%m%d_%H%M%S')}.db.bak"
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        src.execute("PRAGMA busy_timeout=5000")
        try:
            dst = sqlite3.connect(str(bak))
            dst.execute("PRAGMA busy_timeout=5000")
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        backups = sorted(db.parent.glob("ming_*.db.bak"))
        for old in backups[:-5]:
            old.unlink()
    except Exception as e:
        if log_error_fn:
            log_error_fn(e)

    return now
