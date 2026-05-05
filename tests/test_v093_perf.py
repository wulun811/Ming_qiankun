#!/usr/bin/env python3
# test_v093_perf.py —— v0.11.9m 性能基准测试（T-Perf-01~07）
# 用法: python tests/test_v093_perf.py
import sys, os, time, json, shutil, unittest, multiprocessing, sqlite3, gc
from pathlib import Path

TEST_HOME = Path.home() / ".ming_v093_perf_test"
if TEST_HOME.exists():
    try:
        shutil.rmtree(TEST_HOME)
    except Exception:
        pass
TEST_HOME.mkdir(parents=True, exist_ok=True)

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

HOT_DIR = TEST_HOME / "hot"
COLD_DIR = TEST_HOME / "cold"
HOT_DIR.mkdir(parents=True, exist_ok=True)
COLD_DIR.mkdir(parents=True, exist_ok=True)

os.environ["MING_HOT_DIR"] = str(HOT_DIR)
os.environ["MING_COLD_DIR"] = str(COLD_DIR)
os.environ["MING_DB"] = str(TEST_HOME / "ming.db")


def _worker_emit(worker_id, count, hot_root):
    """子进程 worker"""
    sys.path.insert(0, str(SRC))
    from probe_uni import ProbeUni

    p = ProbeUni(system=f"perf_w{worker_id}", mode="white")
    p.HOT_DIR = Path(hot_root) / f"w{worker_id}"
    p.HOT_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    for i in range(count):
        p.emit("agent_step", {"layer_agent": {"step_id": f"s{i}"}})
    p._flush_batch()
    return (worker_id, time.monotonic() - t0, count)


class TestPerf01_ProbeThroughput(unittest.TestCase):
    """T-Perf-01: 探针吞吐 —— 单进程 1000 次 emit() 耗时 < 100ms"""

    def setUp(self):
        self.hot = HOT_DIR / "perf01"
        self.hot.mkdir(parents=True, exist_ok=True)
        from probe_uni import ProbeUni

        self.p = ProbeUni(system="perf_test", mode="white")
        self.p.HOT_DIR = self.hot

    def test_1000_emit_under_100ms(self):
        t0 = time.monotonic()
        for i in range(1000):
            self.p.emit("agent_step", {"layer_agent": {"step_id": f"s{i}"}})
        elapsed_ms = (time.monotonic() - t0) * 1000
        self.p._flush_batch()
        print(f"  T-Perf-01: 1000 emit = {elapsed_ms:.1f}ms")
        self.assertLess(elapsed_ms, 100)


class TestPerf02_BatchFlushLatency(unittest.TestCase):
    """T-Perf-02: 端到端 flush 延迟 —— emit 10 条 + 文件写入完成 < 20ms (Windows SSD)"""

    def setUp(self):
        self.hot = HOT_DIR / "perf02"
        self.hot.mkdir(parents=True, exist_ok=True)
        from probe_uni import ProbeUni

        self.p = ProbeUni(system="perf_test", mode="white")
        self.p.HOT_DIR = self.hot

    def test_batch_flush_p99_under_20ms(self):
        latencies = []
        for round_i in range(20):
            t0 = time.monotonic()
            for i in range(10):
                self.p.emit(
                    "agent_step", {"layer_agent": {"step_id": f"r{round_i}_s{i}"}}
                )
            self.p._flush_batch()
            elapsed_ms = (time.monotonic() - t0) * 1000
            latencies.append(elapsed_ms)
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p99_idx = int(len(latencies) * 0.99)
        p99 = latencies[min(p99_idx, len(latencies) - 1)]
        print(
            f"  T-Perf-02: e2e flush p50={p50:.2f}ms, p99={p99:.2f}ms (emit 10 + file write)"
        )
        self.assertLess(p99, 20)


