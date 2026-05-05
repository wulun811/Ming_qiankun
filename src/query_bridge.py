# query_bridge.py —— v0.11.9m 双轨查询
# 职责：优先查 SQLite 冷轨，归档延迟时自动降级到热轨 JSONL 兜底
# 只读，禁止任何 INSERT/UPDATE/DELETE

import json, time
from pathlib import Path


class QueryBridge:
    def __init__(self, db_path=None, hot_dir=None):
        import sqlite3

        self.db_path = Path(db_path) if db_path else (Path.home() / ".ming" / "ming.db")
        self.hot_dir = Path(hot_dir) if hot_dir else (Path.home() / ".ming" / "hot")
        self._conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self._conn.execute("PRAGMA query_only = ON")

    def query_events(
        self, system=None, event_type=None, limit=100, since=None, exclude_type=None
    ):
        """查询事件，优先 SQLite，无结果时查热轨"""
        sql = "SELECT * FROM events WHERE 1=1"
        params = []
        if system:
            sql += " AND system = ?"
            params.append(system)
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        # 默认排除 __meta_health__ 事件
        exclude = exclude_type or "__meta_health__"
        if exclude:
            sql += " AND event_type != ?"
            params.append(exclude)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        cols = [
            d[0] for d in self._conn.execute("SELECT * FROM events LIMIT 0").description
        ]
        results = [dict(zip(cols, r)) for r in rows]

        # 如果结果不足且 since 在归档延迟窗口内（< 2 秒）
        if len(results) < limit and since and (time.time() - since) < 2:
            hot_events = self._scan_hot_files(
                system, event_type, limit - len(results), since, exclude_type
            )
            results.extend(hot_events)

        return results

    def query_system_pid(self, system: str):
        """查询系统 PID 信息，优先 SQLite，无结果时查热轨 __register__"""
        row = self._conn.execute(
            "SELECT system, pid, registered_at, last_seen, mode FROM system_pid WHERE system = ?",
            (system,),
        ).fetchone()

        if row:
            return {
                "system": row[0],
                "pid": row[1],
                "registered_at": row[2],
                "last_seen": row[3],
                "mode": row[4],
                "source": "sqlite",
            }

        # SQLite 无记录？去热轨兜底
        return self._scan_hot_register(system)

    def query_latest_events(self, system: str, n: int = 10):
        """查询系统最新 n 条事件"""
        return self.query_events(system=system, limit=n)

    def query_token_breakdown(self, system=None, since=None, group_by="model"):
        """Token 消耗分解（按 model/hour/agent 分组）"""
        group_col, order_col = {
            "model": ("json_extract(payload, '$.layer_llm.model')", "tokens"),
            "hour": ("CAST(timestamp / 3600 AS INTEGER)", "tokens"),
            "agent": ("system", "tokens"),
        }.get(group_by, ("json_extract(payload, '$.layer_llm.model')", "tokens"))
        since_clause = "AND timestamp > ?" if since else ""
        sql = f"""
            SELECT {group_col} AS grp,
                   SUM(json_extract(payload, '$.layer_llm.input_tokens') +
                       json_extract(payload, '$.layer_llm.output_tokens')) AS tokens,
                   SUM(json_extract(payload, '$.layer_llm.input_tokens')) AS input_tokens,
                   SUM(json_extract(payload, '$.layer_llm.output_tokens')) AS output_tokens,
                   COUNT(*) AS calls
            FROM events
            WHERE event_type='llm_invoke'
              AND (system = ? OR ? IS NULL)
              {since_clause}
            GROUP BY grp
            ORDER BY {order_col} DESC
        """
        params = [system, system]
        if since:
            params.insert(2, since)
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["group", "tokens", "input_tokens", "output_tokens", "calls"]
        return [dict(zip(cols, r)) for r in rows]

    def query_tool_dangerous(self, system=None, since=None):
        """危险工具调用审计"""
        since_clause = "AND timestamp > ?" if since else ""
        sql = f"""
            SELECT system,
                   json_extract(payload, '$.layer_tool.tool_name') AS tool,
                   json_extract(payload, '$.layer_tool.tool_args') AS args,
                   json_extract(payload, '$.layer_tool.tool_result') AS result,
                   json_extract(payload, '$.layer_tool.tool_status') AS status,
                   json_extract(payload, '$.layer_tool.execution_ms') AS exec_ms,
                   timestamp
            FROM events
            WHERE event_type='tool_call'
              AND json_extract(payload, '$.layer_tool.tool_name') IN ('rm','unlink','exec','sudo','rm_rf','git_force_push','db_drop','chmod_777','eval')
              AND (system = ? OR ? IS NULL)
              {since_clause}
            ORDER BY timestamp DESC
        """
        params = [system, system]
        if since:
            params.insert(2, since)
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["system", "tool", "args", "result", "status", "exec_ms", "timestamp"]
        return [dict(zip(cols, r)) for r in rows]

    def query_step_sequence(self, system=None, session_id=None, since=None, limit=100):
        """Step 序列回放"""
        since_clause = "AND timestamp > ?" if since else ""
        session_clause = (
            "AND json_extract(payload, '$.layer_agent.session_id') = ?"
            if session_id
            else ""
        )
        sql = f"""
            SELECT system,
                   json_extract(payload, '$.layer_agent.step_id') AS step_id,
                   json_extract(payload, '$.layer_agent.session_id') AS session_id,
                   timestamp,
                   event_type
            FROM events
            WHERE event_type IN ('agent_step', 'tool_call', 'llm_invoke')
              AND (system = ? OR ? IS NULL)
              {session_clause}
              {since_clause}
            ORDER BY timestamp ASC
            LIMIT ?
        """
        params = [system, system]
        if session_id:
            params.insert(2, session_id)
        if since:
            params.append(since)
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["system", "step_id", "session_id", "timestamp", "event_type"]
        return [dict(zip(cols, r)) for r in rows]

    def query_step_loop(self, system=None, window=600):
        """Step 循环检测"""
        sql = """
            SELECT system,
                   COUNT(DISTINCT json_extract(payload, '$.layer_agent.step_id')) * 1.0 / NULLIF(COUNT(*), 0) AS uniqueness,
                   COUNT(*) AS total_steps,
                   COUNT(DISTINCT json_extract(payload, '$.layer_agent.step_id')) AS unique_steps
            FROM events
            WHERE event_type='agent_step'
              AND timestamp > ?
              AND (system = ? OR ? IS NULL)
            GROUP BY system
            HAVING uniqueness < 0.5
            ORDER BY uniqueness ASC
        """
        since = time.time() - window
        rows = self._conn.execute(sql, (since, system, system)).fetchall()
        cols = ["system", "uniqueness", "total_steps", "unique_steps"]
        return [dict(zip(cols, r)) for r in rows]

    def query_memory_retrieve(self, system=None, since=None, limit=50):
        """记忆检索记录"""
        since_clause = "AND timestamp > ?" if since else ""
        sql = f"""
            SELECT system,
                   json_extract(payload, '$.results_count') AS results_count,
                   json_extract(payload, '$.latency_ms') AS latency_ms,
                   timestamp
            FROM events
            WHERE event_type='ying.retrieve'
              AND (system = ? OR ? IS NULL)
              {since_clause}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params = [system, system]
        if since:
            params.insert(2, since)
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["system", "results_count", "latency_ms", "timestamp"]
        return [dict(zip(cols, r)) for r in rows]

    def query_agent_status(self):
        """Agent 在线状态（已注册系统的最后活跃时间）"""
        sql = """
            SELECT system,
                   MAX(timestamp) AS last_seen,
                   CAST(strftime('%s', 'now') - MAX(timestamp) AS REAL) AS idle_seconds
            FROM events
            WHERE timestamp > ?
            GROUP BY system
            ORDER BY last_seen DESC
        """
        since = time.time() - 7200
        rows = self._conn.execute(sql, (since,)).fetchall()
        cols = ["system", "last_seen", "idle_seconds"]
        results = [dict(zip(cols, r)) for r in rows]
        for r in results:
            r["status"] = "offline" if r["idle_seconds"] > 600 else "online"
        return results

    def query_token_spike(self, system=None, window=300, threshold=3.0):
        """Token 消耗突增检测（window 秒 vs 12×window 基线）"""
        now = time.time()
        cutoff = now - window
        lookback = now - window * 12
        sql = """
            SELECT system,
                   SUM(CASE WHEN timestamp > ?
                       THEN json_extract(payload, '$.layer_llm.input_tokens') +
                            json_extract(payload, '$.layer_llm.output_tokens')
                       ELSE 0 END) AS recent_tokens,
                   AVG(CASE WHEN timestamp <= ?
                       THEN json_extract(payload, '$.layer_llm.input_tokens') +
                            json_extract(payload, '$.layer_llm.output_tokens')
                       ELSE NULL END) AS baseline_avg,
                   COUNT(CASE WHEN timestamp > ? THEN 1 END) AS recent_calls,
                   COUNT(CASE WHEN timestamp <= ? THEN 1 END) AS baseline_calls
            FROM events
            WHERE event_type='llm_invoke'
              AND timestamp > ?
              AND (system = ? OR ? IS NULL)
            GROUP BY system
            HAVING recent_tokens > baseline_avg * ? AND baseline_avg > 0
        """
        rows = self._conn.execute(
            sql, (cutoff, cutoff, cutoff, cutoff, lookback, system, system, threshold)
        ).fetchall()
        cols = [
            "system",
            "recent_tokens",
            "baseline_avg",
            "recent_calls",
            "baseline_calls",
        ]
        results = [dict(zip(cols, r)) for r in rows]
        for r in results:
            r["spike_ratio"] = round(
                r["recent_tokens"] / r["baseline_avg"] if r["baseline_avg"] > 0 else 0,
                2,
            )
            r["window_seconds"] = window
        return results

    def query_event_timeline(self, system=None, since=None, limit=200):
        """事件时间线"""
        since_clause = "AND timestamp > ?" if since else ""
        sql = f"""
            SELECT timestamp, event_type, system,
                   CASE
                     WHEN event_type='tool_call' THEN json_extract(payload, '$.layer_tool.tool_name')
                     WHEN event_type='llm_invoke' THEN json_extract(payload, '$.layer_llm.model')
                     ELSE NULL
                   END AS detail
            FROM events
            WHERE (system = ? OR ? IS NULL)
              {since_clause}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params = [system, system]
        if since:
            params.insert(2, since)
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["timestamp", "event_type", "system", "detail"]
        return [dict(zip(cols, r)) for r in rows]

    def _scan_hot_files(
        self, system=None, event_type=None, limit=100, since=None, exclude_type=None
    ):
        """扫描热轨文件中未归档的事件"""
        events = []
        files = sorted(
            self.hot_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        exclude = exclude_type or "__meta_health__"

        for f in files[:10]:  # 只看最近 10 个文件
            try:
                for line in f.read_text(encoding="utf-8").strip().splitlines():
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if exclude and ev.get("event_type") == exclude:
                        continue
                    if system and ev.get("system") != system:
                        continue
                    if event_type and ev.get("event_type") != event_type:
                        continue
                    if since and ev.get("timestamp", 0) < since:
                        continue

                    events.append(ev)
                    if len(events) >= limit:
                        return events
            except Exception:
                continue

        return events

    def _scan_hot_register(self, system: str):
        """热轨兜底查找 __register__ 事件"""
        files = sorted(
            self.hot_dir.glob(f"{system}_*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for f in files[:3]:  # 只看最近 3 个文件
            try:
                for line in f.read_text(encoding="utf-8").strip().splitlines():
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if ev.get("event_type") == "__register__":
                        payload = ev.get("payload", {})
                        return {
                            "system": system,
                            "pid": payload.get("pid"),
                            "registered_at": payload.get("registered_at"),
                            "last_seen": ev.get("timestamp"),
                            "mode": payload.get("mode", "white"),
                            "source": "hot_fallback",
                        }
            except Exception:
                continue

        return None

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False

    def __del__(self):
        try:
            self._conn.close()
        except Exception:
            pass
