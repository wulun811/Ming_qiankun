#!/usr/bin/env python3
"""乾坤镜压力测试：1000 事件/秒，持续 120 秒，采集内存/CPU/吞吐曲线。

用法:
    python3 tests/stress_1000eps.py

输出:
    results/stress_1000eps_YYYYMMDD-HHMM.csv  — 逐秒采样
    results/stress_1000eps_YYYYMMDD-HHMM.json — 摘要统计
"""

import os, sys, time, json, csv, random, hashlib, shutil, subprocess, threading, signal
from pathlib import Path
from datetime import datetime
from collections import Counter

# ── 路径 ──────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / "src"
HOT_DIR = Path.home() / ".ming" / "hot"
DB_PATH = Path.home() / ".ming" / "ming.db"
RESULTS_DIR = PROJECT_ROOT / "results"

sys.path.insert(0, str(SRC))

# ── 参数 ──────────────────────────────────────────────
EPS = 1000  # 每秒事件数
DURATION = 120  # 发射持续秒数
PAD = 2  # 头尾缓冲秒数
SYSTEM_NAME = "stress-test"
BATCH_SIZE = 50  # 每热轨文件事件数
FLUSH_INTERVAL_MS = 500  # 探针 flush 间隔 ms
DRAIN_TIMEOUT = 300  # 排空等待上限秒
TS = datetime.now().strftime("%Y%m%d-%H%M")

# ── 事件类型混合 ───────────────────────────────────────
EVENT_TYPES = [
    ("platform_snapshot", 0.15, 250),
    ("__health__", 0.10, 100),
    ("tool_call", 0.20, 300),
    ("llm_invoke", 0.15, 300),
    ("agent_step", 0.15, 150),
    ("llm_output", 0.10, 300),
    ("session_event", 0.05, 150),
    ("error", 0.05, 100),
    ("agent_step_start", 0.03, 100),
    ("agent_step_finish", 0.02, 100),
]
# 权重归一化
_total_weight = sum(w for _, w, _ in EVENT_TYPES)
TYPE_WEIGHTS = [(t[0], t[1] / _total_weight, t[2]) for t in EVENT_TYPES]

EVENT_POOL = {"run_id": hashlib.md5(str(time.time()).encode()).hexdigest()[:12]}
_stress_pid = os.getpid()


def _choose_type():
    r = random.random()
    acc = 0.0
    for name, w, _ in TYPE_WEIGHTS:
        acc += w
        if r <= acc:
            return name
    return TYPE_WEIGHTS[-1][0]


def _gen_payload(etype):
    ts = time.time()
    base = {"system": SYSTEM_NAME, "timestamp": ts}

    if etype == "platform_snapshot":
        base["vm_rss_kb"] = random.randint(300000, 800000)
        base["vm_size_kb"] = random.randint(8000000, 25000000)
        base["fd_count"] = random.randint(20, 80)
        base["threads"] = random.randint(20, 60)
        base["pid"] = _stress_pid

    elif etype == "__health__":
        base["emit_success_count_1m"] = random.randint(0, 100)
        base["mem_free_mb"] = random.randint(5000, 15000)
        base["_source"] = "stress-test-probe"
        base["_incomplete"] = 0

    elif etype == "tool_call":
        base["layer_tool"] = {
            "tool_name": random.choice(
                ["exec", "read", "bash", "write", "edit", "grep"]
            ),
            "tool_status": random.choice(["success", "success", "success", "fail"]),
            "tool_input_hash": hashlib.md5(str(random.random()).encode()).hexdigest()[
                :12
            ],
            "execution_ms": random.randint(10, 5000),
        }

    elif etype == "llm_invoke":
        base["layer_llm"] = {
            "model": "test-model-v1",
            "input_tokens": random.randint(100, 50000),
            "output_tokens": random.randint(50, 5000),
            "latency_ms": random.randint(100, 50000),
            "finish_reason": random.choice(["stop", "stop", "stop", "length"]),
        }

    elif etype == "agent_step":
        step = random.randint(1, 200)
        base["layer_agent"] = {
            "step_id": f"{EVENT_POOL['run_id']}:{step}",
            "session_id": "test-session",
            "decision_summary": "test decision",
            "role": "agent",
        }

    elif etype == "llm_output":
        base["layer_llm"] = {
            "output_text": "test " * random.randint(1, 40),
            "output_text_hash": hashlib.md5(str(random.random()).encode()).hexdigest()[
                :12
            ],
        }

    elif etype == "session_event":
        base["update_type"] = random.choice(["transcript_update", "state_change"])
        base["content_length"] = random.randint(0, 2000)

    elif etype == "error":
        base["error_type"] = random.choice(
            ["tool_failure", "timeout", "connection_error"]
        )
        base["error_msg"] = f"test error #{random.randint(1, 999)}"

    elif etype == "agent_step_start":
        step = random.randint(1, 200)
        base["layer_agent"] = {
            "step_id": f"{EVENT_POOL['run_id']}:{step}",
            "step_status": "start",
            "session_id": "test-session",
        }

    elif etype == "agent_step_finish":
        step = random.randint(1, 200)
        base["layer_agent"] = {
            "step_id": f"{EVENT_POOL['run_id']}:{step}",
            "step_status": "finish",
            "session_id": "test-session",
            "latency_ms": random.randint(100, 30000),
        }

    return base


