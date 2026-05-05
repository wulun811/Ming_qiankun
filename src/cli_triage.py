# cli_triage.py —— 0.11.9m 分诊命令
# 职责：triage run / status / report
import json, sqlite3, sys, time
from pathlib import Path


def cmd_triage(args):
    """分诊器命令：run / status / report"""
    sys.path.insert(0, str(Path(__file__).parent))
    from triage import run as triage_run, TRIAGE_SNAPSHOT

    action = args.action

    if action == "run":
        snapshot = triage_run(verbose=not getattr(args, "json", False))
        if not snapshot:
            print("分诊失败：数据库不存在或为空")
            sys.exit(1)

        if getattr(args, "json", False):
            out = {"snapshot": snapshot, "diagnoses_generated": False}
            if not getattr(args, "no_diagnose", False):
                from lit_lite import diagnose

                diagnose()
                out["diagnoses_generated"] = True
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return

        if not getattr(args, "no_diagnose", False):
            print("\n正在运行诊断引擎...")
            from lit_lite import diagnose

            diagnose()
            print("诊断完成")

    elif action == "status":
        if not TRIAGE_SNAPSHOT.exists():
            print("分诊快照：不存在")
            print("请运行: ming triage run")
            sys.exit(1)
        try:
            data = json.loads(TRIAGE_SNAPSHOT.read_text())
            age = time.time() - data.get("generated_at", 0)
            summary = data.get("summary", {})
            if getattr(args, "json", False):
                out = {
                    "generated_at": data.get("generated_at"),
                    "age_minutes": round(age / 60, 1),
                    "valid": age < 86400,
                    "events_sampled": data.get("scan_stats", {}).get(
                        "events_sampled", 0
                    ),
                    "total_rules": summary.get("total", 0),
                    "ready": summary.get("ready", 0),
                    "degraded": summary.get("degraded", 0),
                    "blocked": summary.get("blocked", 0),
                }
                print(json.dumps(out, indent=2, ensure_ascii=False))
                return
            print("分诊快照状态")
            print("=" * 40)
            print(
                f"  生成时间：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(data['generated_at']))}"
            )
            print(f"  距今：{age / 60:.0f} 分钟（{'有效' if age < 86400 else '过期'}）")
            print(
                f"  采样事件：{data.get('scan_stats', {}).get('events_sampled', 0)} 条"
            )
            print(f"  总规则数：{summary.get('total', 0)}")
            print(f"  ✅ 可诊断：{summary.get('ready', 0)}")
            print(f"  ⚠️  部分可诊：{summary.get('degraded', 0)}")
            print(f"  ❌ 不能诊断：{summary.get('blocked', 0)}")
        except (json.JSONDecodeError, KeyError, OSError) as e:
            print(f"快照读取失败：{e}")
            sys.exit(1)

    elif action == "report":
        if not TRIAGE_SNAPSHOT.exists():
            print("分诊快照：不存在")
            print("请运行: ming triage run")
            sys.exit(1)
        try:
            data = json.loads(TRIAGE_SNAPSHOT.read_text())
            rule_status = data.get("rule_status", {})
            suggestions = data.get("suggestions", [])
            scan_stats = data.get("scan_stats", {})
            summary = data.get("summary", {})

            if getattr(args, "json", False):
                ready_rules = []
                degraded_rules = []
                blocked_rules = []
                for did, info in sorted(rule_status.items()):
                    status = info.get("status", "unknown")
                    reason = info.get("reason", "")
                    entry = {"id": did, "reason": reason}
                    if status == "ready":
                        ready_rules.append(entry)
                    elif status == "degraded":
                        degraded_rules.append(entry)
                    else:
                        blocked_rules.append(entry)
                out = {
                    "generated_at": data.get("generated_at"),
                    "event_types": scan_stats.get("event_type_counts", {}),
                    "events_sampled": scan_stats.get("events_sampled", 0),
                    "total_rules": summary.get("total", 0),
                    "ready": ready_rules,
                    "degraded": degraded_rules,
                    "blocked": blocked_rules,
                    "suggestions": suggestions,
                }
                print(json.dumps(out, indent=2, ensure_ascii=False))
                return

            print("乾坤镜分诊报告")
            print("=" * 40)
            print(f"事件类型：{len(scan_stats.get('event_type_counts', {}))} 种")
            for et, cnt in sorted(scan_stats.get("event_type_counts", {}).items()):
                print(f"  {et}: {cnt}")
            print(f"采样事件：{scan_stats.get('events_sampled', 0)} 条")
            print()

            ready_rules = []
            degraded_rules = []
            blocked_rules = []
            for did, info in sorted(rule_status.items()):
                status = info.get("status", "unknown")
                reason = info.get("reason", "")
                if status == "ready":
                    ready_rules.append((did, reason))
                elif status == "degraded":
                    degraded_rules.append((did, reason))
                else:
                    blocked_rules.append((did, reason))

            if ready_rules:
                print(f"✅ 可诊断（{len(ready_rules)} 种）：")
                for did, reason in ready_rules[:20]:
                    print(f"  {did}: {reason}")
                if len(ready_rules) > 20:
                    print(f"  ... 还有 {len(ready_rules) - 20} 种")
                print()

            if degraded_rules:
                print(f"⚠️  部分可诊断（{len(degraded_rules)} 种）：")
                for did, reason in degraded_rules[:10]:
                    print(f"  {did}: {reason}")
                if len(degraded_rules) > 10:
                    print(f"  ... 还有 {len(degraded_rules) - 10} 种")
                print()

            if blocked_rules:
                print(f"❌ 不能诊断（{len(blocked_rules)} 种）：")
                for did, reason in blocked_rules[:10]:
                    print(f"  {did}: {reason}")
                if len(blocked_rules) > 10:
                    print(f"  ... 还有 {len(blocked_rules) - 10} 种")
                print()

            # 每系统诊病覆盖
            db = Path.home() / ".ming" / "ming.db"
            if db.exists():
                try:
                    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                    conn.execute("PRAGMA busy_timeout=5000")
                    systems = [
                        r[0]
                        for r in conn.execute(
                            "SELECT DISTINCT system FROM events ORDER BY system"
                        ).fetchall()
                    ]
                    if systems:
                        print()
                        print("── 每系统诊病数 ──")
                        diseases_yaml = (
                            Path(__file__).parent.parent / "config" / "diseases.yaml"
                        )
                        disease_deps = {}
                        if diseases_yaml.exists():
                            # simple catalog load
                            try:
                                from lit_rule import load_diseases_yaml

                                catalog_raw = load_diseases_yaml() or []
                                for rule in catalog_raw:
                                    rid = rule.get("id", "")
                                    scope = rule.get("scope", "observable")
                                    deps = rule.get("depends", {})
                                    ev_types = deps.get("event_types", [])
                                    if (
                                        scope not in ("mcp_required", "external_audit")
                                        and ev_types
                                    ):
                                        disease_deps[rid] = set(ev_types)
                            except Exception:
                                pass

                        total_rules = len(disease_deps) or summary.get("total", 0)
                        for s in systems:
                            rows = conn.execute(
                                "SELECT DISTINCT event_type FROM events WHERE system = ?",
                                (s,),
                            ).fetchall()
                            sys_events = {r[0] for r in rows}
                            if disease_deps:
                                ready = sum(
                                    1
                                    for evs in disease_deps.values()
                                    if evs.issubset(sys_events)
                                )
                            else:
                                ready = 0
                            pct = 100 * ready / max(total_rules, 1)
                            bar_len = 10
                            filled = int(pct / 10)
                            bar = "█" * filled + "░" * (bar_len - filled)
                            print(
                                f"  {s:<20s} [{bar}] {ready}/{total_rules} ({pct:.0f}%)"
                            )
                    conn.close()
                except Exception:
                    pass
        except (json.JSONDecodeError, KeyError, OSError) as e:
            print(f"报告读取失败：{e}")
            sys.exit(1)
