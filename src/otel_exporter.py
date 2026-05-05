# otel_exporter.py —— v0.11.9m OTEL/JSON Exporter
# 职责：将乾坤镜诊断结果推送为 OTEL LogRecord
# 依赖：纯 Python 标准库，零第三方依赖
# 模式：完全无状态纯函数，通过 --since 参数控制起始时间
# 启动：python src/otel_exporter.py --since 1714123456.0

import json, os, sqlite3, time, sys, argparse, urllib.request, urllib.error

DB_PATH = os.path.expanduser("~/.ming/ming.db")
ENDPOINT = os.getenv("MING_OTEL_EXPORTER_ENDPOINT", "http://localhost:4318/v1/logs")
LIMIT = int(os.getenv("MING_OTEL_EXPORT_LIMIT", "100"))
SEV_MAP = {
    "P0": (21, "FATAL"),
    "P1": (17, "ERROR"),
    "P2": (13, "WARN"),
    "P3": (9, "INFO"),
    "META": (5, "DEBUG"),
}
MAX_ATTR_SIZE = 128 * 1024  # 128KB 截断保护


def _truncate(val, max_len=MAX_ATTR_SIZE):
    """截断超长字符串，标记 truncated"""
    s = str(val)
    if len(s.encode("utf-8")) > max_len:
        truncated = s[: max_len // 2] + "\n...[truncated]...\n" + s[-max_len // 2 :]
        return truncated, True
    return s, False


def get_diag_tables(conn):
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'diagnoses_%'"
        ).fetchall()
    ]


def read_diagnoses(conn, since, limit):
    tables = get_diag_tables(conn)
    if not tables:
        return []
    sql = (
        " UNION ALL ".join(f"SELECT * FROM {t} WHERE created_at > ?" for t in tables)
        + " ORDER BY created_at ASC LIMIT ?"
    )
    cursor = conn.execute(sql, [since] * len(tables) + [limit])
    cols = [d[0] for d in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def build_otlp_logs(diagnoses):
    logs = []
    for d in diagnoses:
        sn, st = SEV_MAP.get(d.get("severity", "P2"), (13, "WARN"))
        ev = d.get("evidence", "")
        if isinstance(ev, str) and ev:
            try:
                json.loads(ev)
            except Exception:
                ev = ""
        truncated = False
        attributes = [
            {
                "key": "qiankun.fault_id",
                "value": {"stringValue": str(d.get("fault_id", ""))},
            },
            {
                "key": "qiankun.system",
                "value": {"stringValue": str(d.get("system", ""))},
            },
            {
                "key": "qiankun.confidence",
                "value": {"doubleValue": float(d.get("confidence", 0))},
            },
            {
                "key": "qiankun.evidence_quality",
                "value": {"intValue": int(d.get("evidence_quality", 1))},
            },
            {
                "key": "qiankun.severity",
                "value": {"stringValue": str(d.get("severity", ""))},
            },
        ]
        ev_str, ev_truncated = _truncate(ev)
        attributes.append({"key": "qiankun.evidence", "value": {"stringValue": ev_str}})
        if ev_truncated:
            truncated = True
        # prescription 字段（如果诊断结果包含）
        rx = d.get("prescription")
        if rx:
            summary, s_trunc = _truncate(rx.get("summary", ""))
            attributes.append(
                {
                    "key": "qiankun.prescription.summary",
                    "value": {"stringValue": summary},
                }
            )
            if s_trunc:
                truncated = True
            actions = rx.get("actions", [])
            actions_str = json.dumps(actions, ensure_ascii=False)
            actions_str, a_trunc = _truncate(actions_str)
            attributes.append(
                {
                    "key": "qiankun.prescription.actions",
                    "value": {"stringValue": actions_str},
                }
            )
            if a_trunc:
                truncated = True
        if truncated:
            attributes.append(
                {"key": "qiankun.truncated", "value": {"boolValue": True}}
            )
        logs.append(
            {
                "severityNumber": sn,
                "severityText": st,
                "body": {
                    "stringValue": f"[{d.get('fault_id', '?')}] {d.get('diagnosis_name', '?')}"
                },
                "attributes": attributes,
                "timeUnixNano": int(d.get("created_at", time.time()) * 1e9),
            }
        )
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": "qiankun-diagnoser"},
                        },
                        {"key": "service.version", "value": {"stringValue": "0.11.9m"}},
                    ]
                },
                "scopeLogs": [{"logRecords": logs}],
            }
        ]
    }


def export_once(conn, since, limit):
    diagnoses = read_diagnoses(conn, since, limit)
    if not diagnoses:
        print(f"[otel_exporter] No new since {since}")
        return since
    payload = json.dumps(build_otlp_logs(diagnoses)).encode("utf-8")
    req = urllib.request.Request(
        ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            max_ts = max(d["created_at"] for d in diagnoses)
            print(
                f"[otel_exporter] Exported {len(diagnoses)} (since={since}, max_ts={max_ts})"
            )
            return max_ts
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                print(f"[otel_exporter] Client error {e.code}", file=sys.stderr)
                return since
            if attempt < 2:
                time.sleep(2**attempt)
        except Exception as e:
            if attempt < 2:
                time.sleep(2**attempt)
            else:
                print(f"[otel_exporter] Failed: {e}", file=sys.stderr)
    return since


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--since", type=float, default=float(os.getenv("MING_OTEL_SINCE", "0"))
    )
    args = parser.parse_args()
    if not os.path.exists(DB_PATH):
        print(f"[otel_exporter] DB not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    max_ts = export_once(conn, args.since, LIMIT)
    print(max_ts)
    conn.close()


if __name__ == "__main__":
    main()
