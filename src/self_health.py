# self_health.py —— v0.11.9m 自健康检测模块
# 职责：8项指标检测 + 平台兼容文件锁 + 热轨写入 __meta_health__ 事件
# 纯标准库，零第三方依赖
import json, os, platform, socket, sys, time
from pathlib import Path

BASE = Path.home() / ".ming"
HOT = Path(os.getenv("MING_HOT_DIR", str(BASE / "hot")))
DB = Path(os.getenv("MING_DB_PATH", str(BASE / "ming.db")))
HB = BASE / ".archiver_heartbeat"
INTERVAL = int(os.getenv("MING_SELF_HEALTH_INTERVAL", "30"))
OTEL_PORT = int(os.getenv("MING_OTEL_PORT", "4319"))
T = dict(
    arch_lag=5.0,
    wal_mb=100.0,
    disk_mb=500.0,
    lit_lag=10.0,
    export=0.10,
    vac_h=168,
    vac_mb=100.0,
    hot_n=200,
    hot_mb=50.0,
)


def _plat_lock(path):
    """平台兼容文件锁 → (locked, cleanup) — P1-11: 使用 os.open 避免 TOCTOU"""
    s = platform.system()
    fd = None
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
        if s == "Linux":
            import fcntl

            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True, lambda: (fcntl.flock(fd, fcntl.LOCK_UN), os.close(fd))
            except (OSError, IOError):
                os.close(fd)
        elif s == "Windows":
            import msvcrt

            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True, lambda: (
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1),
                    os.close(fd),
                )
            except (OSError, IOError):
                os.close(fd)
    except (OSError, IOError):
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
    print(
        f"[self_health] lock {'not supported' if s not in ('Linux', 'Windows') else 'failed'} on {s}",
        file=sys.stderr,
    )
    return False, lambda: None


def check_archiver_lag():
    if not HB.exists():
        return None, "crit"
    try:
        lag = time.time() - float(HB.read_text().strip())
        return lag, "crit" if lag > 30 else ("warn" if lag > T["arch_lag"] else "ok")
    except (ValueError, OSError):
        return None, "error"


def check_wal_size():
    wp = Path(str(DB) + "-wal")
    if not wp.exists():
        return 0.0, "ok"
    try:
        mb = wp.stat().st_size / (1024 * 1024)
        return mb, "warn" if mb > T["wal_mb"] else "ok"
    except OSError:
        return None, "error"


def check_vacuum_due():
    wm, _ = check_wal_size()
    if wm is not None and wm > T["vac_mb"]:
        return True, "warn"
    if DB.exists():
        try:
            if (time.time() - DB.stat().st_mtime) / 3600 > T["vac_h"]:
                return True, "warn"
        except OSError:
            pass
    return False, "ok"


def check_otel_bridge():
    port_ok = False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            port_ok = s.connect_ex(("127.0.0.1", OTEL_PORT)) == 0
    except OSError:
        pass
    pid = None
    pf = BASE / ".otel_bridge.pid"
    if pf.exists():
        try:
            pid = int(pf.read_text().strip())
        except (ValueError, OSError):
            pass
    if port_ok and pid:
        try:
            os.kill(pid, 0)
            return {"status": "running", "pid": pid}, "ok"
        except OSError:
            return {"status": "pid_dead", "pid": pid}, "warn"
    return (
        ({"status": "running", "pid": None}, "ok")
        if port_ok
        else ({"status": "stopped", "pid": None}, "warn")
    )


def check_hot_dir():
    if not HOT.exists():
        return 0, 0.0, "ok"
    try:
        n = 0
        total_mb = 0.0
        with os.scandir(str(HOT)) as it:
            for entry in it:
                if entry.is_file() and entry.name.endswith(".jsonl"):
                    try:
                        total_mb += entry.stat().st_size / (1024 * 1024)
                    except OSError:
                        pass
                    n += 1
        return n, total_mb, "warn" if n > T["hot_n"] or total_mb > T["hot_mb"] else "ok"
    except OSError:
        return None, None, "error"


