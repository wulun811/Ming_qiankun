# archiver_summary.py —— 0.11.10 月度摘要聚合模块
# 职责：对已完成自然月执行聚合，写入 monthly_summary 表，幂等控制
import json, time, hashlib, sqlite3
from datetime import datetime, timedelta, timezone


TOKEN_ESTIMATE = {
    "llm_invoke": lambda p: len(p.get("layer_llm", {}).get("messages", [])) * 200,
    "llm_output": lambda p: len(p.get("layer_llm", {}).get("output_text", "")) // 2,
    "llm_reasoning": lambda p: len(p.get("layer_llm", {}).get("reasoning", "")) // 2,
    "tool_call": lambda p: len(json.dumps(p.get("layer_tool", {}))) // 2,
}


def _estimate_tokens(payload_text, event_type):
    try:
        p = json.loads(payload_text) if isinstance(payload_text, str) else payload_text
        fn = TOKEN_ESTIMATE.get(event_type) or (lambda p: len(json.dumps(p)) // 10)
        return fn(p)
    except Exception:
        return 0


def summarize_month(conn, year_month):
    now = datetime.now(timezone.utc)
    year, month = map(int, year_month.split("_"))
    first_of_month = datetime(year, month, 1, tzinfo=timezone.utc)
    if month == 12:
        first_of_next = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        first_of_next = datetime(year, month + 1, 1, tzinfo=timezone.utc)
    first_ts = first_of_month.timestamp()
    last_ts = (first_of_next - timedelta(seconds=1)).timestamp()

    # 跳过未完成的月份
    if first_of_next > now:
        return {"skipped": "month_not_complete", "year_month": year_month}

    already = conn.execute(
        "SELECT last_aggregated_at FROM monthly_summary WHERE year_month=? LIMIT 1",
        (year_month,),
    ).fetchone()
    if already and already[0] >= last_ts:
        return {"skipped": "already_aggregated", "year_month": year_month}

    # 按 system+event_type 聚合
    rows = conn.execute(
        """
        SELECT system, event_type,
               COUNT(*) as cnt,
               SUM(CASE WHEN event_type = 'error' THEN 1 ELSE 0 END) as errs,
               AVG(LENGTH(COALESCE(payload, ''))) as avg_bytes,
               MIN(timestamp), MAX(timestamp)
        FROM events
        WHERE timestamp >= ? AND timestamp <= ?
        GROUP BY system, event_type
        ORDER BY system, event_type
        """,
        (first_ts, last_ts),
    ).fetchall()

    total_aggregated = sum(r[2] for r in rows)
    actual_count = conn.execute(
        "SELECT COUNT(*) FROM events WHERE timestamp >= ? AND timestamp <= ?",
        (first_ts, last_ts),
    ).fetchone()[0]

    if actual_count > 0 and abs(total_aggregated - actual_count) / actual_count > 0.001:
        return {
            "error": f"aggregation mismatch: summed={total_aggregated} actual={actual_count}"
        }

    now_ts = time.time()
    summary = {
        "year_month": year_month,
        "systems": {},
        "total_events": total_aggregated,
    }
    for system, event_type, cnt, errs, avg_bytes, min_ts, max_ts in rows:
        conn.execute(
            """INSERT OR REPLACE INTO monthly_summary
               (year_month, system, event_type, total_count, error_count,
                avg_payload_bytes, total_tokens_est, min_timestamp, max_timestamp,
                summary_json, last_aggregated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                year_month,
                system,
                event_type,
                cnt,
                errs,
                round(avg_bytes, 1),
                0,
                min_ts,
                max_ts,
                "{}",
                now_ts,
            ),
        )
        if system not in summary["systems"]:
            summary["systems"][system] = {"total": 0, "errors": 0, "types": {}}
        summary["systems"][system]["total"] += cnt
        summary["systems"][system]["errors"] += errs
        summary["systems"][system]["types"][event_type] = cnt

    conn.commit()
    summary["aggregated_at"] = now_ts
    return summary


def load_monthly_summary(conn, year_month):
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT year_month, system, event_type, total_count, error_count,
                  avg_payload_bytes, total_tokens_est, min_timestamp, max_timestamp
           FROM monthly_summary
           WHERE year_month = ?
           ORDER BY system, event_type""",
        (year_month,),
    ).fetchall()
    return [dict(r) for r in rows]
