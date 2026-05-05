# archiver_triage.py —— 0.11.9m 异步分诊+诊断触发
# 职责：后台线程执行分诊（triage）→ 再触发诊断引擎（lit_lite）
# 安全：不 import sqlite3，不知道 DB 路径，只 import 纯业务模块
import threading
import time


def trigger_triage(triage_running, snapshot_path, interval, log_fn):
    """检查是否需要运行分诊，如需要则后台启动"""
    try:
        if triage_running.is_set():
            return
        if snapshot_path.exists():
            age = time.time() - snapshot_path.stat().st_mtime
            if age < interval:
                return
    except Exception:
        pass

    triage_running.set()
    t = threading.Thread(
        target=lambda: _run_triage_async(triage_running, log_fn, snapshot_path),
        daemon=True,
    )
    t.start()


def _run_triage_async(triage_running, log_fn, snapshot_path):
    """后台线程：分诊 → 诊断"""
    try:
        from triage import run as triage_run

        triage_run(verbose=False)

        from lit_lite import diagnose

        diagnose()
    except Exception as e:
        if log_fn:
            log_fn(e, 0)
    finally:
        triage_running.clear()


def run_diagnosis_async(log_fn=None):
    """后台触发轻量诊断（不阻塞主循环）"""
    try:
        from lit_lite import diagnose

        t = threading.Thread(target=diagnose, daemon=True)
        t.start()
    except Exception:
        if log_fn:
            log_fn("run_diagnosis_async failed")
