-- 0003_v0.11.1_privacy.sql
-- 隐私控制索引（必须在 forget/TTL 功能之前创建）

CREATE INDEX IF NOT EXISTS idx_events_session_id ON events(json_extract(payload, '$.layer_agent.session_id'));
CREATE INDEX IF NOT EXISTS idx_events_system_timestamp ON events(system, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