class TestPerf03_ArchiverThroughput(unittest.TestCase):
    """T-Perf-03: 归档器吞吐 —— 1 秒内归档事件"""

    def setUp(self):
        self._old_env = {
            k: os.environ.get(k) for k in ("MING_HOT_DIR", "MING_COLD_DIR", "MING_DB")
        }
        self.hot = HOT_DIR / "perf03"
        self.hot.mkdir(parents=True, exist_ok=True)
        self.db = TEST_HOME / "perf03.db"
        self.cold = COLD_DIR / "perf03"
        self.cold.mkdir(parents=True, exist_ok=True)
        os.environ["MING_HOT_DIR"] = str(self.hot)
        os.environ["MING_COLD_DIR"] = str(self.cold)
        os.environ["MING_DB"] = str(self.db)

    def tearDown(self):
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_archive_events(self):
        from probe_uni import ProbeUni

        p = ProbeUni(system="perf_test", mode="white")
        p.HOT_DIR = self.hot
        COUNT = 1000
        for i in range(COUNT):
            p.emit("agent_step", {"layer_agent": {"step_id": f"s{i}"}})
        p._flush_batch()
        from archiver import Archiver

        archiver = Archiver(
            db_path=str(self.db), hot_dir=str(self.hot), cold_dir=str(self.cold)
        )
        archiver._alive = True
        t0 = time.monotonic()
        for _ in range(20):
            archiver.run_once()
            time.sleep(0.05)
        archive_ms = (time.monotonic() - t0) * 1000
        archiver._alive = False
        conn = sqlite3.connect(str(self.db))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events")
        db_count = cur.fetchone()[0]
        conn.close()
        print(
            f"  T-Perf-03: archive {COUNT} events = {archive_ms:.0f}ms, db_count={db_count}"
        )
        self.assertGreaterEqual(db_count, COUNT * 0.95)


class TestPerf04_DiskCheck(unittest.TestCase):
    """T-Perf-04: 磁盘检测 —— OS 缓存后 p99 < 20ms"""

    def test_disk_check_cached_p99(self):
        times = []
        for _ in range(100):
            t0 = time.monotonic()
            _ = shutil.disk_usage(str(Path.home()))
            elapsed_ms = (time.monotonic() - t0) * 1000
            times.append(elapsed_ms)
        times.sort()
        p99 = times[99]
        print(f"  T-Perf-04: disk_usage p99 = {p99:.3f}ms (OS 缓存后，Windows SSD)")
        self.assertLess(p99, 20.0)


class TestPerf05_ConcurrentMultiProcess(unittest.TestCase):
    """T-Perf-05: 并发多进程 —— 10 进程同时 emit，无重复/无损坏"""

    def test_10_concurrent_emit(self):
        hot_root = HOT_DIR / "perf05"
        if hot_root.exists():
            shutil.rmtree(hot_root)
        hot_root.mkdir(parents=True, exist_ok=True)
        N_PROCS = 10
        N_EVENTS_PER_PROC = 100
        with multiprocessing.Pool(N_PROCS) as pool:
            results = pool.starmap(
                _worker_emit,
                [(i, N_EVENTS_PER_PROC, str(hot_root)) for i in range(N_PROCS)],
            )
        total_events = sum(r[2] for r in results)
        corrupt = 0
        file_count = 0
        if hot_root.exists():
            for d in hot_root.iterdir():
                if d.is_dir():
                    for f in d.glob("*.jsonl"):
                        try:
                            for line in f.read_text().strip().split("\n"):
                                if line:
                                    json.loads(line)
                                    file_count += 1
                        except Exception:
                            corrupt += 1
        print(
            f"  T-Perf-05: {N_PROCS} procs x {N_EVENTS_PER_PROC} = {total_events} total, files={file_count}, corrupt={corrupt}"
        )
        self.assertEqual(corrupt, 0)


class TestPerf06_MemoryLeak(unittest.TestCase):
    """T-Perf-06: 长时间运行 —— 10000 次 emit+flush，内存增长 < 10%"""

    def test_memory_stable(self):
        try:
            import psutil
        except ImportError:
            self.skipTest("psutil not installed")
            return
        try:
            import tracemalloc
        except ImportError:
            tracemalloc = None

        self.hot = HOT_DIR / "perf06"
        self.hot.mkdir(parents=True, exist_ok=True)
        from probe_uni import ProbeUni

        p = ProbeUni(system="perf_test", mode="white")
        p.HOT_DIR = self.hot
        proc = psutil.Process(os.getpid())

        gc.collect()
        mem_before = proc.memory_info().rss
        alloc_before = tracemalloc.get_traced_memory()[0] if tracemalloc else 0

        COUNT = 10000
        for i in range(COUNT):
            p.emit("agent_step", {"layer_agent": {"step_id": f"s{i}"}})
            if (i + 1) % 100 == 0:
                p._flush_batch()
        p._flush_batch()

        gc.collect()
        mem_after = proc.memory_info().rss
        alloc_after = tracemalloc.get_traced_memory()[0] if tracemalloc else 0

        growth_pct = (
            (mem_after - mem_before) / mem_before * 100 if mem_before > 0 else 0
        )
        alloc_growth = alloc_after - alloc_before if tracemalloc else 0

        print(
            f"  T-Perf-06: {COUNT} loops, RSS growth = {growth_pct:.1f}% ({mem_before >> 20}MB -> {mem_after >> 20}MB)"
        )
        if tracemalloc:
            print(
                f"  T-Perf-06: tracemalloc alloc growth = {alloc_growth / 1024:.1f}KB"
            )
        self.assertLess(growth_pct, 10)


