# triage.py —— v0.11.9m 分诊器
# 职责：扫描 SQLite 实际字段，对照 diseases.yaml 判断哪些病能诊
# 输出：triage_snapshot.json（lit_lite.py 消费）
# 依赖：纯标准库（sqlite3, json, yaml）

import sqlite3, json, time, os, re
from pathlib import Path
from collections import defaultdict

DB = Path.home() / ".ming" / "ming.db"
DISEASES_YAML = Path(__file__).parent.parent / "config" / "diseases.yaml"
TRIAGE_SNAPSHOT = Path.home() / ".ming" / "triage_snapshot.json"

SAMPLE_RECENT = 50
SAMPLE_RANDOM = 50
SAMPLE_THRESHOLD = 100
COVERAGE_THRESHOLD = 0.30


def extract_fields(payload: dict, prefix: str = "") -> set:
    fields = set()
    for key, val in payload.items():
        path = f"{prefix}.{key}" if prefix else key
        if val is not None and val != "":
            fields.add(path)
        if isinstance(val, dict):
            fields.update(extract_fields(val, path))
    return fields


def flatten_payload(payload_str: str) -> set:
    try:
        payload = json.loads(payload_str)
        return extract_fields(payload)
    except (json.JSONDecodeError, TypeError):
        return set()


def stratified_sample(conn, event_type: str) -> list:
    total = conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_type=?", (event_type,)
    ).fetchone()[0]

    if total <= SAMPLE_THRESHOLD:
        return conn.execute(
            "SELECT payload FROM events WHERE event_type=?", (event_type,)
        ).fetchall()

    recent = conn.execute(
        "SELECT payload FROM events WHERE event_type=? ORDER BY timestamp DESC LIMIT ?",
        (event_type, SAMPLE_RECENT),
    ).fetchall()

    random_rows = conn.execute(
        "SELECT payload FROM events WHERE event_type=? ORDER BY RANDOM() LIMIT ?",
        (event_type, SAMPLE_RANDOM),
    ).fetchall()

    seen = set()
    result = []
    for row in recent + random_rows:
        if row[0] not in seen:
            seen.add(row[0])
            result.append(row)
    return result


def scan_sqlite(conn) -> dict:
    event_types = conn.execute("SELECT DISTINCT event_type FROM events").fetchall()
    event_types = [r[0] for r in event_types]

    event_type_counts = {}
    for et in event_types:
        cnt = conn.execute(
            "SELECT COUNT(*) FROM events WHERE event_type=?", (et,)
        ).fetchone()[0]
        event_type_counts[et] = cnt

    systems = conn.execute(
        "SELECT DISTINCT system FROM events WHERE system IS NOT NULL"
    ).fetchall()
    systems = [r[0] for r in systems]

    all_fields = defaultdict(set)
    total_sampled = 0

    for et in event_types:
        rows = stratified_sample(conn, et)
        total_sampled += len(rows)
        for (payload_str,) in rows:
            fields = flatten_payload(payload_str)
            all_fields[et].update(fields)

    return {
        "event_types": event_types,
        "event_type_counts": event_type_counts,
        "systems": systems,
        "all_fields": {k: list(v) for k, v in all_fields.items()},
        "events_sampled": total_sampled,
    }


