# test_ab_webhook_coverage.py —— 乾坤镜 ab_filter + webhook + coverage_scan 测试

import sys, unittest, json, tempfile, sqlite3, ast
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from ab_filter import ABFilter
from webhook import WebhookPusher
from coverage_scan import CoverageScan


class TestABFilter(unittest.TestCase):
    def test_compare_solutions_no_db(self):
        ab = ABFilter(db_path=Path("/nonexistent/ming.db"))
        result = ab.compare_solutions("tool_fail", ["solution1", "solution2"])
        self.assertEqual(result, [])

    def test_recommend_no_db(self):
        ab = ABFilter(db_path=Path("/nonexistent/ming.db"))
        result = ab.recommend("tool_fail")
        self.assertIsNone(result)

    def test_get_solutions_no_db(self):
        ab = ABFilter(db_path=Path("/nonexistent/ming.db"))
        result = ab._get_solutions("tool_fail")
        self.assertEqual(result, [])

    def test_compare_solutions_with_data(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, event_type TEXT, payload TEXT)"
            )
            for i in range(6):
                conn.execute(
                    "INSERT INTO events (event_type, payload) VALUES (?, ?)",
                    (
                        "tool_fail",
                        json.dumps(
                            {"solution": "fix1", "error": "oops", "resolved": True}
                        ),
                    ),
                )
            conn.commit()
            conn.close()

            ab = ABFilter(db_path=db_path)
            result = ab.compare_solutions("tool_fail", ["fix1"])
            self.assertEqual(len(result), 1)
            self.assertGreaterEqual(result[0]["sample_size"], 5)

    def test_recommend_insufficient_data(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test3.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, event_type TEXT, payload TEXT)"
            )
            conn.execute(
                "INSERT INTO events (event_type, payload) VALUES (?, ?)",
                ("tool_fail", json.dumps({"error": "oops"})),
            )
            conn.commit()
            conn.close()

            ab = ABFilter(db_path=db_path)
            result = ab.recommend("tool_fail")
            self.assertIsNotNone(result)
            self.assertEqual(result["confidence"], "insufficient_data")

    def test_recommend_with_enough_data(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test4.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, event_type TEXT, payload TEXT)"
            )
            for i in range(10):
                conn.execute(
                    "INSERT INTO events (event_type, payload) VALUES (?, ?)",
                    ("tool_fail", json.dumps({"error": "oops", "resolved": True})),
                )
            conn.commit()
            conn.close()

            ab = ABFilter(db_path=db_path)
            result = ab.recommend("tool_fail")
            self.assertIsNotNone(result)
            self.assertIn("solution", result)

    def test_default_db_path(self):
        ab = ABFilter()
        self.assertIsInstance(ab.db_path, Path)


class TestWebhook(unittest.TestCase):
    def test_default_config(self):
        wp = WebhookPusher()
        self.assertIn("alert_levels", wp.config)
        self.assertIn("P0", wp.config["alert_levels"])

    def test_push_filtered_by_severity(self):
        wp = WebhookPusher()
        diag = {
            "severity": "P3",
            "system": "test",
            "diagnosis_name": "info",
            "confidence": 0.5,
            "evidence_quality": 1,
            "inference_chain": "",
        }
        wp.push(diag)

    def test_push_raw(self):
        wp = WebhookPusher()
        wp.push_raw({"key": "value"})

    def test_custom_config_path(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("alert_levels:\n  - P2\n")
            f.flush()
            path = Path(f.name)

        try:
            wp = WebhookPusher(config_path=path)
            self.assertIsInstance(wp.config, dict)
        finally:
            path.unlink(missing_ok=True)

    def test_invalid_config_yaml(self):
        wp = WebhookPusher(config_path=Path("/nonexistent/config.yaml"))
        self.assertEqual(wp.config["alert_levels"], ["P0", "P1"])


class TestCoverageScan(unittest.TestCase):
    def test_scan_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            cs = CoverageScan(Path(td))
            result = cs.scan()
            self.assertEqual(result, [])

    def test_scan_with_code(self):
        with tempfile.TemporaryDirectory() as td:
            test_file = Path(td) / "test.py"
            test_file.write_text(
                """
def critical_function():
    try:
        raise ValueError("oops")
    except ValueError:
        pass
"""
            )
            cs = CoverageScan(Path(td))
            result = cs.scan()
            self.assertTrue(len(result) > 0)
            found = [r for r in result if r["function"] == "except_block"]
            self.assertTrue(len(found) > 0)
            self.assertEqual(found[0]["risk_level"], "high")

    def test_scan_with_emit(self):
        with tempfile.TemporaryDirectory() as td:
            test_file = Path(td) / "test2.py"
            test_file.write_text(
                """
from probe_uni import ProbeUni
probe = ProbeUni()

def critical_process():
    try:
        raise ValueError("oops")
    except ValueError as e:
        probe.emit("error", {"msg": str(e)})
"""
            )
            cs = CoverageScan(Path(td))
            result = cs.scan()
            self.assertEqual(result, [])

    def test_suggest_probes(self):
        with tempfile.TemporaryDirectory() as td:
            test_file = Path(td) / "test3.py"
            test_file.write_text(
                """
def important_func():
    try:
        1/0
    except ZeroDivisionError:
        pass
"""
            )
            cs = CoverageScan(Path(td))
            result = cs.suggest_probes()
            self.assertTrue(len(result) >= 1)
            self.assertEqual(result[0]["suggested_event_type"], "exception_caught")


if __name__ == "__main__":
    unittest.main()
