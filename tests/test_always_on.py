# test_always_on.py —— v0.11.9m always-on 机制单元测试
# 覆盖: always_on_ids 构建 / triage bypass 逻辑 / 非 always_on 不受影响

import json, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lit_rule import load_diseases_yaml


class TestAlwaysOnIds(unittest.TestCase):
    """验证 always_on_ids 从 diseases.yaml 正确构建"""

    def test_always_on_rules_present(self):
        diseases = load_diseases_yaml()
        self.assertIsNotNone(diseases, "diseases.yaml 必须可加载")
        always = [d for d in diseases if d.get("always_on") is True]
        self.assertEqual(len(always), 15, "应有 15 条 always_on 规则")
        ids = {d["id"] for d in always}
        expected = {
            "SYS-024",
            "SYS-026",
            "SYS-027",
            "SYS-031",
            "MDL-040",
            "SYS-032",
            "TLT-058",
            "TLT-059",
            "AGT-068",
            "AGT-069",
            "AGT-070",
            "MEM-083",
            "SYS-033",
            "SYS-036",
            "SYS-038",
        }
        self.assertEqual(ids, expected, "always_on 规则 ID 集合应精确匹配")

    def test_all_always_on_p0_are_present(self):
        diseases = load_diseases_yaml()
        always = {d["id"] for d in diseases if d.get("always_on") is True}
        # v0.11.9m Phase 1 新增的 5 条必须存在
        for rid in ("SYS-032", "TLT-058", "TLT-059", "AGT-068", "AGT-069"):
            self.assertIn(rid, always, f"{rid} 必须标记为 always_on")
        # 土行孙 v0.7 的 5 条 P0 也必须存在
        for rid in ("SYS-024", "SYS-026", "SYS-027", "SYS-031", "MDL-040"):
            self.assertIn(rid, always, f"{rid} 必须标记为 always_on")

    def test_no_unintended_always_on(self):
        diseases = load_diseases_yaml()
        for d in diseases:
            if d.get("always_on") is True:
                continue
            self.assertFalse(
                d.get("always_on", False),
                f"{d['id']} 未显式标记 always_on，不应为 True",
            )


class TestAlwaysOnLogic(unittest.TestCase):
    """验证 always_on 在 is_rule_ready 逻辑中正确工作"""

    def setUp(self):
        diseases = load_diseases_yaml()
        self.always_ids = {d["id"] for d in diseases if d.get("always_on") is True}
        self.assertEqual(len(self.always_ids), 15)

    def _is_rule_ready(self, rule_id, triage):
        """复制 lit_lite.py 中的 is_rule_ready 逻辑"""
        if rule_id in self.always_ids:
            return True
        if triage is None:
            return True
        status = triage.get("rule_status", {}).get(rule_id, {}).get("status", "blocked")
        return status in ("ready", "degraded")

    def test_always_on_executes_when_blocked(self):
        """always_on 规则在 triage blocked 时仍返回 ready"""
        triage = {
            "rule_status": {
                "SYS-027": {"status": "blocked", "confidence_multiplier": 0.0},
            }
        }
        self.assertTrue(
            self._is_rule_ready("SYS-027", triage),
            "SYS-027 (always_on) 在 triage blocked 时应返回 True",
        )

    def test_always_on_executes_when_not_in_triage(self):
        """always_on 规则不在 triage 快照中时也返回 ready"""
        triage = {"rule_status": {}}
        self.assertTrue(self._is_rule_ready("SYS-032", triage))
        self.assertTrue(self._is_rule_ready("TLT-058", triage))
        self.assertTrue(self._is_rule_ready("AGT-069", triage))

    def test_non_always_on_respects_triage(self):
        """非 always_on 规则遵循 triage 判断"""
        triage = {
            "rule_status": {
                "SYS-001": {"status": "ready", "confidence_multiplier": 1.0},
                "SYS-002": {"status": "blocked", "confidence_multiplier": 0.0},
                "SYS-027": {"status": "blocked", "confidence_multiplier": 0.0},
            }
        }
        self.assertTrue(
            self._is_rule_ready("SYS-001", triage),
            "SYS-001 (ready, non-always_on) 应返回 True",
        )
        self.assertFalse(
            self._is_rule_ready("SYS-002", triage),
            "SYS-002 (blocked, non-always_on) 应返回 False",
        )
        self.assertTrue(
            self._is_rule_ready("SYS-027", triage),
            "SYS-027 (always_on) 即使 blocked 也返回 True",
        )

    def test_non_always_on_not_in_triage_defaults_blocked(self):
        """非 always_on 规则不在 triage 快照中时默认 blocked"""
        triage = {"rule_status": {}}
        self.assertFalse(
            self._is_rule_ready("SYS-001", triage),
            "SYS-001 不在 triage 中应返回 blocked=False",
        )

    def test_null_triage_all_ready(self):
        """triage 为 None 时所有规则返回 True"""
        self.assertTrue(self._is_rule_ready("SYS-001", None))
        self.assertTrue(self._is_rule_ready("SYS-999", None))
        self.assertTrue(self._is_rule_ready("SYS-027", None))


if __name__ == "__main__":
    unittest.main()
