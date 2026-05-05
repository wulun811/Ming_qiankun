# test_stress_150k.py —— 乾坤镜 15 万事件压力测试
# 模拟 75s × 2000eps，监控 RSS/CPU/吞吐曲线，验证无内存泄漏

import sys, unittest, json, tempfile, os, time, random, hashlib, sqlite3, csv
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

TEST_SYSTEMS = ["stress-a", "stress-b"]
EVENT_TYPES = [
    "tool_call",
    "llm_invoke",
    "agent_step",
    "error",
    "__health__",
    "llm_output",
    "platform_snapshot",
    "agent_step_start",
    "agent_step_finish",
]
TOOLS = ["bash", "git", "read", "write", "edit", "grep"]
MODELS = ["gpt-4", "gpt-4o", "claude-3", "gemini-pro"]


def _gen_event(system, event_type):
    payload = {}
    if event_type == "tool_call":
        payload["layer_tool"] = {
            "tool_name": random.choice(TOOLS),
            "tool_status": random.choice(["success", "success", "success", "fail"]),
            "execution_ms": random.randint(10, 5000),
        }
    elif event_type == "llm_invoke":
        payload["layer_llm"] = {
            "model": random.choice(MODELS),
            "input_tokens": random.randint(100, 50000),
            "output_tokens": random.randint(50, 5000),
            "latency_ms": random.randint(100, 50000),
        }
    elif event_type in ("agent_step", "agent_step_start", "agent_step_finish"):
        payload["layer_agent"] = {
            "step_id": f"s{random.randint(1, 500)}",
            "session_id": "stress-session",
            "agent_name": system,
        }
    elif event_type == "error":
        payload["error_type"] = random.choice(["tool_failure", "timeout"])
    elif event_type == "platform_snapshot":
        payload["vm_rss_kb"] = random.randint(100000, 500000)
    return {
        "system": system,
        "mode": "white",
        "event_type": event_type,
        "payload": payload,
        "timestamp": time.time(),
        "monotonic_ms": time.monotonic() * 1000,
        "lamport": 1,
    }


def _write_hot(hot_dir, system, events_batch):
    ts = time.strftime("%Y%m%d_%H%M%S")
    fpath = hot_dir / f"{system}_{ts}_{os.getpid()}_stress.jsonl"
    with open(fpath, "a", encoding="utf-8") as f:
        for ev in events_batch:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        f.flush()


