# archiver_schema.py —— 0.11.9m 数据库 Schema 初始化
# 职责：CREATE TABLE / INDEX 语句（一次性的 DDL）
# 安全：不 import sqlite3，conn 由 archiver.py 传入
import time


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
    conn.execute("PRAGMA temp_store = MEMORY")
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_anchors (
            file_path     TEXT PRIMARY KEY,
            sha256        TEXT NOT NULL,
            registered_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at    TEXT
        )
    """)
    conn.commit()
