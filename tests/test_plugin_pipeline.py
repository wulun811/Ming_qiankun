# test_plugin_pipeline.py — v0.11.9m 插件流水线端到端测试
import unittest, tempfile, shutil, time, json, sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from archiver import Archiver
from probe_uni import ProbeUni


class TestTriageToLitLite(unittest.TestCase):
    """triage → lit_lite 流水线"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.cold_dir = Path(self.tmp) / "cold"
        self.db_path = Path(self.tmp) / "ming.db"
        self.hot_dir.mkdir()
        self.cold_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_install_no_crash(self):
        """P1-1 + P1-2: 新安装不崩溃"""
        # DB 不存在时 lit_lite 应该直接返回
        import lit_lite

        original_db = lit_lite.DB
        original_out = lit_lite.OUT
        lit_lite.DB = self.db_path
        lit_lite.OUT = Path(self.tmp) / "lit_lite_out"

        try:
            lit_lite.diagnose()  # 不应该崩溃
            self.assertFalse(self.db_path.exists())  # 不应该创建空 DB
        finally:
            lit_lite.DB = original_db
            lit_lite.OUT = original_out


class TestArchiverToWebExporter(unittest.TestCase):
    """archiver → web_exporter 流水线"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.cold_dir = Path(self.tmp) / "cold"
        self.db_path = Path(self.tmp) / "ming.db"
        self.web_dir = Path(self.tmp) / "web"
        self.hot_dir.mkdir()
        self.cold_dir.mkdir()
        self.web_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_export_after_archive(self):
        """归档后导出 data.json"""
        archiver = Archiver(
            db_path=str(self.db_path),
            hot_dir=str(self.hot_dir),
            cold_dir=str(self.cold_dir),
        )
        probe = ProbeUni(system="test", mode="white")
        probe.HOT_DIR = self.hot_dir
        probe.emit("test_event", {"key": "value"})
        probe._flush_batch()
        time.sleep(0.1)

        archiver.run_once()

        import plugins.web_dashboard.web_exporter as we

        original_db = we.DB
        original_hot = we.HOT
        original_out = we.OUT
        we.DB = self.db_path
        we.HOT = self.hot_dir
        we.OUT = self.web_dir

        try:
            we.export()
            data_json = self.web_dir / "data.json"
            self.assertTrue(data_json.exists())
            data = json.loads(data_json.read_text())
            self.assertIn("systems", data)
        finally:
            we.DB = original_db
            we.HOT = original_hot
            we.OUT = original_out


class TestAsyncTriage(unittest.TestCase):
    """P1-3: 异步分诊"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.cold_dir = Path(self.tmp) / "cold"
        self.db_path = Path(self.tmp) / "ming.db"
        self.hot_dir.mkdir()
        self.cold_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_triage_runs_async(self):
        """分诊在后台线程运行"""
        archiver = Archiver(
            db_path=str(self.db_path),
            hot_dir=str(self.hot_dir),
            cold_dir=str(self.cold_dir),
        )
        self.assertTrue(hasattr(archiver, "_triage_running"))
        self.assertFalse(archiver._triage_running.is_set())


if __name__ == "__main__":
    unittest.main()
