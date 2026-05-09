# cli_health.py —— 0.11.9m 健康/状态/自检命令
# 职责：cmd_health / cmd_self_check / cmd_status
import json, sqlite3, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from archiver_util import diagnoses_query


def cmd_health(args):
    """健康状态查询"""
    from self_health import run_check

    results, overall = run_check()
    if getattr(args, "json", False):
        out = {"overall": overall, "checks": {}}
        for name, r in results.items():
            out["checks"][name] = {
                "status": r["status"],
                "value": r["value"],
                "threshold": r["threshold"],
            }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        sys.exit(0 if overall == "ok" else (1 if overall == "warn" else 2))

    icons = {"ok": "[OK]", "warn": "[!!]", "crit": "[XX]", "error": "[??]"}
    print("乾坤镜健康状态")
    print("=" * 40)
    for name, r in results.items():
        icon = icons.get(r["status"], "[??]")
        val = r["value"]
        val_str = f"{val}" if val is not None else "N/A"
        print(f"  {icon} {name}: {val_str} (阈值 {r['threshold']})")
    print(f"\n总体状态: {overall}")
    sys.exit(0 if overall == "ok" else (1 if overall == "warn" else 2))


def cmd_self_check(args):
    """自健康检查（8项指标）"""
    from self_health import run_check

    results, overall = run_check()
    labels = {
        "archiver_lag": "归档器延迟",
        "wal_size": "WAL 大小",
        "vacuum_due": "VACUUM 状态",
        "hot_dir": "热轨堆积",
        "disk_free": "磁盘剩余",
        "otel_bridge": "OTEL Bridge",
        "lit_lite": "诊断引擎",
        "otel_export": "OTEL 导出",
    }
    suggestion_map = {
        "vacuum_due": "执行 VACUUM：ming admin vacuum",
        "archiver_lag": "检查归档器是否正常运行",
        "wal_size": "WAL 过大，建议执行 VACUUM",
        "disk_free": "磁盘空间不足，清理旧数据",
        "hot_dir": "热轨文件堆积过多，检查归档器",
        "otel_bridge": "检查 OTEL Bridge 是否启动",
        "lit_lite": "检查诊断引擎是否卡死",
        "otel_export": "检查 OTEL 导出配置",
    }
    suggestions = [
        suggestion_map[name]
        for name, r in results.items()
        if r["status"] in ("warn", "crit") and name in suggestion_map
    ]
    if getattr(args, "json", False):
        out = {"results": {}, "overall": overall, "suggestions": suggestions}
        for name, r in results.items():
            val = r["value"]
            if name == "otel_bridge":
                val = val.get("status") if isinstance(val, dict) else val
            out["results"][labels.get(name, name)] = {
                "status": r["status"],
                "value": val,
                "threshold": r["threshold"],
            }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        sys.exit(0 if overall == "ok" else (1 if overall == "warn" else 2))

    icons = {"ok": "[OK]", "warn": "[!!]", "crit": "[XX]", "error": "[??]"}
    print("乾坤镜自健康检查")
    print("=" * 40)
    for name, r in results.items():
        icon = icons.get(r["status"], "[??]")
        val = r["value"]
        thresh = r["threshold"]
        if name == "archiver_lag":
            val_str = f"{val:.1f}s" if val is not None else "N/A"
        elif name in ("wal_size", "disk_free"):
            val_str = f"{val:.0f}MB" if val is not None else "N/A"
        elif name == "hot_dir":
            val_str = f"{val} 文件" if val is not None else "N/A"
        elif name == "vacuum_due":
            val_str = "建议执行" if val else "正常"
        elif name == "otel_bridge":
            val_str = (
                val
                if isinstance(val, str)
                else (val.get("status") if isinstance(val, dict) else "N/A")
            )
        elif name == "lit_lite":
            val_str = f"{val:.1f}s" if val is not None else "N/A"
        elif name == "otel_export":
            val_str = f"{val * 100:.0f}%" if val is not None else "N/A"
        else:
            val_str = str(val)
        print(f"  {icon} {labels.get(name, name)}：{val_str}（阈值 {thresh}）")
    if suggestions:
        print("\n建议动作：")
        for i, s in enumerate(suggestions, 1):
            print(f"  {i}. {s}")
    sys.exit(0 if overall == "ok" else (1 if overall == "warn" else 2))


