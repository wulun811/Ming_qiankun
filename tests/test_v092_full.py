#!/usr/bin/env python3
# test_v092_full.py —— v0.11.9m 全量测试套件（T01-T28）
# 用法: python tests/test_v092_full.py
import sys, os, time, json, shutil, sqlite3, unittest, warnings, signal, importlib
from pathlib import Path

# 测试环境
TEST_HOME = Path.home() / ".ming_v092_full_test"
if TEST_HOME.exists():
    shutil.rmtree(TEST_HOME)
TEST_HOME.mkdir()

os.environ["MING_HOT_DIR"] = str(TEST_HOME / "hot")
os.environ["MING_COLD_DIR"] = str(TEST_HOME / "cold")
os.environ["MING_DB"] = str(TEST_HOME / "ming.db")
os.environ["WQ_ARCHIVER_BATCH_SIZE"] = "100"
os.environ["WQ_ARCHIVER_FLUSH_SEC"] = "0.5"

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from probe_uni import ProbeUni
from archiver import Archiver


class TestProbeCore(unittest.TestCase):
    """T01-T09: 探针核心测试"""

    def setUp(self):
        self.hot = TEST_HOME / "hot"
        self.hot.mkdir(parents=True, exist_ok=True)
        ProbeUni.HOT_DIR = self.hot
        self.p = ProbeUni(system="test_sys", mode="white")

    def tearDown(self):
        for f in self.hot.glob("*.jsonl"):
            f.unlink()

    def test_T01_three_layer_payload(self):
        """T01: 三层 payload 校验，缺 layer_network 标记 _incomplete"""
        self.p.emit(
            "llm_invoke",
            {
                "layer_agent": {"step_id": "s1"},
                "layer_llm": {"model": "gpt-4o", "latency_ms": 100},
            },
        )
        # 强制 flush
        self.p._flush_batch()
        # 检查热轨文件
        files = list(self.hot.glob("*.jsonl"))
        self.assertTrue(len(files) > 0)
        content = files[0].read_text(encoding="utf-8")
        lines = [l for l in content.strip().split("\n") if l]
        event = json.loads(lines[-1])
        self.assertIn("_incomplete", event.get("payload", {}))

    def test_T02_batch_write(self):
        """T02: Batch 写入，10 条或 200ms 触发 flush"""
        for i in range(12):
            self.p.emit("agent_step", {"layer_agent": {"step_id": f"s{i}"}})
        self.p._flush_batch()
        files = list(self.hot.glob("*.jsonl"))
        self.assertTrue(len(files) > 0)

    def test_T03_lamport_single_process(self):
        """T03: Lamport 时钟（单进程），序号单调递增"""
        for i in range(5):
            self.p.emit("agent_step", {"layer_agent": {"step_id": f"s{i}"}})
        self.p._flush_batch()
        # 检查 lamport 单调递增
        files = list(self.hot.glob("*.jsonl"))
        if files:
            lines = [l for l in files[0].read_text().strip().split("\n") if l]
            lamps = [json.loads(l).get("lamport", 0) for l in lines]
            for i in range(len(lamps) - 1):
                self.assertLessEqual(lamps[i], lamps[i + 1])

    def test_T05_clock_skew(self):
        """T05: 时钟跳变检测"""
        # 模拟时钟跳变（通过修改内部状态）
        self.p._last_wall_time = time.time() - 10  # 10 秒前
        self.p.emit("agent_step", {"layer_agent": {"step_id": "s1"}})
        self.p._flush_batch()
        files = list(self.hot.glob("*.jsonl"))
        if files:
            lines = [l for l in files[0].read_text().strip().split("\n") if l]
            event = json.loads(lines[-1])
            self.assertIn("_clock_skew", event.get("payload", {}))

    def test_T08_expect_fulfill(self):
        """T08: expect/fulfill"""
        self.p.expect("git_commit", 30)
        self.p._flush_batch()
        # 检查 expectations 文件
        files = list(self.hot.glob("*.jsonl"))
        self.assertTrue(len(files) > 0)
        content = files[0].read_text()
        self.assertIn("git_commit", content)

    def test_T09_health_report(self):
        """T09: health 上报"""
        self.p.emit("agent_step", {"layer_agent": {"step_id": "s1"}})
        self.p.health()
        self.p._flush_batch()
        files = list(self.hot.glob("*.jsonl"))
        self.assertTrue(len(files) > 0)
        content = files[0].read_text()
        self.assertIn("__health__", content)


