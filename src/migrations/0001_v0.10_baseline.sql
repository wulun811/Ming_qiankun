-- 0001_v0.10_baseline.sql
-- v0.10 基线 schema：检测并 ALTER 补齐缺失字段（幂等）
-- 仅支持从 v0.10.3+ 升级

-- events 表（如果不存在则创建）
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
);

-- probe_health 表（如果不存在则创建）
CREATE TABLE IF NOT EXISTS probe_health (
    id INTEGER PRIMARY KEY,
    system TEXT,
    window_start REAL,
    emit_count INTEGER,
    drop_count INTEGER,
    disk_free_mb REAL,
    last_errors JSON,
    integrity_score REAL
);

-- 补齐 events 表可能缺失的字段（使用 PRAGMA 检测，兼容所有 SQLite 版本）
-- 注意：migrate.py 会在 SQLite <3.35.0 时自动移除 IF NOT EXISTS
