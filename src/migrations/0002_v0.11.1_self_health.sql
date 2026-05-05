-- 0002_v0.11.1_self_health.sql
-- 自健康检测字段
-- 注意：如果字段已存在会报错，migrate.py 会捕获并跳过

ALTER TABLE probe_health ADD COLUMN archiver_lag_seconds REAL;
ALTER TABLE probe_health ADD COLUMN wal_size_mb REAL;
ALTER TABLE probe_health ADD COLUMN vacuum_due INTEGER DEFAULT 0;
ALTER TABLE probe_health ADD COLUMN otel_bridge_queue_size INTEGER;
ALTER TABLE probe_health ADD COLUMN hot_dir_files INTEGER;
ALTER TABLE probe_health ADD COLUMN hot_dir_size_mb REAL;
