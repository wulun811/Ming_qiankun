# probe_platform.py —— v0.11.9m 独立系统探针
# 职责：采集宿主机指标（内存/FD/线程/IO），emit 到热轨
# 独立进程：不 import probe_uni，观测者与被观测者物理隔离
# 依赖：纯标准库
# 启动：python src/probe_platform.py [--interval 30] [--pid PID]

import os, json, time, sys, platform, shutil
from pathlib import Path

HOT_DIR = Path(os.getenv("MING_HOT_DIR", str(Path.home() / ".ming" / "hot")))
SYSTEM = "__host__"
SCHEMA_VERSION = "0.11.9m"
_prev_cpu = {}


def _detect_platform() -> str:
    return {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}.get(
        platform.system(), "unknown"
    )


def _detect_env() -> dict:
    profile = {
        "os": _detect_platform(),
        "kernel": "",
        "hostname": "",
        "cpu_model": "",
        "cpu_count": os.cpu_count() or 0,
        "mem_total_kb": 0,
        "runtime": "baremetal",
        "container_type": "",
        "vm_type": "",
    }

    try:
        uname = os.uname()
        profile["kernel"] = uname.release
        profile["hostname"] = uname.nodename
    except Exception:
        pass

    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    profile["mem_total_kb"] = int(line.split()[1])
                    break
    except Exception:
        pass

    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("model name") or line.startswith("Model Name"):
                    model = line.split(":", 1)[1].strip()
                    if model:
                        profile["cpu_model"] = model
                        break
    except Exception:
        pass

    cgroup_content = ""
    for cgroup_path in ("/proc/1/cgroup", "/proc/self/cgroup"):
        try:
            with open(cgroup_path, "r") as f:
                cgroup_content = f.read()
                if cgroup_content:
                    break
        except Exception:
            continue

    if "/docker/" in cgroup_content or "docker-" in cgroup_content:
        profile["runtime"] = "container"
        profile["container_type"] = "docker"
    elif "/kubepods/" in cgroup_content:
        profile["runtime"] = "container"
        profile["container_type"] = "k8s"
    elif "/lxc/" in cgroup_content or "/lxc.monitor/" in cgroup_content:
        profile["runtime"] = "container"
        profile["container_type"] = "lxc"
    elif "libpod" in cgroup_content:
        profile["runtime"] = "container"
        profile["container_type"] = "podman"

    if profile["runtime"] == "baremetal":
        dmi_path = "/sys/class/dmi/id/product_name"
        try:
            with open(dmi_path, "r") as f:
                product = f.read().strip().lower()
        except Exception:
            product = ""

        vm_keywords = {
            "kvm": "kvm",
            "vmware": "vmware",
            "virtualbox": "virtualbox",
            "hvm": "xen",
            "xen": "xen",
            "hyper-v": "hyperv",
            "virtual machine": "hyperv",
            "standard pc (i440fx": "qemu",
            "standard pc (q35": "qemu",
        }
        for kw, vtype in vm_keywords.items():
            if kw in product:
                profile["runtime"] = "vm"
                profile["vm_type"] = vtype
                break

        if profile["runtime"] == "baremetal":
            try:
                with open("/proc/cpuinfo", "r") as f:
                    if "hypervisor" in f.read().lower():
                        profile["runtime"] = "vm"
                        profile["vm_type"] = "unknown"
            except Exception:
                pass

    return profile


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _read_loadavg() -> tuple:
    try:
        with open("/proc/loadavg", "r") as f:
            parts = f.read().strip().split()
            return (float(parts[0]), float(parts[1]), float(parts[2]))
    except Exception:
        return (0.0, 0.0, 0.0)