def cmd_status(args):
    hot = Path.home() / ".ming" / "hot"
    cold = Path.home() / ".ming" / "cold"
    db = Path.home() / ".ming" / "ming.db"
    hb = Path.home() / ".ming" / ".archiver_heartbeat"
    plugins = Path.home() / ".ming" / "plugins"

    hot_count = sum(1 for _ in hot.glob("*.jsonl")) if hot.exists() else 0
    cold_count = sum(1 for _ in cold.glob("*.jsonl")) if cold.exists() else 0
    db_size = db.stat().st_size / (1024 * 1024) if db.exists() else None
    archiver_age = None
    if hb.exists():
        try:
            archiver_age = time.time() - float(hb.read_text().strip())
        except (ValueError, OSError):
            archiver_age = None
    archiver_alive = archiver_age is not None and archiver_age < 60
    plugin_count = (
        len([p for p in plugins.glob("*") if p.is_dir()]) if plugins.exists() else 0
    )

    if getattr(args, "json", False):
        out = {
            "hot_files": hot_count,
            "cold_files": cold_count,
            "db_size_mb": round(db_size, 1) if db_size else None,
            "archiver_alive": archiver_alive,
            "archiver_age_sec": round(archiver_age, 1) if archiver_age else None,
            "plugins": plugin_count,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    print(f"Hot track:  {hot_count} files")
    print(f"Cold track: {cold_count} files")
    print(f"DB size:    {db_size:.1f} MB" if db_size else "DB size:    N/A")
    if archiver_age is not None:
        print(
            f"Archiver:   {'alive' if archiver_age < 60 else f'dead ({archiver_age:.0f}s ago)'}"
        )
    else:
        print("Archiver:   never started")
    print(f"Plugins:    {plugin_count}")

    # === 实例健康一览 + 探针状态 ===
    if db.exists():
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row

            now = time.time()

            systems_set = set()
            for r in conn.execute("SELECT DISTINCT system FROM events").fetchall():
                systems_set.add(r[0])
            try:
                for r in diagnoses_query(conn, "DISTINCT system"):
                    systems_set.add(r[0])
            except Exception:
                pass
            systems = sorted(systems_set)

            dismissed = {}
            dismissed_path = Path.home() / ".ming" / "dismissed_diseases.json"
            if dismissed_path.exists():
                try:
                    dismissed = json.loads(dismissed_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            archived = {}
            archived_path = Path.home() / ".ming" / "archived_diseases.json"
            if archived_path.exists():
                try:
                    archived = json.loads(archived_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            resets = {}
            resets_path = Path.home() / ".ming" / "health_resets.json"
            if resets_path.exists():
                try:
                    resets = json.loads(resets_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            icons = {
                "healthy": "🟢",
                "sub_healthy": "🟡",
                "warning": "🟠",
                "critical": "🔴",
            }

            print()
            print("── 实例健康 ──")
            for s in systems:
                # 探针时间
                row = conn.execute(
                    "SELECT MAX(timestamp) FROM events WHERE system = ?", (s,)
                ).fetchone()
                probe_age = 999
                if row and row[0]:
                    probe_age = now - row[0]

                if probe_age < 0:
                    probe_str = "🔴未上报"
                elif probe_age > 120:
                    h = probe_age // 3600
                    probe_str = (
                        f"🟠离线{h}h" if h > 0 else f"🟠离线{probe_age / 60:.0f}m"
                    )
                else:
                    probe_str = f"🟢在线{probe_age:.0f}s前"

                # 诊断（过滤忽略/归档）
                try:
                    dx_rows = diagnoses_query(
                        conn, "severity, fault_id", "system = ?", (s,)
                    )
                except Exception:
                    dx_rows = []

                p0 = p1 = p2 = 0
                reset_ts = resets.get(s)
                for r in dx_rows:
                    fid = r["fault_id"]
                    key = f"{fid}:{s}"
                    if key in dismissed or key in archived:
                        continue
                    sev = r["severity"]
                    if sev == "P0":
                        p0 += 1
                    elif sev == "P1":
                        p1 += 1
                    elif sev == "P2":
                        p2 += 1

                if reset_ts:
                    health = "healthy"
                elif p0 > 0:
                    health = "critical"
                elif p1 > 0:
                    health = "warning"
                elif p2 > 0:
                    health = "sub_healthy"
                else:
                    health = "healthy"

                icon = icons.get(health, "")

                # 统计忽略/归档
                d_count = sum(1 for k in dismissed if k.endswith(f":{s}"))
                a_count = sum(1 for k in archived if k.endswith(f":{s}"))
                extra = ""
                if d_count or a_count:
                    parts = []
                    if d_count:
                        parts.append(f"忽略{d_count}")
                    if a_count:
                        parts.append(f"归档{a_count}")
                    extra = f" ({', '.join(parts)})"
                if reset_ts:
                    extra += " [已复位]"

                dx_info = f"P0:{p0} P1:{p1} P2:{p2}" if dx_rows else "无疾病"
                print(f"  {icon} {s:<20s} {probe_str:<12s} {dx_info}{extra}")

            conn.close()
        except Exception:
            pass
