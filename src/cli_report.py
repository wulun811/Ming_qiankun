# cli_report.py —— 0.11.10 诊断报告命令
# 职责：体检中心模式报告 + 月度摘要（--month）
import json, sqlite3, time, sys, os, subprocess
from pathlib import Path
from datetime import datetime

DB = Path.home() / ".ming" / "ming.db"
HOT = Path.home() / ".ming" / "hot"
COLD = Path.home() / ".ming" / "cold"
HB = Path.home() / ".ming" / ".archiver_heartbeat"
DISMISSED_PATH = Path.home() / ".ming" / "dismissed_diseases.json"
ARCHIVED_PATH = Path.home() / ".ming" / "archived_diseases.json"
RESETS_PATH = Path.home() / ".ming" / "health_resets.json"
DISEASES_YAML = Path(__file__).parent.parent / "config" / "diseases.yaml"
TRIAGE_SNAPSHOT = Path.home() / ".ming" / "triage_snapshot.json"

from archiver_compress import resolve_payload

try:
    from archiver_summary import summarize_month, load_monthly_summary
except ImportError:
    summarize_month = None
    load_monthly_summary = None

ICON_SEV = {"P0": "🔴", "P1": "🟡", "P2": "🟢"}
ICON_HL = {
    "healthy": "🟢",
    "sub_healthy": "🟡",
    "warning": "🟠",
    "critical": "🔴",
}
LABEL_HL = {
    "healthy": "健康",
    "sub_healthy": "亚健康",
    "warning": "警告",
    "critical": "危急",
}
SEV_ORDER = {"P0": 0, "P1": 1, "P2": 2, "META": 3}
HL_ORDER = {"critical": 0, "warning": 1, "sub_healthy": 2, "healthy": 3}


