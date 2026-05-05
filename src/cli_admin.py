# cli_admin.py —— 0.11.9m 管理命令
# 职责：cmd_admin (forget / vacuum / cleanup) / cmd_exclude (add / remove / list) / cmd_probe (list / uninstall)
import json, os, sqlite3, sys, time
from pathlib import Path

from archiver_exclude import _load_excluded, _save_excluded

_PROTECTED_SYSTEMS = {"__admin__", "__self_health__", "__host__", "unknown"}


def _escape_like(s):
    return s.replace("%", "\\%").replace("_", "\\_").replace("\\", "\\\\")


def _open_db(db_path, readonly=False):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro" if readonly else str(db_path), uri=readonly)
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _write_audit(hot_dir, event_type, payload):
    hot_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    fp = hot_dir / f"{event_type}_{ts}_{os.getpid()}.jsonl"
    record = {"event_type": event_type, "system": "__admin__", "payload": payload, "timestamp": time.time()}
    with open(fp, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return fp


def _delete_diags_across_years(conn, system, row_counter=False):
    """删除/统计所有 diagnoses_{year} + expectations + probe_health 表中指定系统的记录"""
    total = 0
    dx_tbls = conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'diagnoses_%'").fetchall()
    for tbl in [t[0] for t in dx_tbls] + ["expectations", "probe_health"]:
        try:
            if row_counter:
                total += conn.execute(f"SELECT COUNT(*) FROM {tbl} WHERE system=?", (system,)).fetchone()[0]
            else:
                conn.execute(f"DELETE FROM {tbl} WHERE system=?", (system,))
        except sqlite3.OperationalError:
            pass
    return total


def cmd_admin(args):
    """管理命令：forget / vacuum / cleanup"""
    DB = Path.home() / ".ming" / "ming.db"
    HOT = Path.home() / ".ming" / "hot"

    if args.action == "forget":
        if not args.session_id:
            print("错误: 需要 --session-id 参数")
            sys.exit(1)
        if not getattr(args, "confirm", False):
            print("错误: 需要 --confirm 确认删除")
            sys.exit(1)
        if not DB.exists():
            print("数据库不存在: {}".format(DB)); sys.exit(1)

        conn = _open_db(DB)
        deleted_events = conn.execute(
            "DELETE FROM events WHERE json_extract(payload, '$.layer_agent.session_id') = ?",
            (args.session_id,),
        ).rowcount
        try:
            tbl = f"diagnoses_{time.strftime('%Y')}"
            deleted_dx = conn.execute(
                f"DELETE FROM {tbl} WHERE evidence LIKE ? ESCAPE '\\'",
                (f"%{_escape_like(args.session_id)}%",),
            ).rowcount
        except sqlite3.OperationalError:
            deleted_dx = 0
        conn.commit(); conn.close()

        fp = _write_audit(HOT, "__admin_action__", {
            "action": "forget", "session_id": args.session_id,
            "deleted_events": deleted_events, "deleted_diagnoses": deleted_dx,
            "operator": "cli", "timestamp": time.time(),
        })
        print(f"已删除 session {args.session_id}:\n  events: {deleted_events}\n  diagnoses: {deleted_dx}\n  审计事件已写入: {fp}")

    elif args.action == "vacuum":
        if not DB.exists():
            print("数据库不存在: {}".format(DB)); sys.exit(1)
        conn = _open_db(DB)
        before = DB.stat().st_size
        conn.execute("VACUUM"); conn.close()
        after = DB.stat().st_size

        fp = _write_audit(HOT, "__vacuum__", {
            "action": "vacuum", "before_size_mb": round(before / 1048576, 2),
            "after_size_mb": round(after / 1048576, 2),
            "operator": "cli", "timestamp": time.time(),
        })
        print(f"VACUUM 完成:\n  前: {before / 1048576:.2f} MB\n  后: {after / 1048576:.2f} MB\n  释放: {(before - after) / 1048576:.2f} MB\n  审计事件已写入: {fp}")

    elif args.action == "cleanup":
        if not DB.exists():
            print("数据库不存在: {}".format(DB)); sys.exit(1)

        conn = _open_db(DB)
        cleaned_events = cleaned_dx = 0
        for sys_name in ("unknown", "__admin__"):
            cleaned_events += conn.execute("DELETE FROM events WHERE system = ?", (sys_name,)).rowcount
            try:
                tbl = f"diagnoses_{time.strftime('%Y')}"
                cleaned_dx += conn.execute(f"DELETE FROM {tbl} WHERE system = ?", (sys_name,)).rowcount
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.execute("PRAGMA optimize"); conn.close()

        _write_audit(HOT, "__cleanup__", {
            "action": "cleanup", "cleaned_events": cleaned_events,
            "cleaned_diagnoses": cleaned_dx, "operator": "cli", "timestamp": time.time(),
        })
        print(f"清理完成:\n  events: {cleaned_events}\n  diagnoses: {cleaned_dx}")


def cmd_exclude(args):
    """停止/恢复观察某个实例：add / remove / list"""
    excluded = _load_excluded()

    if args.action == "list":
        print("已排除的实例:\n  " + "\n  ".join(sorted(excluded)) if excluded else "没有排除任何实例")
        return

    if not args.system:
        print("错误: 需要指定实例名称"); sys.exit(1)

    if args.action == "add":
        if args.system in excluded:
            print(f"实例 {args.system} 已在排除列表中"); return
        excluded.add(args.system)
        _save_excluded(excluded)
        print(f"已停止观察 {args.system}\n当前排除列表: {', '.join(sorted(excluded))}")
    elif args.action == "remove":
        if args.system not in excluded:
            print(f"实例 {args.system} 不在排除列表中"); return
        excluded.discard(args.system)
        _save_excluded(excluded)
        print(f"已恢复观察 {args.system}\n" + (f"当前排除列表: {', '.join(sorted(excluded))}" if excluded else "排除列表已清空"))


def cmd_probe(args):
    """探针管理：list / uninstall"""
    DB = Path.home() / ".ming" / "ming.db"
    HOT = Path.home() / ".ming" / "hot"
    COLD = Path.home() / ".ming" / "cold"
    PAUSED = Path.home() / ".ming" / ".paused"

    if args.action == "list":
        _cmd_probe_list(DB)
    elif args.action == "uninstall":
        if not args.system:
            print("错误: 卸载需要指定探针名称"); sys.exit(1)
        _cmd_probe_uninstall(DB, HOT, COLD, PAUSED, args)


def _cmd_probe_list(DB):
    """列出所有已注册的探针及状态"""
    if not DB.exists():
        print("暂无已注册的探针"); return

    conn = _open_db(DB, readonly=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                "SELECT system, last_seen, mode, "
                "COALESCE((SELECT COUNT(*) FROM events e WHERE e.system=sp.system),0) AS event_count "
                "FROM system_pid sp ORDER BY last_seen DESC"
            ).fetchall()
        except sqlite3.OperationalError:
            print("暂无已注册的探针"); return

        if not rows:
            print("暂无已注册的探针"); return

        now = time.time()
        pd = Path.home() / ".ming" / ".paused"
        paused = {f.name for f in pd.iterdir() if f.is_file()} if pd.exists() else set()
        excluded = _load_excluded()

        print(f"\n  {'探针名称':<18} {'最后心跳':<20} {'模式':<8} {'事件数':<8} {'状态'}")
        print("  " + "-" * 78)
        for r in rows:
            ago = now - r["last_seen"]
            if ago < 60:
                status = "活跃 (<1分)"
            elif ago < 3600:
                status = f"活跃 ({int(ago / 60)}分前)"
            elif ago < 86400:
                status = f"离线 ({int(ago / 3600)}小时前)"
            else:
                status = f"离线 ({int(ago / 86400)}天前)"
            if r["system"] in paused or r["system"] in excluded:
                status += " [已暂停]"
            print(f"  {r['system']:<18} {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['last_seen'])):<20} "
                  f"{r['mode']:<8} {r['event_count']:<8} {status}")
    finally:
        conn.close()


_PROTECTED_HINTS = {
    "__admin__": "用 'ming admin cleanup' 清理管理事件和自检数据",
    "__self_health__": "用 'ming admin cleanup' 清理管理事件和自检数据",
    "__host__": "停止 platform_probe 进程即可，主机监控数据建议保留",
    "unknown": "用 'ming admin cleanup' 清理 unknown 系统数据",
}


def _cmd_probe_uninstall(DB, HOT, COLD, PAUSED, args):
    """安全卸载探针"""
    system, dry_run = args.system, getattr(args, "dry_run", False)
    force, keep_data = getattr(args, "force", False), getattr(args, "keep_data", False)

    if system in _PROTECTED_SYSTEMS:
        print(f"错误: 不能卸载系统内部探针 '{system}'")
        if system in _PROTECTED_HINTS:
            print(f"提示: {_PROTECTED_HINTS[system]}")
        sys.exit(1)

    event_count = diagnosis_count = 0
    last_seen = None
    hot_files = sorted(HOT.glob(f"{system}_*.jsonl")) if HOT.exists() else []
    cold_files = sorted(COLD.glob(f"{system}_*.jsonl")) if COLD.exists() else []

    if DB.exists():
        conn = _open_db(DB)
        try:
            row = conn.execute("SELECT last_seen FROM system_pid WHERE system=?", (system,)).fetchone()
            if row:
                last_seen = row[0]
            event_count = conn.execute("SELECT COUNT(*) FROM events WHERE system=?", (system,)).fetchone()[0]
            diagnosis_count = _delete_diags_across_years(conn, system, row_counter=True)
        finally:
            conn.close()

    print(f"\n准备卸载探针: {system}")
    print(f"  事件: {event_count}  诊断: {diagnosis_count}  热轨: {len(hot_files)}  冷轨: {len(cold_files)}")
    if last_seen:
        ago = int(time.time() - last_seen)
        print(f"  最后心跳: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_seen))}"
              f"{'  ⚠ 可能仍在运行' if ago < 60 else ''}")
    elif event_count == 0:
        print("  数据库无记录，仅清理文件残留")

    if dry_run:
        print("\n[dry-run] 未执行变更"); return

    if not force:
        action_desc = ("备份 → 停止观察 → 清库 → 清文件 → VACUUM" if not keep_data
                       else "停止观察 + 清理热轨文件（保留记录）")
        if input(f"\n将执行: {action_desc}\n\n确认卸载？[y/N] ").strip().lower() not in ("y", "yes"):
            print("已取消"); return

    deleted_events = deleted_pid = deleted_files = 0

    if not keep_data and DB.exists() and event_count > 0:
        conn = _open_db(DB)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM events WHERE system=? ORDER BY timestamp", (system,)).fetchall()
            if rows:
                COLD.mkdir(parents=True, exist_ok=True)
                backup_file = COLD / f"{system}_backup_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
                with open(backup_file, "w", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(dict(r), ensure_ascii=False, default=str) + "\n")

            deleted_events = conn.execute("DELETE FROM events WHERE system=?", (system,)).rowcount
            deleted_pid = conn.execute("DELETE FROM system_pid WHERE system=?", (system,)).rowcount
            _delete_diags_across_years(conn, system)
            conn.commit()
            conn.execute("VACUUM")
        except (sqlite3.OperationalError, OSError) as e:
            print(f"操作失败: {e}"); return
        finally:
            conn.close()

    PAUSED.mkdir(parents=True, exist_ok=True)
    (PAUSED / system).write_text("")
    excluded = _load_excluded()
    excluded.add(system)
    _save_excluded(excluded)

    for fp in hot_files:
        try:
            fp.unlink(); deleted_files += 1
        except OSError:
            pass
    if not keep_data:
        for fp in cold_files:
            try:
                fp.unlink(); deleted_files += 1
            except OSError:
                pass

    _write_audit(HOT, "__probe_uninstall__", {
        "action": "uninstall", "target_system": system,
        "deleted_events": deleted_events, "deleted_pid": deleted_pid,
        "deleted_files": deleted_files, "keep_data": keep_data,
        "operator": "cli", "timestamp": time.time(),
    })

    if keep_data:
        print(f"\n已停止观察 {system}，数据已保留")
    else:
        parts = [f"{deleted_events} 事件", f"{deleted_pid} 注册"]
        if deleted_files:
            parts.append(f"{deleted_files} 文件")
        print(f"\n卸载完成: {' / '.join(parts)}")
