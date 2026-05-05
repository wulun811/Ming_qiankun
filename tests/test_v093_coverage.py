#!/usr/bin/env python3
# test_v093_coverage.py —— v0.11.9m 测试覆盖补齐
# 覆盖模块：ab_filter, cli, webhook, probe_platform, coverage_scan, archiver_daemon
# 用法: python tests/test_v093_coverage.py
import sys, os, time, json, shutil, sqlite3, unittest, tempfile, io
from pathlib import Path
from unittest.mock import patch, MagicMock

# 测试环境
TEST_HOME = Path.home() / ".ming_v093_coverage_test"
if TEST_HOME.exists():
    shutil.rmtree(TEST_HOME)
TEST_HOME.mkdir()

os.environ["MING_HOT_DIR"] = str(TEST_HOME / "hot")
os.environ["MING_COLD_DIR"] = str(TEST_HOME / "cold")
os.environ["MING_DB"] = str(TEST_HOME / "ming.db")

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))


class TestABFilter(unittest.TestCase):
    """ab_filter.py 测试"""

    def test_import(self):
        """验证模块可导入"""
        from ab_filter import ABFilter

        self.assertIsNotNone(ABFilter)

    def test_create_instance(self):
        """验证可创建实例"""
        from ab_filter import ABFilter

        f = ABFilter()
        self.assertIsNotNone(f)


class TestWebhook(unittest.TestCase):
    """webhook.py 测试"""

    def test_import(self):
        """验证模块可导入"""
        from webhook import WebhookPusher

        self.assertIsNotNone(WebhookPusher)

    def test_create_instance(self):
        """验证可创建实例"""
        from webhook import WebhookPusher

        s = WebhookPusher()
        self.assertIsNotNone(s)


class TestCoverageScan(unittest.TestCase):
    """coverage_scan.py 测试"""

    def test_import(self):
        """验证模块可导入"""
        from coverage_scan import CoverageScan

        self.assertIsNotNone(CoverageScan)

    def test_create_instance(self):
        """验证可创建实例"""
        from coverage_scan import CoverageScan

        s = CoverageScan(source_dir=str(SRC))
        self.assertIsNotNone(s)


class TestArchiverDaemon(unittest.TestCase):
    """archiver_daemon.py 测试"""

    def test_import(self):
        """验证模块可导入"""
        import archiver_daemon

        self.assertIsNotNone(archiver_daemon)

    def test_main_exists(self):
        """验证 main 函数存在"""
        from archiver_daemon import main

        self.assertTrue(callable(main))


class TestCli(unittest.TestCase):
    """cli.py 测试"""

    def test_import(self):
        """验证模块可导入"""
        from cli import main as cli_main

        self.assertIsNotNone(cli_main)

    def test_cli_help(self):
        """验证 CLI 可打印帮助信息"""
        from cli import main as cli_main
        import sys

        old_argv = sys.argv
        sys.argv = ["cli.py", "--help"]
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            with self.assertRaises(SystemExit):
                cli_main()
        finally:
            sys.stdout = old_stdout
            sys.argv = old_argv


class TestProbePlatform(unittest.TestCase):
    """probe_platform.py 测试"""

    def test_import_basic(self):
        """验证模块基础导入"""
        try:
            import probe_platform

            self.assertIsNotNone(probe_platform)
        except ModuleNotFoundError:
            # 如果 probe_uni 导入有问题，跳过
            self.skipTest("probe_platform 导入依赖问题")

    def test_classes_exist(self):
        """验证主要函数存在（probe_platform.py 为纯函数式，无类）"""
        try:
            import probe_platform

            self.assertTrue(hasattr(probe_platform, "emit_snapshot"))
            self.assertTrue(hasattr(probe_platform, "emit_health"))
            self.assertTrue(hasattr(probe_platform, "_collect_metrics"))
        except ModuleNotFoundError:
            self.skipTest("probe_platform 导入依赖问题")


if __name__ == "__main__":
    unittest.main(verbosity=2)
