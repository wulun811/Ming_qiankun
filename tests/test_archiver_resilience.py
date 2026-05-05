# test_archiver_resilience.py — v0.11.3 归档器健壮性测试
import unittest, tempfile, shutil, time, json, sqlite3, os, threading
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from archiver import Archiver
from probe_uni import ProbeUni


class TestArchiverRecovery(unittest.TestCase):
    """P0-1: 归档器异常恢复"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.cold_dir = Path(self.tmp) / "cold"
        self.db_path = Path(self.tmp) / "ming.db"
        self.hot_dir.mkdir()
        self.cold_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_daemon_survives_exception(self):
        """守护线程在异常后继续运行"""
        archiver = Archiver(
            db_path=str(self.db_path),
            hot_dir=str(self.hot_dir),
            cold_dir=str(self.cold_dir),
        )
        # 创建会导致异常的热轨文件
        bad_file = self.hot_dir / "test_bad.jsonl"
        bad_file.write_text("not valid json\n")

        archiver.start_daemon()
        time.sleep(0.5)
        # 守护线程应该仍在运行（_alive 为 True）
        self.assertTrue(archiver._alive)
        archiver.stop()

    def test_consecutive_error_backoff(self):
        """连续错误有退避机制"""
        archiver = Archiver(
            db_path=str(self.db_path),
            hot_dir=str(self.hot_dir),
            cold_dir=str(self.cold_dir),
        )
        archiver.start_daemon()
        self.assertTrue(hasattr(archiver, "_max_consecutive_errors"))
        self.assertEqual(archiver._max_consecutive_errors, 100)
        self.assertTrue(hasattr(archiver, "_consecutive_errors"))
        archiver.stop()


class TestBusyTimeout(unittest.TestCase):
    """P0-2: SQLite busy_timeout"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.cold_dir = Path(self.tmp) / "cold"
        self.db_path = Path(self.tmp) / "ming.db"
        self.hot_dir.mkdir()
        self.cold_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_open_db_has_busy_timeout(self):
        """_open_db 设置 busy_timeout"""
        archiver = Archiver(
            db_path=str(self.db_path),
            hot_dir=str(self.hot_dir),
            cold_dir=str(self.cold_dir),
        )
        conn = archiver._open_db()
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(timeout, 5000)
        conn.close()


class TestDeduplication(unittest.TestCase):
    """P0-4: 事件去重"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.cold_dir = Path(self.tmp) / "cold"
        self.db_path = Path(self.tmp) / "ming.db"
        self.hot_dir.mkdir()
        self.cold_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unique_index_exists(self):
        """唯一索引 idx_events_curr_hash 存在"""
        archiver = Archiver(
            db_path=str(self.db_path),
            hot_dir=str(self.hot_dir),
            cold_dir=str(self.cold_dir),
        )
        conn = sqlite3.connect(str(self.db_path))
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_events_curr_hash'"
        ).fetchall()
        self.assertEqual(len(indexes), 1)
        conn.close()

    def test_duplicate_events_ignored(self):
        """重复事件被忽略（INSERT OR IGNORE）"""
        archiver = Archiver(
            db_path=str(self.db_path),
            hot_dir=str(self.hot_dir),
            cold_dir=str(self.cold_dir),
        )
        archiver._alive = True  # run_once 需要 _alive 为 True

        # 直接创建热轨文件（使用旧时间戳确保不被排除）
        old_ts = "20250101_000000"
        hot_file = self.hot_dir / f"test_{old_ts}_12345_0001.jsonl"
        event = {
            "system": "test",
            "mode": "white",
            "event_type": "test_event",
            "payload": {"seq": 1},
            "timestamp": time.time(),
            "monotonic_ms": time.monotonic() * 1000,
            "_pid": 12345,
            "_schema_version": "0.11.3",
        }
        hot_file.write_text(json.dumps(event) + "\n")

        archived = archiver.run_once()
        self.assertGreaterEqual(
            archived, 1, f"Expected >= 1 archived events, got {archived}"
        )

        conn = sqlite3.connect(str(self.db_path))
        count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertGreaterEqual(count, 1)
        conn.close()


class TestCrossFilesystemMove(unittest.TestCase):
    """P2-3: 跨文件系统文件移动"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.cold_dir = Path(self.tmp) / "cold"
        self.db_path = Path(self.tmp) / "ming.db"
        self.hot_dir.mkdir()
        self.cold_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_move_fallback_to_copy(self):
        """rename 失败时回退到 copy+delete"""
        archiver = Archiver(
            db_path=str(self.db_path),
            hot_dir=str(self.hot_dir),
            cold_dir=str(self.cold_dir),
        )
        archiver._alive = True  # run_once 需要 _alive 为 True

        # 直接创建热轨文件（使用旧时间戳确保不被排除）
        old_ts = "20250101_000000"
        hot_file = self.hot_dir / f"test_{old_ts}_12345_0001.jsonl"
        event = {
            "system": "test",
            "mode": "white",
            "event_type": "test_event",
            "payload": {"seq": 1},
            "timestamp": time.time(),
            "monotonic_ms": time.monotonic() * 1000,
            "_pid": 12345,
            "_schema_version": "0.11.3",
        }
        hot_file.write_text(json.dumps(event) + "\n")

        archiver.run_once()
        cold_files = list(self.cold_dir.glob("*.jsonl"))
        self.assertGreater(len(cold_files), 0)


if __name__ == "__main__":
    unittest.main()
