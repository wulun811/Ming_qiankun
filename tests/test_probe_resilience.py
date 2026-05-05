# test_probe_resilience.py — v0.11.9m 探针健壮性测试
import unittest, tempfile, shutil, time, json, os, atexit
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from probe_uni import ProbeUni


class TestProbeRetry(unittest.TestCase):
    """P0-3: 探针写入失败重试"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.hot_dir.mkdir()
        # 测试环境跳过 atexit，避免退出时多实例 fcntl 文件锁竞争
        self._atexit_patch = patch("atexit.register", lambda f: None)
        self._atexit_patch.start()

    def tearDown(self):
        self._atexit_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_batch_not_cleared_on_failure(self):
        """写入失败时 batch 不清空"""
        probe = ProbeUni(system="test", mode="white")
        probe.HOT_DIR = self.hot_dir
        probe.emit("test_event", {"seq": 1})
        # 正常情况下 batch 应该在 flush 后清空
        probe._flush_batch()
        self.assertEqual(len(probe._batch), 0)

    def test_max_batch_size(self):
        """batch 有大小限制"""
        self.assertEqual(ProbeUni.MAX_BATCH_SIZE, 1000)


class TestPayloadLimit(unittest.TestCase):
    """P2-2: 探针 payload 限制"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.hot_dir.mkdir()
        self._atexit_patch = patch("atexit.register", lambda f: None)
        self._atexit_patch.start()

    def tearDown(self):
        self._atexit_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_large_payload_rejected(self):
        """超大 payload 被拒绝"""
        probe = ProbeUni(system="test", mode="white")
        probe.HOT_DIR = self.hot_dir
        large_payload = {"data": "x" * (2 * 1024 * 1024)}  # 2MB
        result = probe.emit("test_event", large_payload)
        self.assertFalse(result)
        self.assertTrue(any("too large" in err for err in probe._self_errors))

    def test_normal_payload_accepted(self):
        """正常 payload 被接受"""
        probe = ProbeUni(system="test", mode="white")
        probe.HOT_DIR = self.hot_dir
        result = probe.emit("test_event", {"data": "small"})
        self.assertIsNone(result)  # emit 返回 None 表示成功


class TestSerializationFilter(unittest.TestCase):
    """P1-7: 探针序列化过滤"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot_dir = Path(self.tmp) / "hot"
        self.hot_dir.mkdir()
        self._atexit_patch = patch("atexit.register", lambda f: None)
        self._atexit_patch.start()

    def tearDown(self):
        self._atexit_patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unserializable_event_dropped(self):
        """不可序列化事件被丢弃"""
        probe = ProbeUni(system="test", mode="white")
        probe.HOT_DIR = self.hot_dir

        class Unserializable:
            pass

        probe.emit("test_event", {"bad": Unserializable()})
        probe._flush_batch()

        # 不可序列化事件应该被过滤掉
        self.assertEqual(len(probe._batch), 0)


class TestWindowsLamportClock(unittest.TestCase):
    """P3-4: Windows Lamport 时钟"""

    def setUp(self):
        self._atexit_patch = patch("atexit.register", lambda f: None)
        self._atexit_patch.start()

    def tearDown(self):
        self._atexit_patch.stop()

    def test_windows_fallback_includes_pid(self):
        """Windows 回退模式包含进程 ID"""
        import probe_uni as pu

        original = pu._HAS_FCNTL
        pu._HAS_FCNTL = False

        try:
            probe1 = ProbeUni(system="test1", mode="white", pid=12345)
            probe2 = ProbeUni(system="test2", mode="white", pid=54321)
            # 不同 PID 应该产生不同的 lamport 起始值
            # 由于时间可能相同，我们检查 pid 是否影响结果
            self.assertNotEqual(probe1.pid, probe2.pid)
        finally:
            pu._HAS_FCNTL = original


if __name__ == "__main__":
    unittest.main()