def _collect_linux(pid: int) -> dict:
    proc_path = f"/proc/{pid}"
    try:
        vm_rss_kb = vm_size_kb = threads = 0
        with open(f"{proc_path}/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    vm_rss_kb = int(line.split()[1])
                elif line.startswith("VmSize:"):
                    vm_size_kb = int(line.split()[1])
                elif line.startswith("Threads:"):
                    threads = int(line.split()[1])
        fd_count = 0
        try:
            fd_count = len(os.listdir(f"{proc_path}/fd"))
        except (PermissionError, FileNotFoundError):
            pass
        io_read_bytes = io_write_bytes = 0
        try:
            with open(f"{proc_path}/io", "r") as f:
                for line in f:
                    if line.startswith("read_bytes:"):
                        io_read_bytes = int(line.split()[1])
                    elif line.startswith("write_bytes:"):
                        io_write_bytes = int(line.split()[1])
        except (PermissionError, FileNotFoundError):
            pass
        l1, l5, l15 = _read_loadavg()

        cpu_percent = 0.0
        try:
            with open(f"{proc_path}/stat", "r") as f:
                raw = f.read()
            end = raw.rfind(")")
            if end > 0:
                fields = raw[end + 2 :].split()
                if len(fields) >= 2:
                    utime = int(fields[11]) if len(fields) > 11 else 0
                    stime = int(fields[12]) if len(fields) > 12 else 0
                    now = time.time()
                    prev = _prev_cpu.get(pid)
                    if prev:
                        pu, ps, pt = prev
                        dt = now - pt
                        if dt > 0:
                            cpu_percent = round((utime + stime - pu - ps) / dt, 1)
                    _prev_cpu[pid] = (utime, stime, now)
        except Exception:
            pass
        proc_name = ""
        try:
            with open(f"{proc_path}/comm", "r") as f:
                proc_name = f.read().strip()
        except (PermissionError, FileNotFoundError):
            pass
        proc_exe = ""
        try:
            proc_exe = os.readlink(f"{proc_path}/exe")
        except (PermissionError, FileNotFoundError, OSError):
            pass
        return {
            "vm_rss_kb": vm_rss_kb,
            "vm_size_kb": vm_size_kb,
            "fd_count": fd_count,
            "threads": threads,
            "io_read_bytes": io_read_bytes,
            "io_write_bytes": io_write_bytes,
            "load_avg_1m": l1,
            "load_avg_5m": l5,
            "load_avg_15m": l15,
            "cpu_count": os.cpu_count() or 0,
            "cpu_percent": cpu_percent,
            "platform": "linux",
            "proc_name": proc_name,
            "proc_exe": proc_exe,
        }
    except FileNotFoundError:
        return {"error": "process_not_found", "pid": pid}
    except PermissionError:
        return {"error": "permission_denied", "pid": pid}


def _collect_windows(pid: int) -> dict:
    try:
        import ctypes

        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        h_process = kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid
        )
        if not h_process:
            return {
                "error": "cannot_open_process",
                "pid": pid,
                "windows_metrics_unreliable": True,
            }
        try:

            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(counters)
            if psapi.GetProcessMemoryInfo(
                h_process, ctypes.byref(counters), counters.cb
            ):
                return {
                    "vm_rss_kb": counters.WorkingSetSize // 1024,
                    "vm_peak_kb": counters.PeakWorkingSetSize // 1024,
                    "pagefile_kb": counters.PagefileUsage // 1024,
                    "fd_count": 0,
                    "threads": 0,
                    "io_read_bytes": 0,
                    "io_write_bytes": 0,
                    "windows_metrics_unreliable": True,
                    "platform": "windows",
                    "proc_name": "",
                }
            return {
                "error": "get_memory_info_failed",
                "windows_metrics_unreliable": True,
            }
        finally:
            kernel32.CloseHandle(h_process)
    except Exception as e:
        return {"error": str(e), "windows_metrics_unreliable": True}