def load_diseases() -> list:
    if not DISEASES_YAML.exists():
        return []
    try:
        import yaml

        with open(DISEASES_YAML, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or []
    except ImportError:
        return []


def evaluate_diseases(diseases: list, scan_result: dict) -> dict:
    existing_types = set(scan_result["event_types"])
    all_fields_flat = set()
    for fields in scan_result["all_fields"].values():
        all_fields_flat.update(fields)

    rule_status = {}
    ready_count = 0
    degraded_count = 0
    blocked_count = 0

    for disease in diseases:
        did = disease.get("id", "UNKNOWN")
        depends = disease.get("depends", {})
        required_types = set(depends.get("event_types", []))
        required_fields = set(depends.get("fields", []))

        types_ok = required_types.issubset(existing_types)

        if not required_fields:
            field_coverage = 1.0
        else:
            matched = sum(1 for f in required_fields if f in all_fields_flat)
            field_coverage = matched / len(required_fields)

        fields_ok = field_coverage >= COVERAGE_THRESHOLD

        if disease.get("scope") in ("mcp_required", "external_audit"):
            if types_ok and fields_ok:
                status = "degraded"
                reason = f"scope={disease['scope']}，数据完整但需外部系统确认"
                confidence = 0.5
            else:
                status = "blocked"
                reason = f"scope={disease['scope']}，需外部系统且数据不完整"
                confidence = 0.0
        elif types_ok and fields_ok:
            status = "ready"
            reason = "所有依赖满足"
            confidence = 1.0
        elif types_ok and field_coverage > 0:
            status = "degraded"
            missing = [f for f in required_fields if f not in all_fields_flat]
            reason = f"字段覆盖率 {field_coverage:.0%}，缺: {', '.join(missing[:3])}"
            confidence = max(field_coverage, 0.1)
        else:
            status = "blocked"
            missing_types = list(required_types - existing_types)
            missing = [f for f in required_fields if f not in all_fields_flat]
            parts = []
            if missing_types:
                parts.append(f"缺事件: {', '.join(missing_types[:3])}")
            if missing:
                parts.append(f"缺字段: {', '.join(missing[:3])}")
            reason = "; ".join(parts) if parts else "依赖不满足"
            confidence = 0.0

        rule_status[did] = {
            "status": status,
            "coverage": round(field_coverage, 2),
            "confidence_multiplier": round(confidence, 2),
            "reason": reason,
        }

        if status == "ready":
            ready_count += 1
        elif status == "degraded":
            degraded_count += 1
        else:
            blocked_count += 1

    return {
        "rule_status": rule_status,
        "ready": ready_count,
        "degraded": degraded_count,
        "blocked": blocked_count,
    }


def generate_suggestions(eval_result: dict, diseases: list) -> list:
    field_demand = defaultdict(list)
    for disease in diseases:
        did = disease["id"]
        status_info = eval_result["rule_status"].get(did, {})
        if status_info.get("status") in ("blocked", "degraded"):
            for field in disease.get("depends", {}).get("fields", []):
                field_demand[field].append(disease["name"])

    suggestions = []
    for field, disease_names in sorted(field_demand.items(), key=lambda x: -len(x[1])):
        suggestions.append(
            {
                "field": field,
                "disease_count": len(disease_names),
                "diseases": disease_names[:5],
            }
        )
    return suggestions


def run(verbose: bool = True) -> dict:
    if not DB.exists():
        print(f"数据库不存在: {DB}")
        return {}

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        scan_result = scan_sqlite(conn)
        diseases = load_diseases()
        eval_result = evaluate_diseases(diseases, scan_result)
        suggestions = generate_suggestions(eval_result, diseases)

        snapshot = {
            "generated_at": time.time(),
            "scan_stats": {
                "events_sampled": scan_result["events_sampled"],
                "systems": scan_result["systems"],
                "event_type_counts": scan_result["event_type_counts"],
            },
            "rule_status": eval_result["rule_status"],
            "summary": {
                "ready": eval_result["ready"],
                "degraded": eval_result["degraded"],
                "blocked": eval_result["blocked"],
                "total": len(diseases),
            },
            "suggestions": suggestions,
        }

        TRIAGE_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        TRIAGE_SNAPSHOT.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))

        if verbose:
            print("乾坤镜分诊报告")
            print("=" * 40)
            print(f"扫描结果：")
            print(f"  事件类型：{len(scan_result['event_types'])} 种")
            for et, cnt in sorted(scan_result["event_type_counts"].items()):
                print(f"    {et}: {cnt}")
            print(
                f"  系统：{', '.join(scan_result['systems']) if scan_result['systems'] else '无'}"
            )
            print(f"  采样事件：{scan_result['events_sampled']} 条")
            print()
            print(f"诊断能力（共 {len(diseases)} 种病症）：")
            print(f"  ✅ 可诊断：{eval_result['ready']} 种")
            print(f"  ⚠️ 部分可诊断：{eval_result['degraded']} 种（字段覆盖率低）")
            print(f"  ❌ 不能诊断：{eval_result['blocked']} 种")
            print()
            print(f"分诊快照已写入: {TRIAGE_SNAPSHOT}")
    finally:
        conn.close()

    return snapshot


if __name__ == "__main__":
    run()
