# test_archiver_daemons.py —— 乾坤镜 archiver_daemon + cluster_archiver_daemon 测试

import sys, unittest, os
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


class TestArchiverDaemon(unittest.TestCase):
    def test_import_and_main_callable(self):
        from archiver_daemon import main

        self.assertTrue(callable(main))

    def test_archiver_daemon_module_loads(self):
        import archiver_daemon

        self.assertTrue(hasattr(archiver_daemon, "main"))


class TestClusterArchiverDaemon(unittest.TestCase):
    def test_import_module(self):
        from cluster_archiver_daemon import main, _graceful_shutdown

        self.assertTrue(callable(main))
        self.assertTrue(callable(_graceful_shutdown))


class TestClusterPool(unittest.TestCase):
    def test_import(self):
        from cluster_pool import SimplePool

        self.assertTrue(isinstance(SimplePool, type))

    def test_pool_requires_pymysql(self):
        from cluster_pool import pymysql

        if pymysql is None:
            from cluster_pool import SimplePool

            with self.assertRaises(ImportError):
                SimplePool(
                    host="localhost",
                    port=3306,
                    user="test",
                    password="test",
                    database="test",
                    max_conn=2,
                )


if __name__ == "__main__":
    unittest.main()
