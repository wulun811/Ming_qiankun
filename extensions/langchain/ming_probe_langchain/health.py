# health.py —— 乾坤镜 LangChain 探针: 健康报告查询
# pip install ming-probe-langchain 后，用户可直接调用 get_health()
# 或把 MingHealthTool 交给 LangChain Agent 自主判断何时查健康

import json
import os
import socket
import time
from pathlib import Path

MING_HOME = Path.home() / ".ming"
HB = MING_HOME / ".archiver_heartbeat"
DB = MING_HOME / "ming.db"
HOT = MING_HOME / "hot"


def _mb(val):
    return round(val / (1024 * 1024), 2)


def get_health():
    """获取乾坤镜系统健康报告。

    零依赖实现 —— 只读 ~/.ming/ 下的文件，不调用 ming CLI 也不 import mingjing 包。
    返回值结构：
    {
        "archiver_lag": {"value": 0.5, "status": "ok", "desc": "...秒"},
        "wal_size": {"value": 0.1, "status": "ok", "desc": "...MB"},
        "hot_dir": {"value": 0, "status": "ok", "desc": "...文件"},
        "disk_free": {"value": 24144, "status": "ok", "desc": "...MB"},
        "lit_lite": {"value": None, "status": "no_files"},
        "mirror": {"value": ..., "status": ...},
        "overall": "ok" | "warn" | "crit" | "no_archiver"
    }
    """
    _archiver_alive = HB.exists()

    def _archiver_lag():
        if not _archiver_alive:
            return None, "no_archiver"
        try:
            lag = time.time() - float(HB.read_text().strip())
            s = "ok" if lag < 5 else ("warn" if lag < 30 else "crit")
            return round(lag, 2), s
        except (ValueError, OSError):
            return None, "error"

    def _wal_size():
        wp = Path(str(DB) + "-wal")
        if not wp.exists():
            return 0.0, "ok", "0 MB"
        try:
            mb = wp.stat().st_size / (1024 * 1024)
            return round(mb, 2), "warn" if mb > 100 else "ok", f"{round(mb, 2)} MB"
        except OSError:
            return None, "error", None

    def _hot_dir_info():
        if not HOT.exists():
            return 0, "ok", "0 文件"
        try:
            n = 0
            total_mb = 0.0
            for f in HOT.iterdir():
                if f.is_file() and f.suffix == ".jsonl":
                    n += 1
                    total_mb += f.stat().st_size / (1024 * 1024)
            s = "ok"
            desc = f"{n} 文件 / {round(total_mb, 2)} MB"
            if n > 200 or total_mb > 50:
                s = "warn"
            return n, s, desc
        except OSError:
            return None, "error", None

    def _disk_free():
        try:
            s = os.statvfs(str(MING_HOME))
            mb = (s.f_bavail * s.f_frsize) / (1024 * 1024)
            return (
                round(mb, 2),
                "crit" if mb < 100 else ("warn" if mb < 500 else "ok"),
                f"{round(mb, 2)} MB",
            )
        except OSError:
            return None, "error", None

    def _lit_lite():
        d = MING_HOME / "plugins" / "lit_lite" / "out"
        if not d.exists():
            return None, "no_output"
        try:
            fs = list(d.glob("*.jsonl"))
            return len(fs), "ok" if fs else "no_files"
        except OSError:
            return None, "error"

    def _mirror():
        pf = MING_HOME / ".otel_bridge.pid"
        pid = None
        if pf.exists():
            try:
                pid = int(pf.read_text().strip())
            except (ValueError, OSError):
                pass
        port_ok = False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            port_ok = s.connect_ex(("127.0.0.1", 4319)) == 0
            s.close()
        except Exception:
            pass
        if port_ok and pid:
            try:
                os.kill(pid, 0)
                return "running", "ok"
            except OSError:
                return "pid_dead", "warn"
        return "stopped", "warn" if _archiver_alive else "ok"

    al, als = _archiver_lag()
    if als == "no_archiver":
        return {
            "overall": "no_archiver",
            "message": "乾坤镜归档器未运行。pip install mingjing && ming start 启动后再试。",
        }

    wm, wms, wd = _wal_size()
    hn, hns, hd = _hot_dir_info()
    df, dfs, dd = _disk_free()
    ll, lls = _lit_lite()
    mr, mrs = _mirror()

    statuses = [als, wms, hns, dfs, lls, mrs]
    non_optional = [
        s
        for name, s in [
            ("archiver_lag", als),
            ("wal_size", wms),
            ("hot_dir_pileup", hns),
            ("disk_free", dfs),
        ]
    ]
    overall = (
        "crit" if "crit" in statuses else ("warn" if "warn" in non_optional else "ok")
    )

    return {
        "archiver_lag": {
            "value": al,
            "status": als,
            "desc": f"{al} 秒" if al is not None else "N/A",
            "threshold": "<5s",
        },
        "wal_size": {"value": wm, "status": wms, "desc": wd, "threshold": "<100MB"},
        "hot_dir_pileup": {
            "value": hn,
            "status": hns,
            "desc": hd,
            "threshold": "<200文件 / <50MB",
        },
        "disk_free": {"value": df, "status": dfs, "desc": dd, "threshold": ">500MB"},
        "lit_lite": {
            "value": ll,
            "status": lls,
            "desc": f"{ll} 文件" if ll is not None else "N/A",
        },
        "mirror": {"value": mr, "status": mrs, "desc": f"OTEL Bridge {mr}"},
        "overall": overall,
    }