# ── 探针模拟 ───────────────────────────────────────────
try:
    from probe_uni import ProbeUni

    ProbeUni.BATCH_SIZE = BATCH_SIZE
    ProbeUni.FLUSH_INTERVAL_MS = FLUSH_INTERVAL_MS
    _probe = ProbeUni(SYSTEM_NAME, mode="white", pid=_stress_pid)
except Exception as e:
    print(f"[FATAL] 无法加载 probe_uni: {e}")
    sys.exit(1)


class StressController:
    def __init__(self):
        self.emitted = 0
        self.dropped = 0
        self.running = True
        self.paused = False
        self.archiver_pid = None
        self.samples = []  # list of dicts
        self._lock = threading.Lock()
        self._cpu_prev = None  # for CPU calc

    # ── 发射线程 ────────────────────────────────────────
    def emitter(self):
        events_per_batch = BATCH_SIZE
        batches_per_sec = EPS / events_per_batch  # 20
        interval = 1.0 / batches_per_sec  # 0.05s

        total_to_emit = EPS * DURATION
        batches = total_to_emit // events_per_batch

        for b in range(batches):
            if not self.running:
                break
            start = time.time()
            for _ in range(events_per_batch):
                etype = _choose_type()
                payload = _gen_payload(etype)
                try:
                    _probe.emit(etype, payload)
                except Exception:
                    pass  # 静默丢弃
            with self._lock:
                self.emitted += events_per_batch
            elapsed = time.time() - start
            sleep_for = interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    # ── 采样 ──────────────────────────────────────────
    def _read_rss_cpu(self):
        if not self.archiver_pid:
            return -1, -1.0
        try:
            with open(f"/proc/{self.archiver_pid}/status") as f:
                rss = 0
                for line in f:
                    if line.startswith("VmRSS:"):
                        rss = int(line.split()[1]) / 1024  # KB → MB
                        break
        except (OSError, ValueError, IndexError):
            rss = -1

        try:
            with open(f"/proc/{self.archiver_pid}/stat") as f:
                fields = f.read().split()
                utime = int(fields[13])
                stime = int(fields[14])
                total = utime + stime
        except (OSError, ValueError, IndexError):
            return rss, -1.0

        cpu = 0.0
        if self._cpu_prev:
            prev_total, prev_time = self._cpu_prev
            wall_delta = time.time() - prev_time
            if wall_delta > 0:
                cpu = (
                    (total - prev_total)
                    / (wall_delta * os.sysconf(os.sysconf_names["SC_CLK_TCK"]))
                    * 100
                )
        self._cpu_prev = (total, time.time())

        return rss, cpu

    def _read_db_size(self):
        try:
            return DB_PATH.stat().st_size / 1024 / 1024
        except OSError:
            return -1.0

    def _count_hot_files(self):
        try:
            return len(list(HOT_DIR.glob("mingjing-*.jsonl")))
        except Exception:
            return -1

    def _count_ingested(self):
        try:
            import sqlite3

            conn = sqlite3.connect(str(DB_PATH))
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM events WHERE system=?",
                (SYSTEM_NAME,),
            )
            cnt = cur.fetchone()[0]
            conn.close()
            return cnt
        except Exception:
            return -1

    def _pending(self):
        return self.emitted - self._count_ingested()

    def take_sample(self, phase):
        rss, cpu = self._read_rss_cpu()
        db_size = self._read_db_size()
        hot = self._count_hot_files()
        ingested = self._count_ingested()
        pending = self.emitted - ingested
        sample = {
            "timestamp_sec": time.time(),
            "phase": phase,
            "rss_mb": round(rss, 2),
            "cpu_pct": round(cpu, 2),
            "db_size_mb": round(db_size, 2),
            "hot_files": hot,
            "emitted": self.emitted,
            "ingested": ingested,
            "pending": pending,
        }
        self.samples.append(sample)
        return sample

    # ── 分诊 ────────────────────────────────────────────
    def run_triage(self):
        start = time.time()
        result = subprocess.run(
            [sys.executable, str(SRC / "cli.py"), "triage", "run"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        elapsed_ms = (time.time() - start) * 1000
        dx_count = 0
        for line in result.stdout.split("\n"):
            if "诊断" in line and "完成" in line:
                pass
        # 查询本次分诊生成的 stress-test 诊断数
        try:
            import sqlite3

            conn = sqlite3.connect(str(DB_PATH))
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM diagnoses_2026 WHERE system=? AND created_at > ?",
                (SYSTEM_NAME, start),
            )
            dx_count = cur.fetchone()[0]
            conn.close()
        except Exception:
            pass
        return elapsed_ms, dx_count


# ── 主流程 ──────────────────────────────────────────────
def main():
    ctrl = StressController()

    # Phase 0: 找 archiver PID + 拍基线
    print("=" * 60)
    print(" 乾坤镜压力测试 — 1000 eps × 120s")
    print("=" * 60)

    try:
        from probe_uni import ProbeUni

        ctrl.archiver_pid = int(
            Path.home().joinpath(".ming", "standalone.pid").read_text().strip()
        )
        print(f"[Phase 0] Archiver PID: {ctrl.archiver_pid}")
    except Exception:
        print("[FATAL] 找不到 archiver PID，请先启动 archiver")
        sys.exit(1)

    try:
        disk_free = shutil.disk_usage(str(HOT_DIR)).free / 1024 / 1024
        print(f"[Phase 0] 磁盘空闲: {disk_free:.0f}MB")
        if disk_free < 2000:
            print("[FATAL] 磁盘空闲不足 2GB，拒绝测试")
            sys.exit(1)
    except Exception as e:
        print(f"[WARN] 无法检查磁盘: {e}")

    pre_events = ctrl._count_ingested()
    pre_db = ctrl._read_db_size()
    pre_rss, pre_cpu = ctrl._read_rss_cpu()
    print(f"[Phase 0] 基线: RSS={pre_rss:.1f}MB DB={pre_db:.1f}MB events={pre_events}")

    # Phase 1: 缓冲前 2 秒
    print(f"[Phase 1] 缓冲前 {PAD}s（静默）")
    for i in range(PAD):
        ctrl.take_sample("baseline")
        time.sleep(1)

    # Phase 2: 发射 120 秒
    print(f"[Phase 2] 发射 {DURATION}s @ {EPS} eps")
    total_expected = EPS * DURATION
    emitter_thread = threading.Thread(target=ctrl.emitter, daemon=True)
    emitter_thread.start()

    phase2_start = time.time()
    for sec in range(DURATION):
        sample = ctrl.take_sample("emitting")
        progress = ctrl.emitted / total_expected * 100 if total_expected else 0
        print(
            f"  [{sec + 1:3d}/{DURATION}s]  "
            f"发射={ctrl.emitted:6d}  入库={sample['ingested']:6d}  "
            f"待处理={sample['pending']:6d}  "
            f"热轨={sample['hot_files']:4d}  "
            f"RSS={sample['rss_mb']:5.1f}MB  "
            f"CPU={sample['cpu_pct']:5.1f}%",
            end="\r" if sec < DURATION - 1 else "\n",
        )
        time.sleep(1)
    ctrl.running = False
    emitter_thread.join(timeout=5)

    # Phase 3: 缓冲后 2 秒
    print(f"[Phase 3] 缓冲后 {PAD}s（静默）")
    for i in range(PAD):
        ctrl.take_sample("cooldown")
        time.sleep(1)
    print(f"[Phase 3] 发射结束: {ctrl.emitted} 条事件已发射")

    # Phase 4: 排空等待
    print(f"[Phase 4] 等待热轨排空（最长 {DRAIN_TIMEOUT}s）...")
    drain_start = time.time()
    while True:
        hot = ctrl._count_hot_files()
        ingested = ctrl._count_ingested()
        pending = ctrl.emitted - ingested
        sample = ctrl.take_sample("drain")
        elapsed = time.time() - drain_start
        print(
            f"  [排空 {elapsed:5.1f}s]  热轨={hot:4d}  已入库={ingested:6d}/{ctrl.emitted}  待处理={pending}",
            end="\r",
        )
        if hot == 0 and pending <= 0:
            print()
            print(f"[Phase 4] 排空完成，耗时 {elapsed:.1f}s")
            break
        if elapsed > DRAIN_TIMEOUT:
            print()
            print(f"[WARN] 排空超时 ({DRAIN_TIMEOUT}s)，残留 {hot} 文件 {pending} 事件")
            break
        time.sleep(2)

    # Phase 5: 最终分诊
    print("[Phase 5] 触发最终分诊...")
    triage_ms, dx_count = ctrl.run_triage()
    print(f"[Phase 5] 分诊耗时: {triage_ms:.0f}ms, 生成诊断: {dx_count}")
    ctrl.samples[-1]["triage_duration_ms"] = round(triage_ms)

    # Phase 6: 输出结果
    final_ingested = ctrl._count_ingested()
    loss_pct = (1 - final_ingested / ctrl.emitted * 100) if ctrl.emitted else 0
    rss_vals = [s["rss_mb"] for s in ctrl.samples if s["rss_mb"] > 0]

    csv_path = RESULTS_DIR / f"stress_1000eps_{TS}.csv"
    json_path = RESULTS_DIR / f"stress_1000eps_{TS}.json"

    # CSV
    fields = [
        "timestamp_sec",
        "phase",
        "rss_mb",
        "cpu_pct",
        "db_size_mb",
        "hot_files",
        "emitted",
        "ingested",
        "pending",
        "triage_duration_ms",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(ctrl.samples)
    print(f"[Phase 6] CSV 已写入: {csv_path}")

    # JSON 摘要
    summary = {
        "test_ts": TS,
        "eps": EPS,
        "duration_s": DURATION,
        "total_emitted": ctrl.emitted,
        "total_ingested": final_ingested,
        "loss_pct": round(loss_pct, 2),
        "rss_mb": {
            "baseline": round(pre_rss, 1),
            "peak": round(max(rss_vals), 1),
            "mean": round(sum(rss_vals) / len(rss_vals), 1),
            "final": round(rss_vals[-1], 1),
        },
        "cpu_pct": {
            "peak": round(
                max(s["cpu_pct"] for s in ctrl.samples if s["cpu_pct"] >= 0), 1
            ),
            "mean": round(
                sum(s["cpu_pct"] for s in ctrl.samples if s["cpu_pct"] >= 0)
                / max(1, len([s for s in ctrl.samples if s["cpu_pct"] >= 0])),
                1,
            ),
        },
        "db_size_mb": {
            "pre": round(pre_db, 1),
            "post": round(ctrl._read_db_size(), 1),
            "growth": round(ctrl._read_db_size() - pre_db, 1),
        },
        "drain_time_s": round(time.time() - drain_start, 1),
        "triage_duration_ms": round(triage_ms),
        "diagnoses_generated": dx_count,
        "samples": len(ctrl.samples),
    }
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"[Phase 6] JSON 已写入: {json_path}")

    # 打印摘要
    print("\n" + "=" * 60)
    print(" 压力测试摘要")
    print("=" * 60)
    print(f" 发射总量:     {ctrl.emitted:>8} 条")
    print(f" 实际入库:     {final_ingested:>8} 条")
    print(f" 丢事件率:     {loss_pct:>7.1f}%")
    print(f" RSS 基线:     {pre_rss:>7.1f} MB")
    print(f" RSS 峰值:     {summary['rss_mb']['peak']:>7.1f} MB")
    print(f" RSS 终值:     {summary['rss_mb']['final']:>7.1f} MB")
    print(f" CPU 峰值:     {summary['cpu_pct']['peak']:>7.1f}%")
    print(f" DB 增长:      {summary['db_size_mb']['growth']:>7.1f} MB")
    print(f" 排空耗时:     {summary['drain_time_s']:>7.1f} s")
    print(f" 分诊耗时:     {triage_ms:>7.0f} ms")
    print(f" 生成诊断:     {dx_count:>8} 条")

    # Phase 7: 清理
    print(f"\n[Phase 7] 清理 stress-test 数据...")
    try:
        import sqlite3

        conn = sqlite3.connect(str(DB_PATH))
        cur = conn.cursor()
        cur.execute("DELETE FROM events WHERE system=?", (SYSTEM_NAME,))
        events_del = cur.rowcount
        cur.execute("DELETE FROM diagnoses_2026 WHERE system=?", (SYSTEM_NAME,))
        dx_del = cur.rowcount
        conn.commit()
        # 回收空间
        cur.execute("PRAGMA incremental_vacuum(0)")  # auto_vacuum mode
        conn.execute("VACUUM")
        conn.close()
        print(f"[Phase 7] 已删除 {events_del} 事件, {dx_del} 诊断, VACUUM 完成")

        # 清除残留热轨文件
        for f in HOT_DIR.glob("mingjing-*.jsonl"):
            try:
                f.unlink()
            except OSError:
                pass
        print("[Phase 7] 热轨残留已清理")
    except Exception as e:
        print(f"[WARN] 清理异常: {e}")

    print("\n测试完成。")


if __name__ == "__main__":
    import os as _os

    _prod_path = str(Path.home() / ".ming")
    if _os.path.exists(_prod_path):
        resp = input(
            f"⚠️  生产数据目录 {_prod_path} 已存在，压力测试将写入生产环境！\n    确认继续？(yes/no): "
        )
        if resp.strip().lower() != "yes":
            print("已取消。")
            sys.exit(0)
    main()