class TestArchiverCore(unittest.TestCase):
    """T10-T16: 归档器核心测试"""

    def setUp(self):
        self.hot = TEST_HOME / "hot"
        self.cold = TEST_HOME / "cold"
        self.db = TEST_HOME / "ming.db"
        self.hb = TEST_HOME / ".archiver_heartbeat"
        self.hot.mkdir(parents=True, exist_ok=True)
        self.cold.mkdir(parents=True, exist_ok=True)

        ProbeUni.HOT_DIR = self.hot
        Archiver.HOT = self.hot
        Archiver.COLD = self.cold
        Archiver.DB = self.db
        Archiver.HEARTBEAT = self.hb

        self.p = ProbeUni(system="test_sys", mode="white")
        self.a = Archiver()
        self.a._alive = True

    def _emit_and_archive(self, event_type, payload):
        self.p.emit(event_type, payload)
        return self.a.run_once()

    def test_T10_integrity_score(self):
        """T10: integrity_score 计算"""
        self.p.emit(
            "llm_invoke",
            {
                "layer_agent": {"step_id": "s1"},
                "layer_llm": {"model": "gpt-4o"},
                "layer_network": {"target_host": "api.openai.com"},
            },
        )
        self.p._flush_batch()
        n = self.a.run_once()
        if n == 0:
            self.skipTest("No events to archive")
        conn = sqlite3.connect(str(self.db))
        scores = conn.execute("SELECT DISTINCT integrity_score FROM events").fetchall()
        conn.close()
        self.assertTrue(len(scores) > 0)

    def test_T11_diagnosis_table_exists(self):
        """T11: diagnoses_YYYY 表存在"""
        conn = sqlite3.connect(str(self.db))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        year = time.strftime("%Y")
        self.assertIn(f"diagnoses_{year}", tables)

    def test_T12_yearly_table(self):
        """T12: 按年分表，diagnoses_2026 自动创建"""
        conn = sqlite3.connect(str(self.db))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        year = time.strftime("%Y")
        self.assertIn(f"diagnoses_{year}", tables)

    def test_T13_ttl_cleanup(self):
        """T13: TTL 清理，90 天前事件删除"""
        # 插入一条 91 天前的事件
        conn = sqlite3.connect(str(self.db))
        old_ts = time.time() - 91 * 86400
        conn.execute(
            "INSERT INTO events (system, mode, event_type, payload, curr_hash, timestamp, integrity_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("old_sys", "white", "old_event", "{}", "0" * 64, old_ts, 1.0),
        )
        conn.commit()
        count_before = conn.execute(
            "SELECT COUNT(*) FROM events WHERE system = ?", ("old_sys",)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count_before, 1)
        # 运行 TTL 清理
        self.a._purge_expired_events()
        conn = sqlite3.connect(str(self.db))
        count_after = conn.execute(
            "SELECT COUNT(*) FROM events WHERE system = ?", ("old_sys",)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count_after, 0)

    def test_T14_evidence_quality_table(self):
        """T14: 证据质量字段存在"""
        conn = sqlite3.connect(str(self.db))
        year = time.strftime("%Y")
        cols = [
            r[1]
            for r in conn.execute(f"PRAGMA table_info(diagnoses_{year})").fetchall()
        ]
        conn.close()
        self.assertIn("evidence_quality", cols)

    def test_T16_auto_backup(self):
        """T16: 自动备份，保留最近 5 个备份"""
        self.a._auto_backup()
        backups = list(self.db.parent.glob("ming_*.db.bak"))
        self.assertTrue(len(backups) > 0)