_LABELS = {
    "archiver_lag": "归档器延迟",
    "wal_size": "WAL 大小",
    "hot_dir_pileup": "热轨堆积",
    "disk_free": "磁盘剩余",
    "lit_lite": "诊断引擎",
    "mirror": "OTEL Bridge",
}

_CHECK_ORDER = [
    "archiver_lag",
    "wal_size",
    "hot_dir_pileup",
    "disk_free",
    "lit_lite",
    "mirror",
]

_ICONS = {
    "ok": "[OK]",
    "warn": "[!!]",
    "crit": "[XX]",
    "no_output": " [～]",
    "no_files": " [～]",
    "error": "[??]",
    "pid_dead": "[!!]",
}

_SUGGESTIONS = {
    "archiver_lag": "检查归档器是否正常运行（systemctl status ming-archiver）",
    "wal_size": "WAL 过大，建议执行 VACUUM：ming admin vacuum",
    "hot_dir_pileup": "热轨文件堆积过多，检查归档器消费是否卡死",
    "disk_free": "磁盘空间不足，清理旧数据或扩盘",
    "lit_lite": "检查诊断引擎是否卡死",
    "mirror": "检查 OTEL Bridge 是否启动（未监听端口 4319）",
}


def _fmt_value(name, val, status):
    if val is None:
        return "N/A"
    if name == "archiver_lag":
        return f"{val:.1f}s"
    if name == "wal_size":
        return f"{val:.0f}MB"
    if name == "disk_free":
        return f"{val:.0f}MB"
    if name == "hot_dir_pileup":
        return f"{val} 文件"
    if name == "lit_lite":
        return f"{val} 文件" if isinstance(val, int) else str(val)
    if name == "mirror":
        if status == "pid_dead":
            return "pid 残留，端口无响应"
        return {"running": "运行中", "stopped": "未启动"}.get(str(val), str(val))
    return str(val)


def get_health_text():
    """返回人类可读的健康报告文本，适合 LLM 消费。

    格式参照 ming self-check，工整、中文标签、带建议动作。
    """
    r = get_health()
    if r.get("overall") == "no_archiver":
        return "⚠ 乾坤镜归档器未运行。先执行 pip install mingjing && ming start 启动。"

    lines = []
    lines.append("乾坤镜自健康检查")
    lines.append("=" * 36)

    suggestions = []
    for k in _CHECK_ORDER:
        v = r[k]
        icon = _ICONS.get(v["status"], "[??]")
        label = _LABELS.get(k, k)
        val = _fmt_value(k, v.get("value"), v["status"])
        thr = v.get("threshold", "")
        thr_str = f"（阈值 {thr}）" if thr else ""
        lines.append(f"  {icon} {label}：{val}{thr_str}")

        if v["status"] in ("warn", "crit") and k in _SUGGESTIONS:
            suggestions.append(_SUGGESTIONS[k])

    lines.append("")
    lines.append(f"总体状态: {r['overall']}")

    if suggestions:
        lines.append("")
        lines.append("建议动作：")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"  {i}. {s}")

    return "\n".join(lines)


try:
    from langchain_core.tools import tool as _tool

    @_tool
    def get_ming_health() -> str:
        """获取乾坤镜（Mingjing）系统健康报告。

        乾坤镜是 AI Agent 的诊断观测系统。
        此工具返回 6 项核心指标：归档器延迟、WAL 文件大小、热轨堆积、磁盘剩余空间、诊断引擎状态、Mirror 桥接状态。
        当你怀疑系统运行异常、或需要排查探针数据是否被正常消费时使用。
        返回人类可读的文本报告。
        """
        return get_health_text()

    MingHealthTool = get_ming_health

except ImportError:
    MingHealthTool = None
