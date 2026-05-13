# test_remedy_engine.py —— 乾坤镜 remedy_engine 测试

import sys, unittest, json, tempfile, os
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

os.environ["MING_LANG"] = "zh"

from remedy_engine import (
    _render,
    _pv,
    _simple_yaml,
    _yaml_load,
    prescribe,
    load_remedies,
    format_prescription_cli,
)


class TestRemedyEngine(unittest.TestCase):
    def test_pv_string(self):
        self.assertEqual(_pv("hello"), "hello")

    def test_pv_int(self):
        self.assertEqual(_pv("42"), 42)

    def test_pv_float(self):
        self.assertEqual(_pv("3.14"), 3.14)

    def test_pv_bool_true(self):
        self.assertTrue(_pv("true"))

    def test_pv_bool_false(self):
        self.assertFalse(_pv("false"))

    def test_pv_null(self):
        self.assertIsNone(_pv("null"))
        self.assertIsNone(_pv("~"))

    def test_render_string(self):
        ctx = {"system": "test_sys", "step_id": "s1"}
        tpl = "系统 {{system}} at step {{step_id}}"
        result = _render(tpl, ctx)
        self.assertEqual(result, "系统 test_sys at step s1")

    def test_render_nospace(self):
        ctx = {"system": "x"}
        self.assertEqual(_render("{{system}}", ctx), "x")

    def test_render_dict(self):
        ctx = {"name": "foo"}
        d = {"key": "{{name}}", "nested": {"v": "{{name}}-bar"}}
        result = _render(d, ctx)
        self.assertEqual(result, {"key": "foo", "nested": {"v": "foo-bar"}})

    def test_render_list(self):
        ctx = {"id": "99"}
        lst = ["task-{{id}}", {"cmd": "run-{{id}}"}]
        result = _render(lst, ctx)
        self.assertEqual(result, ["task-99", {"cmd": "run-99"}])

    def test_render_non_string(self):
        self.assertEqual(_render(42, {}), 42)
        self.assertEqual(_render(True, {}), True)

    def test_simple_yaml_list(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("- item1\n- item2\n")
            f.flush()
            path = f.name
        try:
            result = _simple_yaml(path)
            self.assertEqual(result, ["item1", "item2"])
        finally:
            os.unlink(path)

    def test_simple_yaml_dict_list(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("- key1: value1\n")
            f.flush()
            path = f.name
        try:
            result = _simple_yaml(path)
            self.assertEqual(result, [{"key1": "value1"}])
        finally:
            os.unlink(path)

    def test_prescribe_no_rule_id(self):
        result = prescribe({"confidence": 0.9}, {})
        self.assertIsNone(result)

    def test_prescribe_unknown_rule(self):
        result = prescribe({"rule_id": "UNKNOWN"}, {})
        self.assertIsNone(result)

    def test_prescribe_p0_emergency(self):
        remedies = {
            "R001": {
                "tiers": {
                    "emergency": {
                        "summary": "立即重启",
                        "rationale": "内存泄漏",
                        "actions": [
                            {
                                "type": "restart",
                                "target": "{{system}}",
                                "human_confirm": True,
                            }
                        ],
                    }
                }
            }
        }
        diag = {
            "rule_id": "R001",
            "confidence": 0.95,
            "severity": "P0",
            "system": "myservice",
            "payload": {"step_id": "s99"},
        }
        result = prescribe(diag, remedies)
        self.assertIsNotNone(result)
        self.assertEqual(result["prescription"]["tier"], "emergency")
        self.assertTrue(result["prescription"]["requires_human_confirm"])

    def test_prescribe_p0_immediate(self):
        remedies = {
            "R002": {
                "tiers": {
                    "immediate": {
                        "summary": "回滚",
                        "rationale": "配置错误",
                        "actions": ["revert-config"],
                    }
                }
            }
        }
        diag = {
            "rule_id": "R002",
            "confidence": 0.75,
            "severity": "P0",
            "system": "svc",
        }
        result = prescribe(diag, remedies)
        self.assertIsNotNone(result)
        self.assertEqual(result["prescription"]["tier"], "immediate")

    def test_prescribe_p1_short_term(self):
        remedies = {
            "R003": {
                "tiers": {
                    "short_term": {
                        "summary": "扩容",
                        "rationale": "CPU 高",
                        "actions": ["scale-up"],
                    }
                }
            }
        }
        diag = {"rule_id": "R003", "confidence": 0.6, "severity": "P1", "system": "api"}
        result = prescribe(diag, remedies)
        self.assertIsNotNone(result)
        self.assertEqual(result["prescription"]["tier"], "short_term")

    def test_prescribe_no_matching_tier(self):
        remedies = {"R004": {"tiers": {}}}
        diag = {"rule_id": "R004", "confidence": 0.3, "severity": "P3", "system": "x"}
        result = prescribe(diag, remedies)
        self.assertIsNone(result)

    def test_prescribe_fault_id_fallback(self):
        remedies = {
            "F001": {
                "tiers": {
                    "long_term": {
                        "summary": "长期修复",
                        "rationale": "架构问题",
                        "actions": ["refactor"],
                    }
                }
            }
        }
        diag = {"fault_id": "F001", "confidence": 0.5, "severity": "P2", "system": "x"}
        result = prescribe(diag, remedies)
        self.assertIsNotNone(result)
        self.assertIn(result["prescription"]["tier"], ("short_term", "long_term"))

    def test_prescribe_payload_json_string(self):
        remedies = {
            "R005": {
                "tiers": {
                    "short_term": {
                        "summary": "fix",
                        "rationale": "bug",
                        "actions": ["action-{{tool_name}}"],
                    }
                }
            }
        }
        diag = {
            "rule_id": "R005",
            "confidence": 0.6,
            "severity": "P2",
            "payload": '{"tool_name": "git", "agent_id": "a1"}',
        }
        result = prescribe(diag, remedies)
        self.assertIsNotNone(result)
        self.assertIn("git", result["prescription"]["actions"][0])

    def test_format_prescription_cli_none(self):
        result = format_prescription_cli(None)
        self.assertIn("暂无修复建议", result)

    def test_format_prescription_cli_basic(self):
        rx = {
            "prescription": {
                "tier": "emergency",
                "summary": "重启服务",
                "rationale": "OOM",
                "actions": [
                    {
                        "type": "shell",
                        "target": "systemctl restart svc",
                        "dry_run": True,
                    }
                ],
                "requires_human_confirm": True,
                "dry_run_only": True,
            }
        }
        result = format_prescription_cli(rx)
        self.assertIn("emergency", result)
        self.assertIn("DRY-RUN", result)

    def test_load_remedies_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("MING_REMEDIES_DIR")
            os.environ["MING_REMEDIES_DIR"] = td
            try:
                result = load_remedies()
                self.assertEqual(result, {})
            finally:
                if old:
                    os.environ["MING_REMEDIES_DIR"] = old
                else:
                    os.environ.pop("MING_REMEDIES_DIR", None)


if __name__ == "__main__":
    unittest.main()
