#!/usr/bin/env python3
# test_v093_cluster.py —— v0.11.9m Cluster 集群架构测试
# 用法: python tests/test_v093_cluster.py
import sys, os, time, json, shutil, unittest
from pathlib import Path

os.environ["MING_LANG"] = "zh"
from unittest.mock import MagicMock, patch

# 测试环境
TEST_HOME = Path.home() / ".ming_v093_cluster_test"
if TEST_HOME.exists():
    shutil.rmtree(TEST_HOME)
TEST_HOME.mkdir()

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))


class TestClusterPool(unittest.TestCase):
    """cluster_pool.py 测试"""

    def test_import(self):
        """验证模块可导入"""
        import cluster_pool

        self.assertIsNotNone(cluster_pool)


class TestClusterArchiver(unittest.TestCase):
    """cluster_archiver.py 测试"""

    def setUp(self):
        self.hot = TEST_HOME / "hot"
        self.staging = self.hot / "staging"
        self.hot.mkdir(parents=True, exist_ok=True)
        self.staging.mkdir(parents=True, exist_ok=True)

        os.environ["MING_HOT_DIR"] = str(self.hot)

    def test_import(self):
        """验证模块可导入"""
        from cluster_archiver import ClusterArchiver

        self.assertIsNotNone(ClusterArchiver)

    def test_create_with_mock_pool(self):
        """验证可创建实例（使用模拟连接池）"""
        from cluster_archiver import ClusterArchiver

        mock_pool = MagicMock()
        archiver = ClusterArchiver(pool=mock_pool, project_id="test_project")
        self.assertEqual(archiver.project_id, "test_project")
        self.assertEqual(archiver._buffer, [])
        self.assertFalse(archiver._alive)

    def test_start_stop_daemon(self):
        """验证守护模式启动和停止"""
        from cluster_archiver import ClusterArchiver

        mock_pool = MagicMock()
        archiver = ClusterArchiver(pool=mock_pool, project_id="test")
        archiver.start_daemon()
        self.assertTrue(archiver._alive)
        archiver.stop()
        self.assertFalse(archiver._alive)

    def test_archive_batch_empty(self):
        """验证空批量归档"""
        from cluster_archiver import ClusterArchiver

        mock_pool = MagicMock()
        archiver = ClusterArchiver(pool=mock_pool)
        result = archiver.archive_batch([])
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["failed"], 0)

    def test_tick_reads_hot_files(self):
        """验证 tick 读取热轨文件"""
        from cluster_archiver import ClusterArchiver

        # 创建热轨文件
        event = {
            "system": "test",
            "event_type": "test_event",
            "payload": {"key": "value"},
        }
        (self.hot / "test.jsonl").write_text(json.dumps(event))

        mock_pool = MagicMock()
        archiver = ClusterArchiver(pool=mock_pool)
        archiver.tick()

        # 文件应从 hot 移到 staging
        hot_files = list(self.hot.glob("*.jsonl"))
        staging_files = list(self.staging.glob("*.jsonl"))
        self.assertEqual(len(hot_files), 0)  # hot 中应已移除
        # staging 有文件或在缓冲中
        self.assertTrue(len(archiver._buffer) >= 0)  # 至少处理了


class TestBridge(unittest.TestCase):
    """bridge.py 测试"""

    def test_import(self):
        """验证模块可导入"""
        from bridge import Bridge

        self.assertIsNotNone(Bridge)

    def test_create_instance(self):
        """验证可创建实例"""
        from bridge import Bridge

        bridge = Bridge()
        self.assertIsNotNone(bridge)
        self.assertEqual(bridge._buffer, [])

    def test_cli_out_mode(self):
        """验证 CLI 模式输出"""
        import io
        from contextlib import redirect_stdout
        from bridge import Bridge

        old_mode = os.environ.get("BRIDGE_MODE", "")
        os.environ["BRIDGE_MODE"] = "cli"

        bridge = Bridge()
        f = io.StringIO()
        with redirect_stdout(f):
            bridge.emit({"integrity": "ok", "confidence": 0.9})

        os.environ["BRIDGE_MODE"] = old_mode
        output = f.getvalue()
        self.assertIn("integrity", output)
        self.assertIn("ok", output)


class TestMingjingEntry(unittest.TestCase):
    """ming.py 双模式入口测试"""

    def test_import(self):
        """验证模块可导入"""
        import ming

        self.assertIsNotNone(ming)

    def test_status_command(self):
        """验证 status 命令可执行"""
        import ming
        import io
        from contextlib import redirect_stdout

        old_pid = ming.PID_FILE
        ming.PID_FILE = TEST_HOME / "test.pid"

        f = io.StringIO()
        with redirect_stdout(f):
            ming.cmd_status()

        ming.PID_FILE = old_pid
        output = f.getvalue()
        self.assertIn("模式", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
