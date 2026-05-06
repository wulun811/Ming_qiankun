# query_bridge.py —— v0.11.9m 双轨查询
# 职责：优先查 SQLite 冷轨，归档延迟时自动降级到热轨 JSONL 兜底
# 只读，禁止任何 INSERT/UPDATE/DELETE

import json, time
from pathlib import Path
from archiver_compress import resolve_payload


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
        sql = "SELECT e.*, b.payload_blob FROM events e LEFT JOIN events_blob b ON e.id = b.event_id WHERE 1=1"
        params = []
        if system:
            sql += " AND e.system = ?"
            params.append(system)
        if event_type:
            sql += " AND e.event_type = ?"
            params.append(event_type)
        exclude = exclude_type or "__meta_health__"
        if exclude:
            sql += " AND e.event_type != ?"
            params.append(exclude)
        if since:
            sql += " AND e.timestamp >= ?"
            params.append(since)
        sql += " ORDER BY e.id DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        cols = [
            d[0]
            for d in self._conn.execute(
                "SELECT e.*, b.payload_blob FROM events e LEFT JOIN events_blob b ON e.id = b.event_id LIMIT 0"
            ).description
        ]
        results = [dict(zip(cols, r)) for r in rows]
        for r in results:
            r["payload"] = resolve_payload(r)
        for r in results:
            r.pop("payload_blob", None)

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
        params = [system, system]
        since_clause = "AND e.timestamp > ?" if since else ""
        if since:
            params.insert(2, since)
        sql = f"""
            SELECT e.storage_tier, e.payload, b.payload_blob,
                   e.timestamp, e.system
            FROM events e LEFT JOIN events_blob b ON e.id = b.event_id
            WHERE e.event_type='llm_invoke'
              AND (e.system = ? OR ? IS NULL)
              {since_clause}
        """
        rows = self._conn.execute(sql, params).fetchall()
        agg = {}
        for r in rows:
            d = dict(
                zip(
                    ["storage_tier", "payload", "payload_blob", "timestamp", "system"],
                    r,
                )
            )
            p = resolve_payload(d)
            if not isinstance(p, dict):
                continue
            if group_by == "model":
                g = (p.get("layer_llm") or {}).get("model") or "unknown"
            elif group_by == "hour":
                g = int(d["timestamp"] / 3600) if d["timestamp"] else 0
            else:
                g = d["system"]
            inp = (p.get("layer_llm") or {}).get("input_tokens", 0) or 0
            out = (p.get("layer_llm") or {}).get("output_tokens", 0) or 0
            if g not in agg:
                agg[g] = {
                    "tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "calls": 0,
                }
            agg[g]["tokens"] += inp + out
            agg[g]["input_tokens"] += inp
            agg[g]["output_tokens"] += out
            agg[g]["calls"] += 1
        result = [{"group": g, **v} for g, v in agg.items()]
        result.sort(key=lambda x: x["tokens"], reverse=True)
        return result

    def query_tool_dangerous(self, system=None, since=None):
        """危险工具调用审计"""
        since_clause = "AND e.timestamp > ?" if since else ""
        sql = f"""
            SELECT e.system, e.storage_tier, e.payload, b.payload_blob, e.timestamp
            FROM events e LEFT JOIN events_blob b ON e.id = b.event_id
            WHERE e.event_type='tool_call'
              AND (e.system = ? OR ? IS NULL)
              {since_clause}
            ORDER BY e.timestamp DESC
        """
        params = [system, system]
        if since:
            params.insert(2, since)
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["system", "storage_tier", "payload", "payload_blob", "timestamp"]
        DANGEROUS_TOOLS = {
            "rm",
            "unlink",
            "exec",
            "sudo",
            "rm_rf",
            "git_force_push",
            "db_drop",
            "chmod_777",
            "eval",
        }
        result = []
        for r in rows:
            d = dict(zip(cols, r))
            p = resolve_payload(d)
            if not isinstance(p, dict):
                continue
            tool = (p.get("layer_tool") or {}).get("tool_name")
            if tool not in DANGEROUS_TOOLS:
                continue
            result.append(
                {
                    "system": d["system"],
                    "tool": tool,
                    "args": (p.get("layer_tool") or {}).get("tool_args"),
                    "result": (p.get("layer_tool") or {}).get("tool_result"),
                    "status": (p.get("layer_tool") or {}).get("tool_status"),
                    "exec_ms": (p.get("layer_tool") or {}).get("execution_ms"),
                    "timestamp": d["timestamp"],
                }
            )
        return result

    def query_step_sequence(self, system=None, session_id=None, since=None, limit=100):
        """Step 序列回放"""
        sql = """
            SELECT e.system, e.storage_tier, e.payload, b.payload_blob,
                   e.timestamp, e.event_type
            FROM events e LEFT JOIN events_blob b ON e.id = b.event_id
            WHERE e.event_type IN ('agent_step', 'tool_call', 'llm_invoke')
              AND (e.system = ? OR ? IS NULL)
        """
        params = [system, system]
        if session_id:
            sql += " AND 1=1"  # placeholder — filtered in Python
        if since:
            sql += " AND e.timestamp > ?"
            params.append(since)
        sql += " ORDER BY e.timestamp ASC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        cols = [
            "system",
            "storage_tier",
            "payload",
            "payload_blob",
            "timestamp",
            "event_type",
        ]
        result = []
        for r in rows:
            d = dict(zip(cols, r))
            p = resolve_payload(d)
            if not isinstance(p, dict):
                continue
            sid = (p.get("layer_agent") or {}).get("step_id")
            sess = (p.get("layer_agent") or {}).get("session_id")
            if session_id and sess != session_id:
                continue
            result.append(
                {
                    "system": d["system"],
                    "step_id": sid,
                    "session_id": sess,
                    "timestamp": d["timestamp"],
                    "event_type": d["event_type"],
                }
            )
        return result[:limit]

    def query_step_loop(self, system=None, window=600):
        """Step 循环检测"""
        since = time.time() - window
        sql = """
            SELECT e.system, e.storage_tier, e.payload, b.payload_blob
            FROM events e LEFT JOIN events_blob b ON e.id = b.event_id
            WHERE e.event_type='agent_step'
              AND e.timestamp > ?
              AND (e.system = ? OR ? IS NULL)
        """
        rows = self._conn.execute(sql, (since, system, system)).fetchall()
        cols = ["system", "storage_tier", "payload", "payload_blob"]
        step_counts = {}
        for r in rows:
            d = dict(zip(cols, r))
            p = resolve_payload(d)
            if not isinstance(p, dict):
                continue
            sid = (p.get("layer_agent") or {}).get("step_id")
            sys_name = d["system"]
            if sys_name not in step_counts:
                step_counts[sys_name] = {"steps": set(), "total": 0}
            step_counts[sys_name]["steps"].add(sid)
            step_counts[sys_name]["total"] += 1
        result = []
        for sys_name, data in step_counts.items():
            unique = len(data["steps"])
            total = data["total"]
            uniqueness = (unique * 1.0 / total) if total > 0 else 1.0
            if uniqueness < 0.5:
                result.append(
                    {
                        "system": sys_name,
                        "uniqueness": uniqueness,
                        "total_steps": total,
                        "unique_steps": unique,
                    }
                )
        result.sort(key=lambda x: x["uniqueness"])
        return result

    def query_memory_retrieve(self, system=None, since=None, limit=50):
        """记忆检索记录"""
        since_clause = "AND e.timestamp > ?" if since else ""
        sql = f"""
            SELECT e.system, e.storage_tier, e.payload, b.payload_blob, e.timestamp
            FROM events e LEFT JOIN events_blob b ON e.id = b.event_id
            WHERE e.event_type='ying.retrieve'
              AND (e.system = ? OR ? IS NULL)
              {since_clause}
            ORDER BY e.timestamp DESC
            LIMIT ?
        """
        params = [system, system]
        if since:
            params.insert(2, since)
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        cols = ["system", "storage_tier", "payload", "payload_blob", "timestamp"]
        result = []
        for r in rows:
            d = dict(zip(cols, r))
            p = resolve_payload(d)
            if not isinstance(p, dict):
                continue
            result.append(
                {
                    "system": d["system"],
                    "results_count": (p.get("layer_agent") or p).get("results_count"),
                    "latency_ms": (p.get("layer_agent") or p).get("latency_ms"),
                    "timestamp": d["timestamp"],
                }
            )
        return result

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
            SELECT e.system, e.storage_tier, e.payload, b.payload_blob, e.timestamp
            FROM events e LEFT JOIN events_blob b ON e.id = b.event_id
            WHERE e.event_type='llm_invoke'
              AND e.timestamp > ?
              AND (e.system = ? OR ? IS NULL)
        """
        rows = self._conn.execute(sql, (lookback, system, system)).fetchall()
        cols = ["system", "storage_tier", "payload", "payload_blob", "timestamp"]
        recent_agg = {}
        baseline_agg = {}
        for r in rows:
            d = dict(zip(cols, r))
            p = resolve_payload(d)
            if not isinstance(p, dict):
                continue
            inp = (p.get("layer_llm") or {}).get("input_tokens", 0) or 0
            out = (p.get("layer_llm") or {}).get("output_tokens", 0) or 0
            tokens = inp + out
            sys_name = d["system"]
            if sys_name not in recent_agg:
                recent_agg[sys_name] = {"tokens": 0, "calls": 0}
                baseline_agg[sys_name] = {"tokens": 0, "calls": 0}
            if d["timestamp"] > cutoff:
                recent_agg[sys_name]["tokens"] += tokens
                recent_agg[sys_name]["calls"] += 1
            if d["timestamp"] <= cutoff:
                baseline_agg[sys_name]["tokens"] += tokens
                baseline_agg[sys_name]["calls"] += 1
        results = []
        for sys_name in recent_agg:
            r = recent_agg[sys_name]
            b = baseline_agg.get(sys_name, {"tokens": 0, "calls": 0})
            baseline_avg = b["tokens"] / b["calls"] if b["calls"] > 0 else 0
            if r["tokens"] > baseline_avg * threshold and baseline_avg > 0:
                results.append(
                    {
                        "system": sys_name,
                        "recent_tokens": r["tokens"],
                        "baseline_avg": baseline_avg,
                        "recent_calls": r["calls"],
                        "baseline_calls": b["calls"],
                        "spike_ratio": round(r["tokens"] / baseline_avg, 2),
                    }
                )
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
