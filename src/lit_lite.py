# lit_lite.py —— 0.11.9m 诊断器核心
# 职责：诊断主循环 diagnose() — 规则遍历 + SQL 执行 + 证据生成 + 交叉验证
# 拆分：规则引擎 → lit_rule.py / 诊断输出 → lit_dx.py / 交叉验证 → lit_crossval.py
import sqlite3, json, time, os, sys
from pathlib import Path

from lit_rule import (
    load_triage_snapshot,
    load_diseases_yaml,
    _extract_columns,
    _build_params,
    _row_to_evidence,
    validate_sql,
    execute_safe_sql,
    set_incremental_since,
    _load_env_profile,
    resolve_env_sql,
    resolve_per_system,
)
from lit_dx import write_dx
from archiver_exclude import is_system_excluded

try:
    from lit_crossval import apply_cross_validation
except ModuleNotFoundError:
    apply_cross_validation = None

DB = Path.home() / ".ming" / "ming.db"
OUT = Path.home() / ".ming" / "plugins" / "lit_lite" / "out"

GHOST_FIELDS = {
    "TLT-042",
    "TLT-047",
    "AGT-052",
    "AGT-056",
    "MEM-074",
    "MEM-075",
    "MEM-076",
}
DEFERRED_IDS = {"MDL-115", "MDL-116", "NET-117", "MEM-118", "MEM-119", "MEM-120"}


def _push_p0_diagnoses(p0_hits):
    """P0 诊断秒级推送到土行孙 gateway，失败静默"""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from config_loader import load_config

        config, _, _ = load_config()
        push_cfg = config.get("push", {})
        if not push_cfg.get("enabled", True):
            return

        from ming_push import push_p0

        push_p0(
            p0_hits,
            host=push_cfg.get("host", "localhost"),
            port=push_cfg.get("port", 9002),
            path=push_cfg.get("path", "/ming/push"),
        )
    except Exception as e:
        sys.stderr.write(f"[lit_lite] P0 push failed: {e}\n")
        sys.stderr.flush()


def _apply_black_mode_penalty(conn, diagnoses):
    try:
        window = 1800
        now = time.time()
        ev_types = set()
        for dx in diagnoses:
            ev_types.update(dx.get("event_types", set()))
        if not ev_types:
            return
        placeholders = ",".join("?" for _ in ev_types)
        rows = conn.execute(
            f"""
            SELECT system, event_type,
                   SUM(CASE WHEN mode = 'black' THEN 1 ELSE 0 END) as black_cnt,
                   COUNT(*) as total_cnt
            FROM events
            WHERE event_type IN ({placeholders}) AND timestamp > ?
            GROUP BY system, event_type
            """,
            list(ev_types) + [now - window],
        ).fetchall()
        mode_map = {}
        for system, ev_type, black_cnt, total_cnt in rows:
            if total_cnt > 0:
                mode_map[(system, ev_type)] = black_cnt / total_cnt
        for dx in diagnoses:
            if dx.get("severity") == "P0":
                continue
            ratios = []
            for et in dx.get("event_types", set()):
                r = mode_map.get((dx["system"], et), 0.0)
                if r > 0:
                    ratios.append(r)
            if not ratios:
                continue
            avg_black = sum(ratios) / len(ratios)
            if avg_black > 0.8:
                dx["confidence"] *= 0.6
            elif avg_black > 0.5:
                dx["confidence"] *= 0.8
    except Exception:
        pass


