# test_cli_submodules.py —— 乾坤镜 CLI 子模块测试（纯函数 + 逻辑验证）

import sys, unittest, json, tempfile, os, time
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from cli_report import (
    _load_json as cr_load_json,
    _parse_yaml_simple as cr_parse_yaml_simple,
    _calc_health,
    _fmt_duration as cr_fmt_duration,
    _fmt_probe as cr_fmt_probe,
    _probe_status_icon,
    _load_disease_catalog as cr_load_disease_catalog,
    _read_proc_rss,
    ICON_SEV,
    ICON_HL,
    LABEL_HL,
)
from cli_disease import (
    _load_json as cd_load_json,
    _save_json,
    _parse_yaml_simple as cd_parse_yaml_simple,
    _fmt_duration as cd_fmt_duration,
    _fmt_probe as cd_fmt_probe,
    _fmt_health,
    _load_disease_catalog as cd_load_disease_catalog,
)
from cli_admin import _escape_like


class TestCLIReportUtils(unittest.TestCase):
    def test_load_json_existing(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.json"
            path.write_text('{"key": "value"}')
            result = cr_load_json(path)
            self.assertEqual(result, {"key": "value"})

    def test_load_json_missing(self):
        result = cr_load_json(Path("/nonexistent/file.json"))
        self.assertEqual(result, {})

    def test_load_json_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "bad.json"
            path.write_text("not json")
            result = cr_load_json(path)
            self.assertEqual(result, {})

    def test_parse_yaml_simple_list(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("- id: E001\n  name: TestDisease\n  layer: LLM\n  severity: P1\n")
            f.flush()
            path = Path(f.name)
        try:
            result = cr_parse_yaml_simple(path)
            self.assertTrue(len(result) >= 1)
            self.assertEqual(result[0]["id"], "E001")
        finally:
            path.unlink(missing_ok=True)

    def test_calc_health_healthy(self):
        result = _calc_health([], {}, {}, 0)
        self.assertEqual(result, "healthy")

    def test_calc_health_critical(self):
        now = time.time()
        active = [
            {"fault_id": "P0_fault", "severity": "P0", "first_seen": now},
            {"fault_id": "P1_fault", "severity": "P1", "first_seen": now},
        ]
        result = _calc_health(active, {}, {}, now - 1)
        self.assertEqual(result, "critical")

    def test_calc_health_sub_healthy(self):
        now = time.time()
        active = [
            {"fault_id": "f1", "severity": "P2", "first_seen": now},
            {"fault_id": "f2", "severity": "P2", "first_seen": now},
        ]
        result = _calc_health(active, {}, {}, now - 1)
        self.assertEqual(result, "sub_healthy")

    def test_calc_health_warning(self):
        now = time.time()
        active = [
            {"fault_id": "w1", "severity": "P1", "first_seen": now},
        ]
        result = _calc_health(active, {}, {}, now - 1)
        self.assertEqual(result, "warning")

    def test_fmt_duration_seconds(self):
        self.assertIn("15s", cr_fmt_duration(15))

    def test_fmt_duration_minutes(self):
        self.assertIn("m", cr_fmt_duration(90))

    def test_fmt_duration_hours(self):
        self.assertIn("h", cr_fmt_duration(7200))

    def test_fmt_probe(self):
        result = cr_fmt_probe(360)
        self.assertIn("m", result)

    def test_probe_status_icon(self):
        self.assertIsInstance(_probe_status_icon(10), str)
        self.assertIsInstance(_probe_status_icon(300), str)
        self.assertIsInstance(_probe_status_icon(9999), str)

    def test_read_proc_rss_current(self):
        rss = _read_proc_rss(os.getpid())
        self.assertIsInstance(rss, (int, float))
        self.assertGreaterEqual(rss, 0)

    def test_read_proc_rss_dead(self):
        rss = _read_proc_rss(9999999)
        self.assertEqual(rss, 0)

    def test_icon_constants(self):
        self.assertIn("P0", ICON_SEV)
        self.assertIn("healthy", ICON_HL)
        self.assertIn("healthy", LABEL_HL)


class TestCLIDiseaseUtils(unittest.TestCase):
    def test_load_json_existing(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "test.json"
            path.write_text('{"foo": "bar"}')
            result = cd_load_json(path)
            self.assertEqual(result, {"foo": "bar"})

    def test_load_json_missing(self):
        result = cd_load_json(Path("/nonexistent/file.json"))
        self.assertEqual(result, {})

    def test_save_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "out.json"
            _save_json(path, {"a": 1})
            self.assertTrue(path.exists())
            data = json.loads(path.read_text())
            self.assertEqual(data, {"a": 1})

    def test_save_json_nested_dir(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "deep" / "out.json"
            _save_json(path, {"x": "y"})
            self.assertTrue(path.exists())

    def test_fmt_duration_seconds(self):
        self.assertIn("15s", cd_fmt_duration(15))

    def test_fmt_duration_minutes(self):
        self.assertIn("m", cd_fmt_duration(90))

    def test_fmt_probe(self):
        result = cd_fmt_probe(60)
        self.assertIn("m", result)

    def test_fmt_health_levels(self):
        self.assertIsInstance(_fmt_health("healthy"), str)
        self.assertIsInstance(_fmt_health("critical"), str)
        self.assertIsInstance(_fmt_health("unknown"), str)


class TestCLIAdminUtils(unittest.TestCase):
    def test_escape_like_normal(self):
        result = _escape_like("normal_string")
        self.assertIn("string", result)

    def test_escape_like_has_backslashes(self):
        result = _escape_like("100%")
        self.assertNotEqual(result, "100%")
        self.assertIn("%", result)


if __name__ == "__main__":
    unittest.main()
