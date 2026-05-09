# archiver_triage.py —— 0.11.9m 异步分诊+诊断触发
# 职责：后台线程执行分诊（triage）→ 再触发诊断引擎（lit_lite）
# 安全：不 import sqlite3，不知道 DB 路径，只 import 纯业务模块
import threading
import time

# ---- 诊断并发控制 ----
# 问题：run_diagnosis_async() 每次创建新线程，高事件频率下线程无限累积
# 修复：threading.Lock 门控（同时最多1个）+ 时间节流（最小间隔5秒）
_diagnosis_lock = threading.Lock()
_last_diagnose_ts = 0.0
_DIAGNOSE_INTERVAL = 5.0


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
    global _last_diagnose_ts
    now = time.time()
    if now - _last_diagnose_ts < _DIAGNOSE_INTERVAL:
        return
    if not _diagnosis_lock.acquire(False):
        return
    _last_diagnose_ts = now
    try:
        from lit_lite import diagnose

        t = threading.Thread(
            target=_run_diagnosis_safe, args=(diagnose, log_fn), daemon=True
        )
        t.start()
    except Exception:
        _diagnosis_lock.release()
        if log_fn:
            log_fn("run_diagnosis_async failed")


def _run_diagnosis_safe(diagnose_fn, log_fn=None):
    """包装诊断函数，确保锁释放 + 异常不逃逸"""
    try:
        diagnose_fn()
    except Exception as e:
        if log_fn:
            log_fn(f"diagnose failed: {e}")
    finally:
        _diagnosis_lock.release()