class TestStress150K(unittest.TestCase):
    """15 万事件压力测试，监控曲线 + 无泄漏断言"""

    TARGET = 150000
    BATCH_SIZE = 100
    EMIT_RATE = 2000
    TIMEOUT = 180
    RSS_GROWTH_LIMIT = 0.15

    @classmethod
    def setUpClass(cls):
        cls.orig_hot = os.environ.get("MING_HOT_DIR")
        cls.orig_db = os.environ.get("MING_DB_PATH")
        cls.td = tempfile.TemporaryDirectory()
        cls.hot_dir = Path(cls.td.name) / "hot"
        cls.db_path = Path(cls.td.name) / "ming.db"
        os.environ["MING_HOT_DIR"] = str(cls.hot_dir)
        os.environ["MING_DB_PATH"] = str(cls.db_path)
        cls.hot_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        if cls.orig_hot:
            os.environ["MING_HOT_DIR"] = cls.orig_hot
        else:
            os.environ.pop("MING_HOT_DIR", None)
        if cls.orig_db:
            os.environ["MING_DB_PATH"] = cls.orig_db
        else:
            os.environ.pop("MING_DB_PATH", None)
        cls.td.cleanup()

    def _init_db(self):
        from archiver_schema import init_schema, init_diagnoses_table

        conn = sqlite3.connect(str(self.db_path))
        init_schema(conn)
        init_diagnoses_table(conn)
        conn.commit()
        conn.close()

    def _read_rss(self, pid):
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024
        except Exception:
            return -1.0
        return -1.0

    def test_run(self):
        self._init_db()

        from archiver import Archiver

        archiver = Archiver(db_path=str(self.db_path), hot_dir=str(self.hot_dir))
        archiver_pid = None
        try:
            archiver_pid = int(open("/proc/self/status").readline().split()[1])
        except Exception:
            pass

        results_dir = Path(tempfile.mkdtemp(prefix="stress_150k_"))
        csv_path = results_dir / "stress_150k.csv"
        json_path = results_dir / "stress_150k.json"

        samples = []
        emitted = 0
        ingested = 0
        rss_baseline = self._read_rss(os.getpid()) or 20.0
        start_time = time.time()
        emit_deadline = start_time + 75
        sample_idx = 0

        emit_batches = self.TARGET // self.BATCH_SIZE
        batch_interval = 75.0 / emit_batches

        with open(csv_path, "w", newline="") as csvf:
            writer = csv.writer(csvf)
            writer.writerow(
                [
                    "elapsed_s",
                    "emitted",
                    "ingested",
                    "rss_mb",
                    "cpu_pct",
                    "db_mb",
                    "hot_files",
                ]
            )

            for b in range(emit_batches):
                for system in TEST_SYSTEMS:
                    events = [
                        _gen_event(system, random.choice(EVENT_TYPES))
                        for _ in range(self.BATCH_SIZE // 2)
                    ]
                    _write_hot(self.hot_dir, system, events)
                    emitted += len(events)

                ingested += archiver.run_once()

                if b % max(1, emit_batches // 100) == 0 or b == emit_batches - 1:
                    rss = self._read_rss(os.getpid())
                    db_size = (
                        self.db_path.stat().st_size / (1024 * 1024)
                        if self.db_path.exists()
                        else 0
                    )
                    hot_count = len(list(self.hot_dir.glob("*.jsonl")))
                    elapsed = time.time() - start_time
                    writer.writerow(
                        [
                            round(elapsed, 2),
                            emitted,
                            ingested,
                            round(rss, 1),
                            0,
                            round(db_size, 2),
                            hot_count,
                        ]
                    )
                    samples.append(
                        {
                            "elapsed_s": round(elapsed, 2),
                            "emitted": emitted,
                            "ingested": ingested,
                            "rss_mb": round(rss, 1),
                            "db_mb": round(db_size, 2),
                            "hot_files": hot_count,
                        }
                    )
                    sample_idx += 1

                if b < emit_batches - 1:
                    time.sleep(max(0, batch_interval - 0.02))

            ingest_deadline = time.time() + 30
            while time.time() < ingest_deadline:
                ingested += archiver.run_once()
                if ingested >= emitted:
                    break
                time.sleep(0.1)

            rss = self._read_rss(os.getpid())
            elapsed = time.time() - start_time

        with open(json_path, "w") as f:
            json.dump(
                {
                    "total_emitted": emitted,
                    "total_ingested": ingested,
                    "elapsed_s": round(elapsed, 2),
                    "throughput_eps": round(emitted / elapsed, 0),
                    "rss_baseline_mb": round(rss_baseline, 1),
                    "rss_final_mb": round(rss, 1),
                    "rss_growth_mb": round(rss - rss_baseline, 1),
                    "rss_growth_pct": round(
                        (rss - rss_baseline) / rss_baseline * 100, 1
                    )
                    if rss_baseline > 0
                    else 0,
                    "db_size_mb": round(self.db_path.stat().st_size / (1024 * 1024), 2),
                    "samples": samples,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

        print(f"\n[STRESS-150K] 结果已写入: {results_dir}")
        print(
            f"  emitted={emitted} ingested={ingested} elapsed={elapsed:.1f}s throughput={emitted / elapsed:.0f}eps"
        )
        print(
            f"  RSS: {rss_baseline:.1f} → {rss:.1f} MB (+{(rss - rss_baseline):.1f}MB / {((rss - rss_baseline) / rss_baseline * 100):.1f}%)"
        )

        # ── 断言 ──
        self.assertEqual(ingested, emitted, f"归档不完整: {ingested}/{emitted}")

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            db_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(
                db_count, emitted, f"数据库事件数不匹配: {db_count}/{emitted}"
            )

            for system in TEST_SYSTEMS:
                cnt = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE system=?", (system,)
                ).fetchone()[0]
                self.assertGreater(cnt, 0, f"系统 {system} 无事件")

            dx_table = f"diagnoses_{time.strftime('%Y')}"
            diag_count = conn.execute(f"SELECT COUNT(*) FROM {dx_table}").fetchone()[0]
            print(f"  diagnoses={diag_count}")
        finally:
            conn.close()

        rss_growth_pct = (
            (rss - rss_baseline) / rss_baseline * 100 if rss_baseline > 0 else 0
        )
        self.assertLess(
            rss_growth_pct,
            self.RSS_GROWTH_LIMIT * 100,
            f"RSS 增长 {rss_growth_pct:.1f}% 超过阈值 {self.RSS_GROWTH_LIMIT * 100}%",
        )

        print(f"\n  ✅ 全部通过: 归档率 100%, RSS 增长 {rss_growth_pct:.1f}% < 15%")


if __name__ == "__main__":
    unittest.main()