def _load_json(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_yaml_simple(path):
    """零依赖 YAML 解析器，支持列表格式和 multiline | 值"""
    content = path.read_text(encoding="utf-8")
    items = []
    current = {}
    in_multiline = False
    multiline_key = None
    multiline_val = []
    for line in content.split("\n"):
        if in_multiline:
            if line.startswith("    ") or line.strip() == "":
                multiline_val.append(line)
                continue
            else:
                current[multiline_key] = "\n".join(multiline_val).strip()
                in_multiline = False
                multiline_val = []
        stripped = line.strip()
        if stripped.startswith("- "):
            if current and ("id" in current or "rule_id" in current):
                items.append(current)
            current = {}
            kv = stripped[2:].split(":", 1)
            if len(kv) == 2:
                current[kv[0].strip()] = kv[1].strip().strip('"').strip("'")
        elif ":" in stripped and not stripped.startswith("#"):
            kv = stripped.split(":", 1)
            key = kv[0].strip()
            val = kv[1].strip()
            if val == "|":
                in_multiline = True
                multiline_key = key
                multiline_val = []
            elif val:
                current[key] = val.strip('"').strip("'")
    if current and ("id" in current or "rule_id" in current):
        items.append(current)
    return items


def _load_disease_catalog():
    catalog = {}
    if not DISEASES_YAML.exists():
        return catalog
    try:
        for d in _parse_yaml_simple(DISEASES_YAML):
            rid = d.get("id", "")
            if rid:
                catalog[rid] = {
                    "name": d.get("name", ""),
                    "layer": d.get("layer", ""),
                    "severity": d.get("severity", "P2"),
                }
    except Exception:
        pass
    return catalog


def _calc_health(active_faults, dismissed_faults, archived_faults, reset_ts):
    if reset_ts:
        all_old = True
        for f in active_faults:
            if f["fault_id"] in dismissed_faults or f["fault_id"] in archived_faults:
                continue
            if f.get("first_seen", 0) >= reset_ts:
                all_old = False
                break
        if all_old:
            return "healthy"

    has_p0 = has_p1 = has_p2 = False
    seen = set()
    for f in active_faults:
        fid = f.get("fault_id", "")
        if fid in dismissed_faults or fid in archived_faults or fid in seen:
            continue
        seen.add(fid)
        sev = f.get("severity", "")
        if sev == "P0":
            has_p0 = True
        elif sev == "P1":
            has_p1 = True
        elif sev == "P2":
            has_p2 = True
    if has_p0:
        return "critical"
    if has_p1:
        return "warning"
    if has_p2:
        return "sub_healthy"
    return "healthy"


def _fmt_duration(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _fmt_probe(pt):
    if pt == -2:
        return "系统级"
    if pt < 0:
        return "未上报"
    if pt > 120:
        return f"离线({_fmt_duration(pt)})"
    return f"在线({_fmt_duration(pt)}前)"


def _probe_status_icon(pt):
    if pt == -2:
        return "🔵"
    if pt < 0:
        return "🔴"
    if pt > 120:
        return "🟠"
    return ""


def _fmt_month_report(year_month, rows):
    now = time.time()
    by_system = {}
    for r in rows:
        sys_name = r["system"]
        if sys_name not in by_system:
            by_system[sys_name] = {"total": 0, "errors": 0, "types": {}}
        by_system[sys_name]["total"] += r["total_count"]
        by_system[sys_name]["errors"] += r["error_count"]
        by_system[sys_name]["types"][r["event_type"]] = r["total_count"]

    ym_pretty = year_month.replace("_", "年") + "月"
    sys.stdout.write(f"╔══════════════════════════════════════════════════════════╗\n")
    sys.stdout.write(
        f"║  乾坤镜月度报告  {ym_pretty}                                 ║\n"
    )
    sys.stdout.write(
        f"╚══════════════════════════════════════════════════════════╝\n\n"
    )

    for sys_name, info in sorted(by_system.items()):
        total = info["total"]
        errors = info["errors"]
        ok = total - errors
        rate = (ok / total * 100) if total > 0 else 0
        top = sorted(info["types"].items(), key=lambda x: -x[1])[:3]
        top_str = "  ".join(f"{t}:{c}" for t, c in top)
        sys.stdout.write(
            f"  {sys_name:<12s}  {total:>8,} 事件  |  {errors:>5} 错误  |  成功 {rate:.1f}%\n"
        )
        if top_str:
            sys.stdout.write(f"    top: {top_str}\n")
        sys.stdout.write("\n")

    total_all = sum(v["total"] for v in by_system.values())
    total_err = sum(v["errors"] for v in by_system.values())
    sys.stdout.write(f"── 合计: {total_all:,} 事件, {total_err:,} 错误 ──\n")
    sys.stdout.write(f"  报告生成: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")


def _cmd_monthly_report(year_month, json_output):
    if not load_monthly_summary:
        print(
            "archiver_summary plugin not available (delete archiver_summary.py to re-enable)"
        )
        return
    if not DB.exists():
        print("DB not found")
        return
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = load_monthly_summary(conn, year_month)
        if rows:
            if json_output:
                out = {"year_month": year_month, "systems": {}}
                for r in rows:
                    s = r["system"]
                    if s not in out["systems"]:
                        out["systems"][s] = {"total": 0, "errors": 0, "types": {}}
                    out["systems"][s]["total"] += r["total_count"]
                    out["systems"][s]["errors"] += r["error_count"]
                    out["systems"][s]["types"][r["event_type"]] = r["total_count"]
                print(json.dumps(out, indent=2, ensure_ascii=False))
            else:
                _fmt_month_report(year_month, rows)
        else:
            result = summarize_month(conn, year_month)
            conn.close()
            conn = None
            if result and "skipped" in result:
                print(f"月度报告 {year_month}: {result['skipped']}")
            elif result and "error" in result:
                print(f"月度报告 {year_month} 聚合失败: {result['error']}")
            else:
                conn2 = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
                conn2.row_factory = sqlite3.Row
                rows2 = load_monthly_summary(conn2, year_month)
                if rows2:
                    if json_output:
                        print(json.dumps(result, indent=2, ensure_ascii=False))
                    else:
                        _fmt_month_report(year_month, rows2)
                conn2.close()
                return
    finally:
        if conn:
            conn.close()


def cmd_report(args):
    month = getattr(args, "month", None)
    if month:
        _cmd_monthly_report(month, getattr(args, "json", False))
        return

    days = getattr(args, "days", 1) or 1
    since = time.time() - days * 86400
    now = time.time()

    # === 加载状态文件 ===
    dismissed_map = _load_json(DISMISSED_PATH)
    archived_map = _load_json(ARCHIVED_PATH)
    resets_map = _load_json(RESETS_PATH)

    # === 查询诊断 ===
    diagnoses = []
    if DB.exists():
        try:
            conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            year = time.strftime("%Y")
            tbl = f"diagnoses_{year}"
            try:
                rows = conn.execute(
                    f"SELECT diagnosis_id, system, fault_id, diagnosis_name, "
                    f"confidence, severity, status, created_at "
                    f"FROM {tbl} WHERE created_at > ? "
                    f"ORDER BY created_at DESC",
                    (since,),
                ).fetchall()
                diagnoses = [dict(r) for r in rows]
            except sqlite3.OperationalError:
                diagnoses = []
        except sqlite3.OperationalError:
            diagnoses = []
        finally:
            conn.close()

    if not diagnoses and not getattr(args, "json", False):
        period = f"近 {days} 天" if days > 1 else "近 24 小时"
        print(f"╔══════════════════════════════════════════════════════════╗")
        print(f"║  乾坤镜体检报告  ({period})  ✓ 无异常                        ║")
        print(f"╚══════════════════════════════════════════════════════════╝")
        return

    KNOWN_PROBES = {"tusunsun", "langchain", "openclaw", "mingjing", "opencode"}

    def _is_known(s):
        return any(s == name or s.startswith(name + "_") for name in KNOWN_PROBES)

    # === 按系统+故障ID聚合（合并同fault_id） ===
    by_system = {}
    _SKIP_SYSTEMS = {"__host__", "unknown", "all"}
    for d in diagnoses:
        sys_name = d.get("system", "unknown")
        if sys_name in _SKIP_SYSTEMS or not _is_known(sys_name):
            continue
        fid = d.get("fault_id", "")
        if sys_name not in by_system:
            by_system[sys_name] = {}
        if fid not in by_system[sys_name]:
            by_system[sys_name][fid] = []
        by_system[sys_name][fid].append(d)

    # === 构建实例卡片 ===
    cards = {}
    for sys_name, faults in by_system.items():
        active = []
        archived_list = []

        for fid, entries in faults.items():
            key = f"{fid}:{sys_name}"
            is_dismissed = key in dismissed_map
            is_archived = key in archived_map

            entry = dict(entries[0])
            entry["occurrence_count"] = len(entries)
            entry["first_seen"] = min(e.get("created_at", now) for e in entries)
            entry["last_seen"] = entry.get("created_at", now)
            entry["duration_seconds"] = max(0, now - entry["first_seen"])
            entry["is_dismissed"] = is_dismissed
            entry["is_archived"] = is_archived

            if is_dismissed:
                continue
            if is_archived:
                archived_list.append(entry)
            else:
                active.append(entry)

        active.sort(
            key=lambda x: (
                SEV_ORDER.get(x.get("severity", "P2"), 9),
                -x.get("confidence", 0),
            )
        )

        dismissed_fids = {
            k.rsplit(":", 1)[0] for k in dismissed_map if k.endswith(f":{sys_name}")
        }
        archived_fids = {
            k.rsplit(":", 1)[0] for k in archived_map if k.endswith(f":{sys_name}")
        }
        reset_ts = resets_map.get(sys_name)

        cards[sys_name] = {
            "display_name": sys_name,
            "active": active,
            "archived": archived_list,
            "health": _calc_health(active, dismissed_fids, archived_fids, reset_ts),
            "p0": sum(1 for f in active if f.get("severity") == "P0"),
            "p1": sum(1 for f in active if f.get("severity") == "P1"),
            "p2": sum(1 for f in active if f.get("severity") == "P2"),
            "dismissed_count": len(dismissed_fids),
            "archived_count": len(archived_fids),
        }

    # 补充只有事件、无诊断的系统（仅限 KNOWN_PROBES 中的探针 + mingjing）
    if DB.exists():
        try:
            conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout=5000")
            probe_systems = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT system FROM system_pid"
                ).fetchall()
            }
            probe_systems.add("mingjing")
            all_systems = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT system FROM events ORDER BY system"
                ).fetchall()
                if r[0] not in _SKIP_SYSTEMS
                and r[0] in probe_systems
                and _is_known(r[0])
            ]
            for s in all_systems:
                if s not in cards:
                    cards[s] = {
                        "display_name": s,
                        "active": [],
                        "archived": [],
                        "health": "healthy",
                        "p0": 0,
                        "p1": 0,
                        "p2": 0,
                        "dismissed_count": 0,
                        "archived_count": 0,
                    }
            conn.close()
        except sqlite3.OperationalError:
            pass

    # === 探针时间（每系统最新事件距今） ===
    probe_times = {}
    if DB.exists():
        try:
            conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout=5000")
            for s in cards:
                row = conn.execute(
                    "SELECT MAX(timestamp) FROM events WHERE system = ?", (s,)
                ).fetchone()
                if row and row[0]:
                    probe_times[s] = now - row[0]
                else:
                    probe_times[s] = -1
            conn.close()
        except sqlite3.OperationalError:
            for s in cards:
                probe_times[s] = -1

    # === 分诊覆盖率 ===
    triage_total = 0
    triage_ready = 0
    triage_layers = {}
    if TRIAGE_SNAPSHOT.exists():
        try:
            snap = json.loads(TRIAGE_SNAPSHOT.read_text())
            summary = snap.get("summary", {})
            triage_total = summary.get("total", 0)
            triage_ready = summary.get("ready", 0)
            rule_status = snap.get("rule_status", {})
            catalog = _load_disease_catalog()
            for rid, info in rule_status.items():
                layer = catalog.get(rid, {}).get("layer", "")
                if layer not in triage_layers:
                    triage_layers[layer] = {"ready": 0, "total": 0}
                triage_layers[layer]["total"] += 1
                if info.get("status") == "ready":
                    triage_layers[layer]["ready"] += 1
        except Exception:
            pass

    # === per-system triage (基于采样事件类型) ===
    per_system_triage = {}
    if DB.exists():
        try:
            conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout=5000")
            for s in cards:
                rows = conn.execute(
                    "SELECT DISTINCT event_type FROM events WHERE system = ?", (s,)
                ).fetchall()
                event_types = {r[0] for r in rows}
                catalog = _load_disease_catalog()
                total = len(catalog)
                ready = 0
                if TRIAGE_SNAPSHOT.exists():
                    snap = json.loads(TRIAGE_SNAPSHOT.read_text())
                    rule_status = snap.get("rule_status", {})
                    for rid, info in rule_status.items():
                        if info.get("status") == "ready":
                            ready += 1
                per_system_triage[s] = {"ready": ready, "total": total}
            conn.close()
        except Exception:
            pass

    # mingjing 是系统级实例，无 PID / 无探针 / 无疾病谱
    if "mingjing" in probe_times:
        probe_times["mingjing"] = -2
    if "mingjing" in per_system_triage:
        per_system_triage["mingjing"] = {"ready": -1, "total": 0}

    # === 系统状态 ===
    hot_count = sum(1 for _ in HOT.glob("*.jsonl")) if HOT.exists() else 0
    cold_count = sum(1 for _ in COLD.glob("*.jsonl")) if COLD.exists() else 0
    db_size = DB.stat().st_size / (1024 * 1024) if DB.exists() else 0
    archiver_alive = False
    archiver_age = 999
    if HB.exists():
        try:
            archiver_age = now - float(HB.read_text().strip())
            archiver_alive = archiver_age < 60
        except (ValueError, OSError):
            pass

    # === 全局统计 ===
    unhealthy = [c for c in cards.values() if c["health"] != "healthy"]
    unhealthy.sort(key=lambda c: HL_ORDER.get(c["health"], 3))
    healthy_list = [c for c in cards.values() if c["health"] == "healthy"]

    total_p0 = sum(c["p0"] for c in cards.values())
    total_p1 = sum(c["p1"] for c in cards.values())
    total_p2 = sum(c["p2"] for c in cards.values())
    total_active_faults = set()
    for c in cards.values():
        for f in c["active"]:
            total_active_faults.add(f.get("fault_id", ""))

    total_dismissed = sum(c["dismissed_count"] for c in cards.values())
    total_archived = sum(c["archived_count"] for c in cards.values())

    probe_online = 0
    probe_offline = 0
    probe_never = 0
    for s in cards:
        pt = probe_times.get(s, -1)
        if pt == -2:
            continue
        if pt < 0:
            probe_never += 1
        elif pt > 120:
            probe_offline += 1
        else:
            probe_online += 1

    global_worst = "healthy"
    for c in cards.values():
        if HL_ORDER.get(c["health"], 3) < HL_ORDER.get(global_worst, 3):
            global_worst = c["health"]

    # === JSON 输出 ===
    if getattr(args, "json", False):
        out = {
            "generated_at": now,
            "period_days": days,
            "global_health": global_worst,
            "global_health_label": LABEL_HL.get(global_worst),
            "total_instances": len(cards),
            "healthy_count": len(healthy_list),
            "unhealthy_count": len(unhealthy),
            "active_faults": len(total_active_faults),
            "p0": total_p0,
            "p1": total_p1,
            "p2": total_p2,
            "dismissed": total_dismissed,
            "archived": total_archived,
            "probe": {
                "online": probe_online,
                "offline": probe_offline,
                "never": probe_never,
            },
            "archiver_alive": archiver_alive,
            "archiver_age_sec": round(archiver_age, 1),
            "db_size_mb": round(db_size, 1),
            "hot_files": hot_count,
            "cold_files": cold_count,
            "triage": {
                "ready": triage_ready,
                "total": triage_total,
                "layers": triage_layers,
            },
            "instances": [
                {
                    "name": c["display_name"],
                    "health": c["health"],
                    "health_label": LABEL_HL.get(c["health"]),
                    "p0": c["p0"],
                    "p1": c["p1"],
                    "p2": c["p2"],
                    "active": [
                        {
                            "severity": f.get("severity"),
                            "fault_id": f.get("fault_id"),
                            "name": f.get("diagnosis_name"),
                            "count": f.get("occurrence_count"),
                            "first_seen": f.get("first_seen"),
                            "duration_seconds": f.get("duration_seconds"),
                        }
                        for f in c["active"]
                    ],
                    "archived_count": c["archived_count"],
                    "dismissed_count": c["dismissed_count"],
                    "probe_seconds_ago": probe_times.get(c["display_name"], -1),
                    "triage": per_system_triage.get(
                        c["display_name"], {"ready": 0, "total": 0}
                    ),
                }
                for c in sorted(
                    cards.values(),
                    key=lambda c: (
                        HL_ORDER.get(c["health"], 3),
                        c["display_name"],
                    ),
                )
            ],
        }
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return

    # ==== 终端输出 ====
    period = f"近 {days} 天" if days > 1 else "近 24 小时"
    print(f"╔══════════════════════════════════════════════════════════╗")
    print(f"║  乾坤镜体检报告  ({period})                                  ║")
    print(f"╚══════════════════════════════════════════════════════════╝")
    print()

    # 总体健康
    hl_icon = ICON_HL.get(global_worst, "⚪")
    hl_label = LABEL_HL.get(global_worst, "未知")
    print(f"── 总体健康: {hl_label} {hl_icon} ──")
    parts = [
        f"实例{len(cards)}(健康{len(healthy_list)} 亚健康{sum(1 for c in cards.values() if c['health'] == 'sub_healthy')} 警告{sum(1 for c in cards.values() if c['health'] == 'warning')} 危急{sum(1 for c in cards.values() if c['health'] == 'critical')})"
    ]
    parts.append(
        f"活跃疾病{len(total_active_faults)}(P0×{total_p0} P1×{total_p1} P2×{total_p2})"
    )
    parts.append(f"探针: 🟢{probe_online} 🟠{probe_offline} 🔴{probe_never}")
    extra = []
    if total_dismissed:
        extra.append(f"忽略中{total_dismissed}")
    if total_archived:
        extra.append(f"已归档{total_archived}")
    if extra:
        parts.append(" ".join(extra))
    print(f"  {'  '.join(parts)}")
    print()

    # 需要关注的实例
    if unhealthy:
        print("── ⚠️ 需要关注 ──")
        print()
        for c in unhealthy:
            pt = probe_times.get(c["display_name"], -1)
            probe_icon = _probe_status_icon(pt)
            probe_str = _fmt_probe(pt)
            triage_str = ""
            triage_info = per_system_triage.get(c["display_name"], {})
            if triage_info:
                if triage_info.get("ready", 0) < 0:
                    triage_str = "  系统级"
                else:
                    triage_str = f"  本系统{triage_info.get('ready', 0)}"

            hl_icon = ICON_HL.get(c["health"], "⚪")
            hl_label = LABEL_HL.get(c["health"], "未知")
            print(
                f"  {hl_icon} {hl_label} | {c['display_name']}    {probe_icon}{probe_str}{triage_str}"
            )

            for f in c["active"]:
                sev = f.get("severity", "?")
                sev_icon = ICON_SEV.get(sev, "⚪")
                fid = f.get("fault_id", "?")
                name = f.get("diagnosis_name", "?")

                first = time.strftime("%m-%d", time.localtime(f.get("first_seen", now)))
                dur = _fmt_duration(f.get("duration_seconds", 0))
                cnt = f.get("occurrence_count", 0)

                print(f"     {sev_icon} {sev} | {fid} {name}")
                print(f"        首次{first}  持续{dur}  共{cnt}次")
            print()

    # 健康实例
    if healthy_list:
        print("── ✓ 健康 ──")
        names = []
        for c in healthy_list:
            pt = probe_times.get(c["display_name"], -1)
            probe_icon = _probe_status_icon(pt)
            probe_str = _fmt_probe(pt)
            triage_info = per_system_triage.get(c["display_name"], {})
            if triage_info.get("ready", 0) < 0:
                triage = ""
            elif triage_info:
                triage = f"本系统{triage_info.get('ready', '-')}"
            else:
                triage = ""
            names.append(
                f"{c['display_name']}({probe_icon}{probe_str}{' ' + triage if triage else ''})"
            )
        print(f"  {',  '.join(names)}")
        print()

    # 已归档
    archived_all = []
    for c in cards.values():
        for f in c["archived"]:
            archived_all.append((c["display_name"], f))
    if archived_all:
        print(f"── ⚐ 已归档 ──")
        for sys_name, f in archived_all[:20]:
            sev = f.get("severity", "?")
            sev_icon = ICON_SEV.get(sev, "⚪")
            fid = f.get("fault_id", "?")
            name = f.get("diagnosis_name", "?")
            print(f"  [{sys_name}] {sev_icon} {sev} | {fid} {name}")
        if len(archived_all) > 20:
            print(f"  ... 还有 {len(archived_all) - 20} 个")
        print()

    # 可用操作（不提供建议，只列操作）
    print("── 可用操作 ──")
    print(
        "  忽略本次: ming ignore <fault_id> -s <system>       (疾病仍然记录，仅报告不展示)"
    )
    print(
        "  归档疾病: ming archive-disease <fault_id> -s <system>  (永久隐藏，不计入健康)"
    )
    print(
        "  健康复位: ming reset <system>                      (强制标记健康，新疾病出现自动取消)"
    )
    print("  恢复显示: ming restore <fault_id> -s <system>       (取消忽略/归档)")
    print()

    # 系统状态
    print("── ⚙ 系统 ──")
    arch_status = (
        ("✓ 运行中" if archiver_alive else f"✗ 离线({archiver_age:.0f}s前)")
        if archiver_age != 999
        else "✗ 从未启动"
    )
    print(
        f"  归档器{arch_status}    数据库{db_size:.1f}MB    热轨{hot_count}文件    冷轨{cold_count}文件"
    )
    print()

    # 資源足跡
    _print_footprint()
    print()