def check_lit_lite_lag():
    d = BASE / "plugins" / "lit_lite" / "out"
    if not d.exists():
        return None, "no_output"
    try:
        fs = list(d.glob("*.jsonl"))
        if not fs:
            return None, "no_files"
        lag = time.time() - max(fs, key=lambda p: p.stat().st_mtime).stat().st_mtime
        return lag, "warn" if lag > T["lit_lag"] else "ok"
    except OSError:
        return None, "error"


def check_otel_export_fail_rate():
    sf = BASE / ".otel_exporter_state.json"
    if not sf.exists():
        return None, "no_state"
    try:
        st = json.loads(sf.read_text())
        t = st.get("total_attempts", 0)
        f = st.get("failed_attempts", 0)
        if t == 0:
            return 0.0, "ok"
        r = f / t
        return r, "warn" if r > T["export"] else "ok"
    except (json.JSONDecodeError, OSError, KeyError):
        return None, "error"


def check_disk_free():
    try:
        st = os.statvfs(str(BASE))
        mb = (st.f_bavail * st.f_frsize) / (1024 * 1024)
        return mb, "crit" if mb < 100 else ("warn" if mb < T["disk_mb"] else "ok")
    except OSError:
        return None, "error"


def emit_health(hot_dir=None):
    """组装 __meta_health__ 事件写入热轨"""
    tgt = Path(hot_dir) if hot_dir else HOT
    tgt.mkdir(parents=True, exist_ok=True)
    al, als = check_archiver_lag()
    wm, wms = check_wal_size()
    vd, vds = check_vacuum_due()
    hn, hmb, hds = check_hot_dir()
    df, dfs = check_disk_free()
    ll, lls = check_lit_lite_lag()
    er, ers = check_otel_export_fail_rate()
    ob, obs = check_otel_bridge()
    hb_val = None
    try:
        if HB.exists():
            hb_val = float(HB.read_text().strip())
    except (ValueError, OSError):
        pass
    payload = {
        "archiver_lag_seconds": al,
        "wal_size_mb": wm,
        "vacuum_due": vd,
        "otel_bridge_queue_size": ob.get("status"),
        "hot_dir_files": hn,
        "hot_dir_size_mb": hmb,
        "disk_free_mb": df,
        "last_archiver_heartbeat": hb_val,
        "lit_lite_lag_seconds": ll,
        "otel_export_fail_rate": er,
        "_statuses": {
            "archiver_lag": als,
            "wal_size": wms,
            "vacuum_due": vds,
            "hot_dir": hds,
            "disk_free": dfs,
            "lit_lite": lls,
            "otel_export": ers,
            "otel_bridge": obs,
        },
    }
    event = {
        "event_type": "__meta_health__",
        "system": "__self__",
        "payload": payload,
        "timestamp": time.time(),
    }
    _, cleanup = _plat_lock(str(tgt / ".self_health.lock"))
    try:
        fp = (
            tgt
            / f"__self_health___{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.jsonl"
        )
        with open(fp, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[self_health] write failed: {e}", file=sys.stderr)
    finally:
        cleanup()
    return payload


def _check_hot_dir_tuple():
    """Wrapper to return (n, status) tuple for run_check"""
    n, mb, status = check_hot_dir()
    return n, status


def run_check():
    """执行一次完整自健康检查 → (results_dict, overall_status)"""
    checks = [
        ("archiver_lag", check_archiver_lag, "<5s"),
        ("wal_size", check_wal_size, "<100MB"),
        ("vacuum_due", check_vacuum_due, "not due"),
        ("hot_dir", _check_hot_dir_tuple, "<200/<50MB"),
        ("disk_free", check_disk_free, ">500MB"),
        ("otel_bridge", check_otel_bridge, "running"),
        ("lit_lite", check_lit_lite_lag, "<10s"),
        ("otel_export", check_otel_export_fail_rate, "<10%"),
    ]
    results = {}
    warn = crit = False
    for name, fn, label in checks:
        try:
            val, status = fn()
            results[name] = {"value": val, "status": status, "threshold": label}
            if status == "crit":
                crit = True
            elif status == "warn":
                warn = True
        except Exception as e:
            results[name] = {"value": None, "status": "error", "error": str(e)}
            warn = True
    return results, "crit" if crit else ("warn" if warn else "ok")
