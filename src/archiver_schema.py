# archiver_schema.py —— 0.11.10 数据库 Schema 初始化
# 职责：CREATE TABLE / INDEX 语句（一次性的 DDL）
# 安全：仅使用标准库，conn 由 archiver.py 传入
import os
import time
import sqlite3


def check_wal_integrity(db_path: str) -> bool:
    """检查 WAL/SHM 文件完整性，损坏时尝试恢复

    返回: True 如果数据库正常或已恢复, False 如果需要重建
    """
    from pathlib import Path

    db = Path(db_path)
    wal = db.with_suffix(".db-wal")
    shm = db.with_suffix(".db-shm")

    # 如果没有 WAL 文件，数据库是干净的
    if not wal.exists():
        return True

    # 检查 WAL 文件大小是否合理（不能为 0 或异常大）
    try:
        wal_size = wal.stat().st_size
        if wal_size == 0:
            # WAL 文件为空，删除它
            wal.unlink(missing_ok=True)
            shm.unlink(missing_ok=True)
            return True
    except OSError:
        # 无法读取 WAL 文件状态，尝试删除
        try:
            wal.unlink(missing_ok=True)
            shm.unlink(missing_ok=True)
        except OSError:
            pass
        return True

    # 尝试打开数据库并执行 WAL checkpoint 来验证完整性
    try:
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA busy_timeout = 1000")
        # 尝试读取数据库来验证一致性
        conn.execute("SELECT count(*) FROM sqlite_master")
        # 尝试 WAL checkpoint
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        return True
    except sqlite3.DatabaseError:
        # WAL 损坏，尝试恢复
        try:
            conn.close()
        except Exception:
            pass

        # 删除损坏的 WAL 和 SHM 文件
        try:
            wal.unlink(missing_ok=True)
            shm.unlink(missing_ok=True)
        except OSError:
            pass

        # 再次尝试打开数据库
        try:
            conn = sqlite3.connect(str(db))
            conn.execute("PRAGMA busy_timeout = 1000")
            conn.execute("SELECT count(*) FROM sqlite_master")
            conn.close()
            return True
        except sqlite3.DatabaseError:
            # 主数据库也损坏了
            try:
                conn.close()
            except Exception:
                pass
            return False


def init_diagnoses_table(cursor, year=None):
    """创建年度诊断表，返回表名"""
    tbl = f"diagnoses_{year or time.strftime('%Y')}"
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {tbl} (
            id INTEGER PRIMARY KEY,
            diagnosis_id TEXT UNIQUE NOT NULL,
            system TEXT NOT NULL,
            fault_id TEXT,
            diagnosis_name TEXT NOT NULL,
            confidence REAL CHECK(confidence >= 0 AND confidence <= 1),
            severity TEXT CHECK(severity IN ('P0','P1','P2','P3','META')),
            evidence JSON NOT NULL,
            evidence_hash TEXT,
            evidence_quality INTEGER DEFAULT 0,
            inference_chain TEXT,
            plugin_name TEXT,
            plugin_version TEXT,
            status TEXT DEFAULT 'pending',
            confirmed_by TEXT,
            confirmed_at REAL,
            prev_hash TEXT,
            curr_hash TEXT,
            created_at REAL DEFAULT (unixepoch())
        )
    """)
    return tbl


def init_schema(conn):
    """初始化所有表和索引（仅 CREATE，不 DROP/ALTER）"""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = DEFAULT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'white',
            event_type TEXT NOT NULL,
            payload JSON,
            content_hash TEXT,
            prev_hash TEXT,
            curr_hash TEXT NOT NULL,
            timestamp REAL NOT NULL,
            integrity TEXT DEFAULT 'pending',
            chain_status TEXT DEFAULT 'linked',
            integrity_score REAL DEFAULT 1.0,
            ttl_protected INTEGER DEFAULT 0,
            monotonic_ms REAL,
            lamport INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_pid (
            system TEXT PRIMARY KEY,
            pid INTEGER NOT NULL,
            registered_at REAL NOT NULL,
            last_seen REAL NOT NULL,
            mode TEXT NOT NULL DEFAULT 'white'
        )
    """)
    # v0.11.10: 新增 events 列（压缩兼容）
    for col_def in [
        "payload_hash TEXT NOT NULL DEFAULT ''",
        "payload_blob BLOB",
        "storage_tier INTEGER DEFAULT 0",
        "compress_attempts INTEGER DEFAULT 0",
    ]:
        try:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events_blob (
            event_id INTEGER PRIMARY KEY REFERENCES events(id),
            payload_blob BLOB
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS monthly_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year_month TEXT NOT NULL,
            system TEXT NOT NULL,
            event_type TEXT NOT NULL,
            total_count INTEGER DEFAULT 0,
            error_count INTEGER DEFAULT 0,
            avg_payload_bytes REAL DEFAULT 0,
            total_tokens_est INTEGER DEFAULT 0,
            min_timestamp REAL,
            max_timestamp REAL,
            summary_json TEXT,
            last_aggregated_at REAL,
            UNIQUE(year_month, system, event_type)
        )
    """)
    init_diagnoses_table(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expectations (
            id INTEGER PRIMARY KEY,
            system TEXT,
            expected_event TEXT,
            deadline REAL,
            fulfilled INTEGER DEFAULT 0,
            created_at REAL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_exp_deadline ON expectations(system, deadline)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS probe_health (
            id INTEGER PRIMARY KEY,
            system TEXT,
            window_start REAL,
            emit_count INTEGER,
            drop_count INTEGER,
            disk_free_mb REAL,
            last_errors JSON,
            integrity_score REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_system ON events(system)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_system_timestamp ON events(system, timestamp DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_integrity ON events(integrity_score)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_lamport ON events(lamport)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ph_system ON probe_health(system, window_start)"
    )
    conn.execute("""
        DELETE FROM events WHERE id NOT IN (
            SELECT MIN(id) FROM events GROUP BY curr_hash
        )
    """)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_events_curr_hash ON events(curr_hash)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_compress ON events(storage_tier, timestamp)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_anchors (
            file_path     TEXT PRIMARY KEY,
            sha256        TEXT NOT NULL,
            registered_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT
        )
    """)
    conn.commit()