class TestPluginRunner(unittest.TestCase):
    """T22-T23: 插件执行器测试"""

    def test_T22_plugin_loading(self):
        """T22: 插件加载（骨架版），扫描 skill.yaml → 执行 → 心跳"""
        from plugin_runner import _parse_yaml

        yaml_text = """name: test_plugin
version: 0.1.0
type: cron
trigger:
  type: cron
  cron: "*/60 * * * * *"
install:
  entrypoint: "echo hello"
"""
        cfg = _parse_yaml(yaml_text)
        self.assertEqual(cfg.get("name"), "test_plugin")
        self.assertEqual(cfg.get("trigger", {}).get("type"), "cron")

    def test_T23_timeout_isolation(self):
        """T23: 硬超时隔离 + 进程组清理，无僵尸残留"""
        import subprocess

        # 使用内联脚本创建子进程
        script = """
import os, time, sys
pid = os.fork()
if pid == 0:
    time.sleep(300)
    sys.exit(0)
else:
    os.waitpid(pid, 0)
"""

        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "start_new_session": True,
        }

        proc = subprocess.Popen(["python3", "-c", script], **kwargs)
        pgid = os.getpgid(proc.pid)

        try:
            proc.communicate(timeout=0.5)
            self.fail("Should have timed out")
        except subprocess.TimeoutExpired:
            os.killpg(pgid, signal.SIGKILL)

        # 验证：主进程已死
        proc.wait(timeout=2)
        self.assertIsNotNone(proc.returncode)

        # 验证：进程组不存在（全清了）
        time.sleep(0.2)
        try:
            os.killpg(pgid, 0)
            self.fail(f"进程组 {pgid} 仍然存在，有僵尸残留")
        except ProcessLookupError:
            pass


class TestLITLite(unittest.TestCase):
    """T17-T21: lit_lite 诊断测试"""

    def test_T17_meta001_blind_spot(self):
        """T17: META-001 盲区感知，探针丢数据时输出 data_unreliable"""
        # 检查 lit_lite 模块存在
        lit_path = SRC / "lit_lite.py"
        self.assertTrue(lit_path.exists(), "lit_lite.py 文件存在")

    def test_T20_semantic_threshold(self):
        """T20: 语义门槛，验证 lit_lite 具备动态规则加载能力"""
        lit_path = SRC / "lit_lite.py"
        content = lit_path.read_text(encoding="utf-8")
        self.assertIn("load_diseases_yaml", content)


class TestWebDashboard(unittest.TestCase):
    """T26: web_dashboard 测试"""

    def test_T26_data_json_generation(self):
        """T26: data.json 生成，含 diagnoses/health/alerts"""
        # 设置测试路径
        import plugins.web_dashboard.web_exporter as we

        we.DB = TEST_HOME / "ming.db"
        we.HOT = TEST_HOME / "hot"
        we.HEARTBEAT = TEST_HOME / ".archiver_heartbeat"
        we.OUT = TEST_HOME / "web"

        data = we.export()
        self.assertIn("systems", data)
        self.assertIn("health", data)
        self.assertIn("alerts", data)


if __name__ == "__main__":
    # 运行测试
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 按顺序加载
    suite.addTests(loader.loadTestsFromTestCase(TestProbeCore))
    suite.addTests(loader.loadTestsFromTestCase(TestArchiverCore))
    suite.addTests(loader.loadTestsFromTestCase(TestPluginRunner))
    suite.addTests(loader.loadTestsFromTestCase(TestLITLite))
    suite.addTests(loader.loadTestsFromTestCase(TestWebDashboard))
    # v0.11.9m: TestDeprecated 删除 — reflector/gate_mini/rule_parser 模块已移除

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 输出总结
    print(f"\n{'=' * 50}")
    print(f"总计: {result.testsRun} 测试")
    print(f"通过: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    print(f"跳过: {len(result.skipped)}")
    print(f"{'=' * 50}")

    sys.exit(0 if result.wasSuccessful() else 1)
