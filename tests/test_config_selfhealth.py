# test_config_selfhealth.py —— 乾坤镜 config_loader + self_health 测试

import sys, unittest, json, os, tempfile, copy
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from config_loader import (
    _strip_comments,
    _find_config,
    _deep_merge,
    _get_nested,
    _set_nested,
    load_config,
    validate,
    format_config_show,
    DEFAULTS,
)
from self_health import (
    BASE,
    HOT,
    DB,
    HB,
    INTERVAL,
    T,
    _plat_lock,
    run_check,
    check_archiver_lag,
    check_disk_free,
)


class TestConfigLoader(unittest.TestCase):
    def test_strip_single_line_comment(self):
        text = '{"key": "value" // comment\n}'
        result = _strip_comments(text)
        self.assertNotIn("comment", result)
        self.assertIn("value", result)

    def test_strip_block_comment(self):
        text = '{"key": /* block */ "value"}'
        result = _strip_comments(text)
        self.assertNotIn("block", result)
        self.assertIn("value", result)

    def test_deep_merge_simple(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        self.assertEqual(result, {"a": 1, "b": 3, "c": 4})

    def test_deep_merge_nested(self):
        base = {"a": {"x": 1, "y": 2}}
        override = {"a": {"y": 99, "z": 3}}
        result = _deep_merge(base, override)
        self.assertEqual(result["a"], {"x": 1, "y": 99, "z": 3})

    def test_get_nested_top(self):
        self.assertEqual(_get_nested({"a": 1}, "a"), 1)

    def test_get_nested_deep(self):
        self.assertEqual(_get_nested({"a": {"b": 42}}, "a.b"), 42)

    def test_get_nested_missing(self):
        self.assertIsNone(_get_nested({}, "x.y.z"))

    def test_set_nested_top(self):
        d = {}
        _set_nested(d, "a", 1)
        self.assertEqual(d["a"], 1)

    def test_set_nested_deep(self):
        d = {}
        _set_nested(d, "a.b.c", 42)
        self.assertEqual(d["a"]["b"]["c"], 42)

    def test_set_nested_tuple(self):
        d = {}
        _set_nested(d, ("x", "y"), 99)
        self.assertEqual(d["x"]["y"], 99)

    def test_validate_valid(self):
        config = copy.deepcopy(dict(DEFAULTS))
        errors = validate(config)
        self.assertEqual(errors, [])

    def test_validate_invalid_mode(self):
        config = _deep_merge(DEFAULTS, {"mode": "invalid_mode"})
        errors = validate(config)
        self.assertTrue(any("mode" in e.lower() for e in errors))

    def test_validate_out_of_range(self):
        config = copy.deepcopy(dict(DEFAULTS))
        config["archiver"]["batch_size"] = 99999
        errors = validate(config)
        self.assertTrue(any("batch_size" in e for e in errors))

    def test_format_config_show(self):
        config = {"version": "0.11.9m", "mode": "standalone", "nested": {"key": "val"}}
        result = format_config_show(config, "test")
        self.assertIn("version", result)
        self.assertIn("0.11.9m", result)
        self.assertIn("test", result)

    def test_load_config_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            old_env = os.environ.get("MING_CONFIG_FILE")
            os.environ["MING_CONFIG_FILE"] = str(Path(td) / "nonexistent.json")
            try:
                config, source, errors = load_config()
                self.assertIn(source, ("default", "env"))
                self.assertEqual(config["version"], "0.11.9m")
            finally:
                if old_env:
                    os.environ["MING_CONFIG_FILE"] = old_env
                else:
                    os.environ.pop("MING_CONFIG_FILE", None)

    def test_load_config_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.json"
            cfg_path.write_text('{"version": "custom","mode": "standalone"}')
            old = os.environ.get("MING_CONFIG_FILE")
            os.environ["MING_CONFIG_FILE"] = str(cfg_path)
            try:
                config, source, _ = load_config()
                self.assertEqual(config["version"], "custom")
            finally:
                if old:
                    os.environ["MING_CONFIG_FILE"] = old
                else:
                    os.environ.pop("MING_CONFIG_FILE", None)


class TestSelfHealth(unittest.TestCase):
    def test_import_and_constants(self):
        self.assertIsInstance(BASE, Path)
        self.assertIsInstance(T, dict)
        self.assertIn("arch_lag", T)

    def test_plat_lock_unlocked(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = str(Path(td) / "test.lock")
            locked, cleanup = _plat_lock(lock_path)
            self.assertTrue(locked)
            self.assertTrue(callable(cleanup))
            cleanup()

    def test_plat_lock_already_locked(self):
        with tempfile.TemporaryDirectory() as td:
            lock_path = str(Path(td) / "test2.lock")
            locked1, cleanup1 = _plat_lock(lock_path)
            self.assertTrue(locked1)
            try:
                locked2, cleanup2 = _plat_lock(lock_path)
                self.assertFalse(locked2)
            finally:
                cleanup1()

    def test_run_check_basic(self):
        result = run_check()
        if isinstance(result, tuple) and len(result) == 2:
            metrics, warn_flag = result
            self.assertIsInstance(metrics, dict)
        else:
            self.assertIsInstance(result, dict)

    def test_check_archiver_lag(self):
        result = check_archiver_lag()
        if isinstance(result, tuple):
            lag, _ = result
            self.assertIsInstance(lag, (int, float))
        else:
            self.assertIsInstance(result, (int, float))

    def test_check_disk_free(self):
        result = check_disk_free()
        if isinstance(result, tuple):
            free, _ = result
            self.assertIsInstance(free, float)
        else:
            self.assertIsInstance(result, float)


if __name__ == "__main__":
    unittest.main()