def _collect_macos(pid: int) -> dict:
    import subprocess

    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "rss,vsz,state,nlwp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return {"error": "process_not_found", "pid": pid}
        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            return {"error": "no_output", "pid": pid}
        parts = lines[1].split()
        vm_rss_kb = int(parts[0])
        vm_size_kb = int(parts[1])
        threads = int(parts[3]) if len(parts) > 3 else 0
        fd_count = 0
        try:
            result = subprocess.run(
                ["lsof", "-p", str(pid)], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                fd_count = len(result.stdout.strip().split("\n")) - 1
        except Exception:
            pass
        return {
            "vm_rss_kb": vm_rss_kb,
            "vm_size_kb": vm_size_kb,
            "fd_count": fd_count,
            "threads": threads,
            "platform": "macos",
            "proc_name": "",
        }
    except Exception as e:
        return {"error": str(e)}


def _collect_metrics(pid: int, plat: str) -> dict:
    collectors = {
        "linux": _collect_linux,
        "windows": _collect_windows,
        "macos": _collect_macos,
    }
    fn = collectors.get(plat, lambda p: {"error": "unsupported_platform"})
    return fn(pid)


def _disk_free_mb() -> float:
    try:
        return shutil.disk_usage(str(HOT_DIR)).free / (1024 * 1024)
    except Exception:
        return -1


def _is_paused() -> bool:
    return (Path.home() / ".ming" / ".paused" / SYSTEM).exists()


def emit_snapshot(metrics: dict):
    if _is_paused():
        return
    HOT_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "system": SYSTEM,
        "mode": "black",
        "event_type": "platform_snapshot",
        "payload": metrics,
        "timestamp": time.time(),
        "monotonic_ms": time.monotonic() * 1000,
        "_pid": os.getpid(),
        "_schema_version": SCHEMA_VERSION,
    }
    ts = time.strftime("%Y%m%d_%H%M%S")
    filepath = HOT_DIR / f"{SYSTEM}_{ts}_{os.getpid()}_0001.jsonl"
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[probe_platform] DROP: {e}", file=sys.stderr)


def emit_health():
    if _is_paused():
        return
    HOT_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "system": SYSTEM,
        "mode": "black",
        "event_type": "__health__",
        "payload": {
            "disk_free_mb": _disk_free_mb(),
            "platform": _detect_platform(),
        },
        "timestamp": time.time(),
        "monotonic_ms": time.monotonic() * 1000,
        "_pid": os.getpid(),
        "_schema_version": SCHEMA_VERSION,
    }
    ts = time.strftime("%Y%m%d_%H%M%S")
    filepath = HOT_DIR / f"{SYSTEM}_{ts}_{os.getpid()}_health.jsonl"
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[probe_platform] DROP: {e}", file=sys.stderr)


def emit_env_profile(env_profile: dict):
    if _is_paused():
        return
    HOT_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "system": SYSTEM,
        "mode": "black",
        "event_type": "platform_profile",
        "payload": env_profile,
        "timestamp": time.time(),
        "monotonic_ms": time.monotonic() * 1000,
        "_pid": os.getpid(),
        "_schema_version": SCHEMA_VERSION,
    }
    ts = time.strftime("%Y%m%d_%H%M%S")
    filepath = HOT_DIR / f"{SYSTEM}_{ts}_{os.getpid()}_env.jsonl"
    try:
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[probe_platform] DROP env_profile: {e}", file=sys.stderr)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="乾坤镜独立系统探针")
    parser.add_argument(
        "--pid", type=int, default=os.getppid(), help="目标进程 PID（默认父进程）"
    )
    parser.add_argument(
        "--interval", type=int, default=30, help="采集间隔秒数（默认 30）"
    )
    args = parser.parse_args()

    plat = _detect_platform()
    env_profile = _detect_env()
    print(
        f"[probe_platform] 启动 | pid={args.pid} | platform={plat} | runtime={env_profile.get('runtime', '?')}/{env_profile.get('container_type', '')}{env_profile.get('vm_type', '')} | interval={args.interval}s"
    )
    emit_env_profile(env_profile)

    if not _is_pid_alive(args.pid):
        print(f"[probe_platform] 警告: PID {args.pid} 不存在，使用当前进程")
        args.pid = os.getpid()

    while True:
        try:
            metrics = _collect_metrics(args.pid, plat)
            metrics["target_pid"] = args.pid
            emit_snapshot(metrics)
            emit_health()
            print(
                f"[probe_platform] 快照 | pid={args.pid} | rss={metrics.get('vm_rss_kb', '?')}KB | fds={metrics.get('fd_count', '?')}"
            )
        except Exception as e:
            print(f"[probe_platform] 错误: {e}", file=sys.stderr)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
