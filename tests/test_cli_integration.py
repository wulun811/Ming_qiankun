# test_cli_integration.py —— 乾坤镜 CLI 命令路由集成测试

import sys, unittest, json, tempfile, os, sqlite3, time, io, contextlib
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from cli_admin import _escape_like
from cli import (
    _PREDEFINED_QUERIES,
    cmd_dx,
    main,
)

DB = Path.home() / ".ming" / "ming.db"


class TestEscapeLike(unittest.TestCase):
    def test_escape_percent(self):
        result = _escape_like("100%")
        self.assertIn("%", result)

    def test_escape_underscore(self):
        result = _escape_like("a_b")
        self.assertNotEqual(result, "a_b")

    def test_escape_normal(self):
        result = _escape_like("normal")
        self.assertEqual(result, "normal")


class TestPredefinedQueries(unittest.TestCase):
    def test_recent_p0_has_placeholder(self):
        sql = _PREDEFINED_QUERIES["recent_p0"]
        self.assertIn("{year}", sql)
        self.assertIn("?", sql)

    def test_recent_p1_has_placeholder(self):
        sql = _PREDEFINED_QUERIES["recent_p1"]
        self.assertIn("{year}", sql)
        self.assertIn("?", sql)

    def test_event_stats_exists(self):
        self.assertIn("event_stats", _PREDEFINED_QUERIES)

    def test_system_stats_exists(self):
        self.assertIn("system_stats", _PREDEFINED_QUERIES)

    def test_unconfirmed_exists(self):
        self.assertIn("unconfirmed", _PREDEFINED_QUERIES)

    def test_all_queries_have_limit(self):
        for name, sql in _PREDEFINED_QUERIES.items():
            self.assertIn("?", sql, f"Query {name} missing ? placeholder")


class TestCmdDx(unittest.TestCase):
    def setUp(self):
        self.orig_db = str(DB)
        self.td = tempfile.TemporaryDirectory()
        self.test_db = Path(self.td.name) / "ming.db"
        import cli

        cli.DB = self.test_db

    def tearDown(self):
        import cli

        cli.DB = Path(self.orig_db)
        self.td.cleanup()

    def _setup_db(self):
        conn = sqlite3.connect(str(self.test_db))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, system TEXT, event_type TEXT, payload TEXT, timestamp REAL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS diagnoses_2026 (diagnosis_id INTEGER PRIMARY KEY, system TEXT, fault_id TEXT, diagnosis_name TEXT, confidence REAL, severity TEXT, status TEXT, created_at REAL)"
        )
        for i in range(3):
            conn.execute(
                "INSERT INTO events (system, event_type, payload, timestamp) VALUES (?, ?, ?, ?)",
                ("test_sys", "tool_call", '{"tool":"git"}', time.time() - i),
            )
        conn.execute(
            "INSERT INTO diagnoses_2026 (system, fault_id, diagnosis_name, confidence, severity, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test_sys", "P0-001", "Test P0", 0.95, "P0", "pending", time.time()),
        )
        conn.execute(
            "INSERT INTO diagnoses_2026 (system, fault_id, diagnosis_name, confidence, severity, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test_sys", "P1-001", "Test P1", 0.85, "P1", "pending", time.time()),
        )
        conn.commit()
        conn.close()

    def test_cmd_dx_no_db(self):
        import cli

        cli.DB = Path("/nonexistent/db.db")

        class Args:
            query_name = "recent_p0"
            limit = 5
            json = False

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            cmd_dx(Args())
        output = captured.getvalue()
        self.assertIn("不存在", output)

    def test_cmd_dx_recent_p0(self):
        self._setup_db()

        class Args:
            query_name = "recent_p0"
            limit = 5
            json = False

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            cmd_dx(Args())
        output = captured.getvalue()
        self.assertIn("P0", output)
        self.assertIn("Test P0", output)

    def test_cmd_dx_recent_p1(self):
        self._setup_db()

        class Args:
            query_name = "recent_p1"
            limit = 5
            json = False

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            cmd_dx(Args())
        output = captured.getvalue()
        self.assertIn("P1", output)
        self.assertIn("Test P1", output)

    def test_cmd_dx_event_stats(self):
        self._setup_db()

        class Args:
            query_name = "event_stats"
            limit = 10
            json = False

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            cmd_dx(Args())
        output = captured.getvalue()
        self.assertIn("tool_call", output)

    def test_cmd_dx_unknown_query(self):
        self._setup_db()

        class Args:
            query_name = "nonexistent"
            limit = 5
            json = False

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            cmd_dx(Args())
        output = captured.getvalue()
        self.assertIn("未知查询", output)

    def test_cmd_dx_unconfirmed(self):
        self._setup_db()

        class Args:
            query_name = "unconfirmed"
            limit = 5
            json = False

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            cmd_dx(Args())
        output = captured.getvalue()
        self.assertIn("pending", output)

    def test_cmd_dx_empty_p0(self):
        conn = sqlite3.connect(str(self.test_db))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS diagnoses_2026 (diagnosis_id INTEGER PRIMARY KEY, system TEXT, fault_id TEXT, diagnosis_name TEXT, confidence REAL, severity TEXT, status TEXT, created_at REAL)"
        )
        conn.commit()
        conn.close()

        class Args:
            query_name = "recent_p0"
            limit = 5
            json = False

        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            cmd_dx(Args())
        output = captured.getvalue()
        self.assertIn("无", output)


class TestCLIMain(unittest.TestCase):
    def test_parse_help(self):
        """main() should accept --help and not crash with empty args"""
        with self.assertRaises(SystemExit) as ctx:
            try:
                old = sys.argv
                sys.argv = ["ming", "--help"]
                main()
            except SystemExit:
                raise
            finally:
                sys.argv = old
        self.assertEqual(ctx.exception.code, 0)

    def test_parse_version(self):
        with self.assertRaises(SystemExit) as ctx:
            try:
                old = sys.argv
                sys.argv = ["ming", "--version"]
                main()
            except SystemExit:
                raise
            finally:
                sys.argv = old
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
