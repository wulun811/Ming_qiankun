# test_probe_platform.py —— 乾坤镜 probe_platform 测试

import sys, unittest, os, json, tempfile
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from probe_platform import (
    _detect_platform,
    _is_pid_alive,
    _read_loadavg,
    _collect_metrics,
    _disk_free_mb,
    emit_snapshot,
    emit_health,
    emit_env_profile,
)


class TestProbePlatform(unittest.TestCase):
    def test_detect_platform(self):
        plat = _detect_platform()
        self.assertIn(plat, ("linux", "windows", "macos", "unknown"))

    def test_is_pid_alive_current(self):
        alive = _is_pid_alive(os.getpid())
        self.assertTrue(alive)

    def test_is_pid_alive_dead(self):
        alive = _is_pid_alive(9999999)
        self.assertFalse(alive)

    def test_read_loadavg(self):
        l1, l5, l15 = _read_loadavg()
        self.assertIsInstance(l1, float)
        self.assertIsInstance(l5, float)
        self.assertIsInstance(l15, float)

    def test_collect_metrics_current_pid(self):
        plat = _detect_platform()
        if plat == "linux":
            metrics = _collect_metrics(os.getpid(), "linux")
            self.assertIn("vm_rss_kb", metrics)
            self.assertIsInstance(metrics["vm_rss_kb"], int)
        elif plat == "unknown":
            result = _collect_metrics(os.getpid(), "unknown")
            self.assertEqual(result.get("error"), "unsupported_platform")

    def test_disk_free_mb(self):
        free = _disk_free_mb()
        self.assertIsInstance(free, float)

    def test_emit_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("MING_HOT_DIR")
            os.environ["MING_HOT_DIR"] = td
            import probe_platform

            probe_platform.HOT_DIR = Path(td)
            try:
                emit_snapshot({"vm_rss_kb": 12345, "fd_count": 10})
                files = list(Path(td).glob("*.jsonl"))
                self.assertTrue(len(files) > 0)
                content = Path(files[0]).read_text()
                self.assertIn("platform_snapshot", content)
                self.assertIn("__host__", content)
            finally:
                if old:
                    os.environ["MING_HOT_DIR"] = old
                    probe_platform.HOT_DIR = Path(old)
                else:
                    os.environ.pop("MING_HOT_DIR", None)

    def test_emit_health(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("MING_HOT_DIR")
            os.environ["MING_HOT_DIR"] = td
            import probe_platform

            probe_platform.HOT_DIR = Path(td)
            try:
                emit_health()
                files = list(Path(td).glob("*_health.jsonl"))
                self.assertTrue(len(files) > 0)
                content = Path(files[0]).read_text()
                self.assertIn("__health__", content)
            finally:
                if old:
                    os.environ["MING_HOT_DIR"] = old
                    probe_platform.HOT_DIR = Path(old)
                else:
                    os.environ.pop("MING_HOT_DIR", None)

    def test_emit_env_profile(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("MING_HOT_DIR")
            os.environ["MING_HOT_DIR"] = td
            import probe_platform

            probe_platform.HOT_DIR = Path(td)
            try:
                emit_env_profile({"os": "linux", "cpu_count": 4})
                files = list(Path(td).glob("*_env.jsonl"))
                self.assertTrue(len(files) > 0)
                content = Path(files[0]).read_text()
                self.assertIn("platform_profile", content)
            finally:
                if old:
                    os.environ["MING_HOT_DIR"] = old
                    probe_platform.HOT_DIR = Path(old)
                else:
                    os.environ.pop("MING_HOT_DIR", None)


if __name__ == "__main__":
    unittest.main()
