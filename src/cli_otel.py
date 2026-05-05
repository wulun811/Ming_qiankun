# cli_otel.py —— v0.11.9 OTLP 管道管理（启动/停止/状态）
# 职责：守护 otel_bridge（收 OTLP 入热轨）+ otel_exporter（发诊断到外部）
# 用法：ming otel start | stop | status

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

MING_HOME = Path(os.path.expanduser(os.getenv("MING_HOME", "~/.ming")))
BRIDGE_PID = MING_HOME / ".otel_bridge.pid"
EXPORT_PID = MING_HOME / ".otel_exporter.pid"
SRC = Path(__file__).parent


def _is_pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _pid_status(pid_file):
    if not pid_file.exists():
        return "stopped", None
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, FileNotFoundError):
        return "stale", None
    if _is_pid_alive(pid):
        return "running", pid
    else:
        return "dead", pid


def _stop(pid_file, name):
    status, pid = _pid_status(pid_file)
    if status in ("stopped", "dead", "stale"):
        print(f"[otel] {name} 未运行")
        if pid_file.exists():
            pid_file.unlink()
        return True
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            time.sleep(0.1)
            if not _is_pid_alive(pid):
                break
        if _is_pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.2)
    except (OSError, ProcessLookupError):
        pass
    print(f"[otel] {name} (PID {pid}) 已停止")
    if pid_file.exists():
        pid_file.unlink()
    return True


def _start(name, pid_file, cmd_args, env=None):
    status, pid = _pid_status(pid_file)
    if status == "running":
        print(f"[otel] {name} 已在运行 (PID {pid})")
        return True
    if pid_file.exists():
        pid_file.unlink()

    MING_HOME.mkdir(parents=True, exist_ok=True)
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if env:
        full_env = os.environ.copy()
        full_env.update(env)
        kwargs["env"] = full_env

    proc = subprocess.Popen(cmd_args, **kwargs)
    pid_file.write_text(str(proc.pid))
    time.sleep(0.5)

    if _is_pid_alive(proc.pid):
        print(f"[otel] {name} 已启动 (PID {proc.pid})")
        return True
    else:
        print(f"[otel] {name} 启动失败（进程已退出）", file=sys.stderr)
        pid_file.unlink()
        return False


def cmd_otel_start(args):
    """启动 OTLP 管道：bridge + exporter"""
    started = []

    # 启动 otel_bridge
    bridge_py = str(SRC / "otel_bridge.py")
    if _start("otel_bridge", BRIDGE_PID, [sys.executable, bridge_py]):
        started.append("bridge")

    # 启动 otel_exporter（循环模式）
    exp_py = str(SRC / "otel_exporter.py")
    exp_interval = os.getenv("MING_OTEL_EXPORT_INTERVAL", "30")
    exp_args = [
        sys.executable,
        exp_py,
        "--since",
        str(time.time() - 300),
        "--interval",
        exp_interval,
    ]
    if _start("otel_exporter", EXPORT_PID, exp_args):
        started.append("exporter")

    if not started:
        print("[otel] 未能启动任何 OTLP 服务", file=sys.stderr)
        return

    print(f"[otel] 已启动: {', '.join(started)}")
    print(f"[otel] Bridge  : http://0.0.0.0:{os.getenv('MING_OTEL_PORT', '4319')}")
    ep = os.getenv("MING_OTEL_EXPORTER_ENDPOINT", "http://localhost:4318/v1/logs")
    print(f"[otel] Exporter: {ep}")


def cmd_otel_stop(args):
    """停止 OTLP 管道"""
    ok = _stop(BRIDGE_PID, "otel_bridge")
    ok &= _stop(EXPORT_PID, "otel_exporter")
    if ok:
        print("[otel] OTLP 管道已全部停止")


def cmd_otel_status(args):
    """查看 OTLP 管道状态"""
    b_status, b_pid = _pid_status(BRIDGE_PID)
    e_status, e_pid = _pid_status(EXPORT_PID)

    status_map = {
        "running": "运行中",
        "stopped": "未启动",
        "dead": "已死亡",
        "stale": "异常",
    }

    print("OTLP 管道状态")
    print("=" * 30)
    b_str = f"  PID {b_pid}" if b_pid else ""
    print(f"otel_bridge  : {status_map.get(b_status, b_status)}{b_str}")
    e_str = f"  PID {e_pid}" if e_pid else ""
    print(f"otel_exporter: {status_map.get(e_status, e_status)}{e_str}")

    if b_status == "running":
        print(f"  端口: {os.getenv('MING_OTEL_PORT', '4319')}")
        print(f"  热轨: {os.getenv('MING_HOT_DIR', str(MING_HOME / 'hot'))}")
    if e_status == "running":
        print(
            f"  端点: {os.getenv('MING_OTEL_EXPORTER_ENDPOINT', 'http://localhost:4318/v1/logs')}"
        )