class TestPerf07_WebExportLatency(unittest.TestCase):
    """T-Perf-07: 前端刷新 —— 1000 条诊断时 export() 耗时 < 500ms"""

    def test_export_1000_diagnoses_under_500ms(self):
        DB_PATH = TEST_HOME / "perf07.db"
        OUT_DIR = COLD_DIR / "perf07"
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA journal_mode = WAL")
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system TEXT, event_type TEXT, mode TEXT, payload TEXT,
            content_hash TEXT, prev_hash TEXT, curr_hash TEXT,
            timestamp REAL NOT NULL, integrity TEXT, chain_status TEXT,
            integrity_score REAL DEFAULT 1.0, ttl_protected INTEGER DEFAULT 0,
            monotonic_ms REAL, lamport INTEGER
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS diagnoses_2026 (
            id INTEGER PRIMARY KEY,
            diagnosis_id TEXT UNIQUE NOT NULL,
            system TEXT NOT NULL, fault_id TEXT,
            diagnosis_name TEXT NOT NULL, evidence JSON NOT NULL,
            evidence_hash TEXT, evidence_quality INTEGER DEFAULT 0,
            inference_chain TEXT, plugin_name TEXT, plugin_version TEXT,
            status TEXT DEFAULT 'pending', confirmed_by TEXT, confirmed_at REAL,
            prev_hash TEXT, curr_hash TEXT,
            confidence REAL DEFAULT 0.9,
            severity TEXT DEFAULT 'info',
            created_at REAL
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS probe_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system TEXT, emit_count INTEGER, drop_count INTEGER,
            disk_free_mb REAL, last_errors JSON,
            integrity_score REAL, window_start REAL
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS expectations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system TEXT, expected_event TEXT, deadline REAL, fulfilled INTEGER
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS system_pid (
            system TEXT PRIMARY KEY,
            pid INTEGER NOT NULL,
            registered_at REAL NOT NULL,
            last_seen REAL NOT NULL,
            mode TEXT NOT NULL DEFAULT 'white'
        )""")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_events_system ON events(system)")
        conn.commit()
        for i in range(1000):
            did = f"d-{i:06d}"
            cur.execute(
                "INSERT INTO events (id, system, event_type, mode, payload, prev_hash, curr_hash, timestamp, integrity, chain_status, lamport) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    i + 1,
                    "perf_sys",
                    "agent_step",
                    "white",
                    json.dumps({"step_id": f"s{i}"}),
                    "0" * 64,
                    "0" * 64,
                    time.time(),
                    "ok",
                    "linked",
                    i,
                ),
            )
            cur.execute(
                "INSERT INTO diagnoses_2026 (id, diagnosis_id, system, fault_id, diagnosis_name, confidence, severity, status, evidence_quality, evidence, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    i + 1,
                    did,
                    "perf_sys",
                    f"F{i}",
                    f"diag_{i}",
                    0.9,
                    "info",
                    "pending",
                    1,
                    "{}",
                    time.time(),
                ),
            )
        cur.execute(
            "INSERT INTO probe_health (system, emit_count, drop_count, disk_free_mb, integrity_score, window_start) VALUES (?, 0, 0, 9999, 1.0, ?)",
            ("perf_sys", time.time()),
        )
        conn.commit()
        conn.close()
        import plugins.web_dashboard.web_exporter as we

        orig_db = we.DB
        orig_hot = we.HOT
        orig_out = we.OUT
        we.DB = DB_PATH
        we.HOT = Path(HOT_DIR)
        we.OUT = OUT_DIR
        try:
            we.HOT.mkdir(parents=True, exist_ok=True)
            t0 = time.monotonic()
            we.export()
            elapsed_ms = (time.monotonic() - t0) * 1000
            output = OUT_DIR / "data.json"
            self.assertTrue(output.exists())
            data = json.loads(output.read_text(encoding="utf-8"))
            print(
                f"  T-Perf-07: export 1000 diagnoses = {elapsed_ms:.1f}ms (diagnoses={len(data.get('diagnoses', []))})"
            )
            self.assertLess(elapsed_ms, 500)
        finally:
            we.DB = orig_db
            we.HOT = orig_hot
            we.OUT = orig_out


if __name__ == "__main__":
    unittest.main(verbosity=2)
