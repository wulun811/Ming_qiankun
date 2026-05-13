# cli_disease.py —— 0.11.9m 疾病操作命令
# 职责：忽略/归档/复位/恢复/实例列表 — 操作态命令（写入 JSON 状态文件）
import json, sqlite3, time, sys
from pathlib import Path
from i18n import _

sys.path.insert(0, str(Path(__file__).parent))
from archiver_util import diagnoses_query

DB = Path.home() / ".ming" / "ming.db"
DISMISSED_PATH = Path.home() / ".ming" / "dismissed_diseases.json"
ARCHIVED_PATH = Path.home() / ".ming" / "archived_diseases.json"
RESETS_PATH = Path.home() / ".ming" / "health_resets.json"
DISEASES_YAML = Path(__file__).parent.parent / "config" / "diseases.yaml"


def _load_json(path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_yaml_simple(path):
    """零依赖 YAML 解析器"""
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
            if current and "id" in current:
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
    if current and "id" in current:
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


def cmd_ignore(args):
    """忽略某疾病的某次报告"""
    fault_id = getattr(args, "fault_id", "")
    system = getattr(args, "system", "")
    if not fault_id or not system:
        print(_("用法: ming ignore <fault_id> -s <system>"))
        sys.exit(1)

    key = f"{fault_id}:{system}"
    data = _load_json(DISMISSED_PATH)
    data[key] = {
        "dismissed_at": time.time(),
        "expires_at": time.time() + 24 * 3600,
    }
    _save_json(DISMISSED_PATH, data)
    print(_("已忽略: %s（24小时后过期，疾病仍然记录，仅报告不展示）") % key)


def cmd_archive_disease(args):
    """归档某疾病的某报告"""
    fault_id = getattr(args, "fault_id", "")
    system = getattr(args, "system", "")
    if not fault_id or not system:
        print(_("用法: ming archive-disease <fault_id> -s <system>"))
        sys.exit(1)

    key = f"{fault_id}:{system}"
    data = _load_json(ARCHIVED_PATH)
    data[key] = {"archived_at": time.time()}
    _save_json(ARCHIVED_PATH, data)
    print(_("已归档: %s（永久隐藏，不计入健康评估）") % key)


def cmd_restore(args):
    """取消忽略/取消归档"""
    fault_id = getattr(args, "fault_id", "")
    system = getattr(args, "system", "")
    if not fault_id or not system:
        print(_("用法: ming restore <fault_id> -s <system>"))
        sys.exit(1)

    key = f"{fault_id}:{system}"
    restored = False

    for path in (DISMISSED_PATH, ARCHIVED_PATH):
        data = _load_json(path)
        if key in data:
            del data[key]
            _save_json(path, data)
            restored = True

    if restored:
        print(_("已恢复: %s") % key)
    else:
        print(_("未找到: %s（可能未被忽略/归档）") % key)


def cmd_reset(args):
    """健康复位：强制标记某实例为健康"""
    system = getattr(args, "system", "")
    if not system:
        print(_("用法: ming reset <system>"))
        sys.exit(1)

    data = _load_json(RESETS_PATH)
    data[system] = time.time()
    _save_json(RESETS_PATH, data)
    print(_("已复位: %s → 健康（新疾病出现时自动取消）") % system)


def cmd_reset_status(args):
    """查看复位状态"""
    data = _load_json(RESETS_PATH)
    if not data:
        print(_("无健康复位记录"))
        return

    system = getattr(args, "system", None)
    if system:
        ts = data.get(system)
        if ts:
            t = time.strftime("%m-%d %H:%M:%S", time.localtime(ts))
            print(_("%s: 复位于 %s（%.0fs前）") % (system, t, time.time() - ts))
        else:
            print(_("%s: 无复位记录") % system)
    else:
        for name, ts in sorted(data.items()):
            t = time.strftime("%m-%d %H:%M:%S", time.localtime(ts))
            print(_("  %s: 复位于 %s") % (name, t))


def _fmt_duration(seconds):
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.0f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _fmt_probe(probe_seconds):
    if probe_seconds is None or probe_seconds < 0:
        return _("🔴未上报")
    if probe_seconds > 120:
        return _("🟠离线%s") % _fmt_duration(probe_seconds)
    return _("🟢在线%s前") % _fmt_duration(probe_seconds)


def _fmt_health(level):
    icons = {
        "healthy": _("[健康]"),
        "sub_healthy": _("[亚健康]"),
        "warning": _("[警告]"),
        "critical": _("[危急]"),
    }
    return icons.get(level, level)


def cmd_instance_list(args):
    """列出所有实例及其健康状态"""
    if not DB.exists():
        print(_("数据库不存在"))
        return

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    now = time.time()

    systems_set = set()

    # 从 events 表收集系统
    try:
        for r in conn.execute("SELECT DISTINCT system FROM events").fetchall():
            systems_set.add(r[0])
    except sqlite3.OperationalError:
        pass

    # 从 diagnoses 表收集（可能有诊断但无事件的系统）
    try:
        for r in diagnoses_query(conn, "DISTINCT system"):
            systems_set.add(r[0])
    except Exception:
        pass

    systems = sorted(systems_set)

    # 收集每系统的最新探针时间
    probe_times = {}
    for s in systems:
        row = conn.execute(
            "SELECT MAX(timestamp) FROM events WHERE system = ?", (s,)
        ).fetchone()
        if row and row[0]:
            probe_times[s] = now - row[0]
        else:
            probe_times[s] = -1

    # 加载外部状态
    dismissed = _load_json(DISMISSED_PATH)
    archived = _load_json(ARCHIVED_PATH)
    resets = _load_json(RESETS_PATH)

    # 收集每系统的诊断（按 fault_id 合并，过滤忽略/归档）
    per_system_dx = {}
    try:
        rows = diagnoses_query(conn, "system, fault_id, severity")
        for r in rows:
            s = r["system"]
            fid = r["fault_id"]
            key = f"{fid}:{s}"
            if key in dismissed or key in archived:
                continue
            if s not in per_system_dx:
                per_system_dx[s] = {"p0": 0, "p1": 0, "p2": 0, "faults": set()}
            sev = r["severity"] or "P2"
            if sev == "P0":
                per_system_dx[s]["p0"] += 1
            elif sev == "P1":
                per_system_dx[s]["p1"] += 1
            elif sev == "P2":
                per_system_dx[s]["p2"] += 1
            per_system_dx[s]["faults"].add(fid)
    except sqlite3.OperationalError:
        pass

    def calc_health(dx_data, sys_name):
        reset_ts = resets.get(sys_name)
        if reset_ts:
            return "healthy"
        if dx_data is None:
            return "healthy"
        if dx_data["p0"] > 0:
            return "critical"
        if dx_data["p1"] > 0:
            return "warning"
        if dx_data["p2"] > 0:
            return "sub_healthy"
        return "healthy"

    conn.close()

    # 输出
    h_cols = (_("实例"), _("探针"), _("健康"), _("疾病"))
    header = f"{h_cols[0]:<18} {h_cols[1]:<14} {h_cols[2]:<8} {h_cols[3]:<6} {'P0':<4} {'P1':<4} {'P2':<4}"
    sep = "─" * len(header)
    print(header)
    print(sep)

    hl_order = {"critical": 0, "warning": 1, "sub_healthy": 2, "healthy": 3}
    sorted_systems = sorted(
        systems,
        key=lambda s: hl_order.get(calc_health(per_system_dx.get(s), s), 3),
    )

    for s in sorted_systems:
        dx = per_system_dx.get(s)
        health = calc_health(dx, s)
        probe = probe_times.get(s)
        probe_str = _fmt_probe(probe)
        dx_count = len(dx["faults"]) if dx else 0
        p0 = dx["p0"] if dx else 0
        p1 = dx["p1"] if dx else 0
        p2 = dx["p2"] if dx else 0
        health_label = _fmt_health(health)

        extra = ""
        if resets.get(s):
            extra = _(" [复位]")
        d_count = sum(1 for k in dismissed if k.endswith(f":{s}"))
        a_count = sum(1 for k in archived if k.endswith(f":{s}"))
        if d_count or a_count:
            parts = []
            if a_count:
                parts.append(_("归档%d") % a_count)
            if d_count:
                parts.append(_("忽略%d") % d_count)
            extra += f" ({', '.join(parts)})"

        print(
            f"{s:<18} {probe_str:<14} {health_label:<8} {dx_count:<6} {p0:<4} {p1:<4} {p2:<4}{extra}"
        )