def diagnose():
    OUT.mkdir(parents=True, exist_ok=True)

    if not DB.exists():
        (OUT / ".plugin_heartbeat").write_text(str(time.time()))
        return

    triage = load_triage_snapshot()
    if triage is None:
        write_dx(
            "all",
            "META-003",
            "分诊快照缺失或过期",
            0.95,
            "META",
            [
                {
                    "summary": "triage_snapshot.json 不存在或超过24小时未更新，诊断结果可能不准确"
                }
            ],
            "请先运行 ming triage 生成分诊快照",
            "triage_missing",
        )

    last_ts = 0.0
    last_triage_file = DB.parent / ".last_triage_ts"
    if (
        os.environ.get("MING_INCREMENTAL_TRIAGE", "1") == "1"
        and last_triage_file.exists()
    ):
        try:
            last_ts = float(last_triage_file.read_text().strip())
        except (ValueError, OSError):
            pass
    set_incremental_since(last_ts)

    hb = OUT / ".plugin_heartbeat"
    last_run = 0
    if hb.exists():
        try:
            last_run = float(hb.read_text().strip())
        except ValueError:
            pass

    now = time.time()
    if last_run > 0 and (now - last_run) > 600:
        write_dx(
            "all",
            "META-002",
            "插件静默失效(LIT lite)",
            0.95,
            "META",
            [
                {
                    "summary": f"上次运行距今 {(now - last_run) / 60:.0f} 分钟",
                    "key_fields": {"last_run": last_run, "expected_interval": 300},
                }
            ],
            "LIT lite 超过2个周期未产出诊断，可能插件崩溃或被阻塞",
            "plugin_silent",
        )

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        health_alert = conn.execute(
            """
            SELECT 1 FROM probe_health
            WHERE system IN (SELECT DISTINCT system FROM events WHERE timestamp > ?)
            AND drop_count > 0 AND window_start > ?
            LIMIT 1
        """,
            (time.time() - 300, time.time() - 300),
        ).fetchone()
    except sqlite3.OperationalError:
        health_alert = None

    if health_alert:
        write_dx(
            "all",
            "META-001",
            "探针数据不可靠(诊断暂停)",
            1.0,
            "META",
            [],
            "探针报告丢包或磁盘满，近期诊断不可信",
            "data_unreliable",
        )
        conn.close()
        (OUT / ".plugin_heartbeat").write_text(str(now))
        return

    diseases = load_diseases_yaml()
    if diseases is None:
        conn.close()
        (OUT / ".plugin_heartbeat").write_text(str(now))
        return

    always_on_ids = {d.get("id", "") for d in diseases if d.get("always_on") is True}

    def is_rule_ready(rule_id: str) -> bool:
        if rule_id in always_on_ids:
            return True
        if triage is None:
            return True
        status = triage.get(rule_id, {}).get("status", "blocked")
        return status in ("ready", "degraded")

    def get_confidence_multiplier(rule_id: str) -> float:
        if triage is None:
            return 1.0
        return triage.get(rule_id, {}).get("confidence_multiplier", 1.0)

    env_profile = _load_env_profile(conn)

    executed = 0
    executed_blocked = 0
    skipped_blocked = 0
    skipped_mcp = 0
    skipped_deferred = 0
    skipped_ghost = 0
    sql_errors = 0
    _p0_hits = []
    _all_diagnoses = []
    disease_map = {d.get("id", ""): d for d in diseases}

    for rule in diseases:
        rule_id = rule.get("id", "")
        severity = rule.get("severity", "P2")
        scope = rule.get("scope", "observable")
        name = rule.get("name", rule_id)
        description = rule.get("description", "")

        if rule_id in GHOST_FIELDS:
            skipped_ghost += 1
            continue

        if rule_id in DEFERRED_IDS:
            skipped_deferred += 1
            continue

        if scope in ("mcp_required", "external_audit"):
            skipped_mcp += 1
            continue

        is_rule_blocked = False
        if not is_rule_ready(rule_id):
            if (
                triage is not None
                and triage.get(rule_id, {}).get("status") == "blocked"
            ):
                is_rule_blocked = True
            else:
                skipped_blocked += 1
                continue

        if is_rule_blocked:
            executed_blocked += 1

        inference_mode = rule.get("inference_mode", "")
        if inference_mode == "shadow":
            sql_template = rule.get("shadow_sql", "")
        else:
            sql_template = rule.get("sql_template", "")

        if not sql_template:
            continue

        sql_template = resolve_env_sql(sql_template, rule, env_profile)

        import re

        if "FROM events" in sql_template and "integrity_score" not in sql_template:
            # Insert filtering clause BEFORE GROUP BY / ORDER BY / HAVING
            for clause in (r"\s+GROUP\s+BY", r"\s+ORDER\s+BY", r"\s+HAVING"):
                m = re.search(clause, sql_template, re.IGNORECASE)
                if m:
                    sql_template = (
                        sql_template[: m.start()]
                        + " AND integrity_score >= 0.6"
                        + sql_template[m.start() :]
                    )
                    break
            else:
                sql_template = sql_template.rstrip() + " AND integrity_score >= 0.6"

        ok, msg = validate_sql(sql_template)
        if not ok:
            sql_errors += 1
            continue

        placeholder_count = sql_template.count("?")
        params = _build_params(rule, placeholder_count)

        rows, exec_msg = execute_safe_sql(conn, sql_template, params)
        if exec_msg != "ok":
            sql_errors += 1
            continue

        if not rows:
            executed += 1
            continue

        columns = _extract_columns(sql_template)

        base_confidence = rule.get("confidence", 0.8)
        if severity == "P0":
            base_confidence = 0.90
        elif severity == "P1":
            base_confidence = 0.85
        elif severity == "P2":
            base_confidence = 0.75
        conf = base_confidence * get_confidence_multiplier(rule_id)
        if is_rule_blocked:
            conf *= 0.3

        for row in rows:
            ev = _row_to_evidence(row, columns, rule)
            system = ev.pop("system", "mingjing")
            if is_system_excluded(system):
                continue
            ps = resolve_per_system(rule, system)
            if not ps["enabled"]:
                continue
            row_conf = conf * ps["confidence_multiplier"]
            rule_dep = rule.get("depends", {})
            _all_diagnoses.append(
                {
                    "system": system,
                    "rule_id": rule_id,
                    "name": name,
                    "severity": severity,
                    "confidence": row_conf,
                    "original_confidence": row_conf,
                    "evidence": [ev],
                    "inference": description,
                    "event_types": list(rule_dep.get("event_types", [])),
                }
            )
            if severity == "P0":
                session_id = ev.get("key_fields", {}).get("session_id", "")
                _p0_hits.append(
                    {
                        "fault_id": rule_id,
                        "name": name,
                        "system": system,
                        "confidence": conf,
                        "evidence": description,
                        "session_id": session_id,
                        "ts": time.time(),
                    }
                )

        executed += 1

    if sql_errors > 0:
        write_dx(
            "all",
            "META-004",
            f"规则SQL执行失败({sql_errors}条)",
            0.7,
            "META",
            [{"summary": f"{sql_errors} 条规则的 SQL 执行失败，检查 diseases.yaml"}],
            "部分诊断规则无法执行，请检查规则定义",
            "sql_errors",
        )

    if _all_diagnoses:
        _apply_black_mode_penalty(conn, _all_diagnoses)

    if _all_diagnoses and apply_cross_validation is not None:
        promoted = apply_cross_validation(_all_diagnoses, disease_map)
        for dx in _all_diagnoses:
            xval_ids = dx.get("cross_validated_by")
            if xval_ids:
                write_dx(
                    dx["system"],
                    dx["rule_id"],
                    dx["name"],
                    1.0,
                    dx["severity"],
                    dx.get("evidence", []),
                    dx.get("inference", ""),
                    "cross_validated",
                    cross_validated_by=xval_ids,
                    original_confidence=dx.get("original_confidence", dx["confidence"]),
                )
            else:
                write_dx(
                    dx["system"],
                    dx["rule_id"],
                    dx["name"],
                    dx["confidence"],
                    dx["severity"],
                    dx.get("evidence", []),
                    dx.get("inference", ""),
                    "pending",
                )
        if promoted > 0:
            write_dx(
                "all",
                "META-005",
                f"交叉验证升级({promoted}条诊断提升至100%置信)",
                1.0,
                "META",
                [
                    {
                        "summary": f"{promoted} 条诊断被独立规则交叉验证，置信度从 ≤90% 提升至 100%"
                    }
                ],
                "独立观测到同一系统的多个互斥事件类型，诊断交叉验证通过",
                "cross_validation_applied",
            )
    elif _all_diagnoses:
        # 无交叉验证模块时仍正常输出诊断
        for dx in _all_diagnoses:
            write_dx(
                dx["system"],
                dx["rule_id"],
                dx["name"],
                dx["confidence"],
                dx["severity"],
                dx.get("evidence", []),
                dx.get("inference", ""),
                "pending",
            )

    if _p0_hits:
        _push_p0_diagnoses(_p0_hits)

    conn.close()
    (OUT / ".plugin_heartbeat").write_text(str(now))
    (DB.parent / ".last_triage_ts").write_text(str(now))


if __name__ == "__main__":
    diagnose()