def _read_proc_rss(pid):
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError):
        pass
    return 0


def _proc_cpu(pid):
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return round(float(r.stdout.strip()), 1)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return 0.0


def _print_footprint():
    print("── 📐 資源足跡 · Resource Footprint ──")

    # 乾坤镜自身：RSS 直接读 /proc（瞬时，不走 ps），CPU 读探针空闲采样
    self_rss = _read_proc_rss(os.getpid())
    self_cpu = 0.0
    if DB.exists():
        try:
            conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout=3000")
            row = conn.execute(
                "SELECT e.payload, e.storage_tier, b.payload_blob "
                "FROM events e LEFT JOIN events_blob b ON e.id = b.event_id "
                "WHERE e.event_type='platform_snapshot' "
                "AND e.system='__host__' ORDER BY e.timestamp DESC LIMIT 1"
            ).fetchone()
            conn.close()
            if row:
                pl = resolve_payload(row)
                if pl:
                    self_cpu = pl.get("cpu_percent", 0.0)
        except sqlite3.OperationalError:
            pass
    print(f"  {'mingjing':<20s} RSS {self_rss:>5.0f} MB   CPU {self_cpu:>5.1f}%")

    # 已注册系统
    if DB.exists():
        try:
            conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout=3000")
            rows = conn.execute(
                "SELECT system, pid, mode FROM system_pid WHERE pid > 0 ORDER BY system"
            ).fetchall()
            conn.close()
            for system, pid, mode in rows:
                if system == "mingjing":
                    continue
                rss = _read_proc_rss(pid)
                cpu = _proc_cpu(pid)
                if rss > 0:
                    label = f"{system}(probe)" if mode == "black" else system
                    print(f"  {label:<20s} RSS {rss:>5.0f} MB   CPU {cpu:>5.1f}%")
        except sqlite3.OperationalError:
            pass
