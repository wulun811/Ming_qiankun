# test_triage.py —— v0.11.2 分诊器测试
# 覆盖：scan_sqlite, evaluate_diseases, generate_suggestions, run()

import sqlite3, json, time, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from triage import (
    scan_sqlite,
    evaluate_diseases,
    generate_suggestions,
    extract_fields,
    flatten_payload,
    COVERAGE_THRESHOLD,
)


class TestExtractFields(unittest.TestCase):
    def test_flat_payload(self):
        payload = {"a": 1, "b": "hello", "c": None}
        fields = extract_fields(payload)
        self.assertIn("a", fields)
        self.assertIn("b", fields)
        self.assertNotIn("c", fields)

    def test_nested_payload(self):
        payload = {"layer_llm": {"latency_ms": 500, "model": "gpt-4"}}
        fields = extract_fields(payload)
        self.assertIn("layer_llm.latency_ms", fields)
        self.assertIn("layer_llm.model", fields)

    def test_empty_payload(self):
        fields = extract_fields({})
        self.assertEqual(fields, set())

    def test_flatten_payload_invalid_json(self):
        fields = flatten_payload("not json")
        self.assertEqual(fields, set())


class TestScanSqlite(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                system TEXT,
                event_type TEXT,
                payload TEXT,
                timestamp REAL
            )
        """)

    def tearDown(self):
        self.conn.close()

    def test_scan_empty(self):
        result = scan_sqlite(self.conn)
        self.assertEqual(result["event_types"], [])
        self.assertEqual(result["events_sampled"], 0)

    def test_scan_with_events(self):
        now = time.time()
        self.conn.execute(
            "INSERT INTO events (system, event_type, payload, timestamp) VALUES (?, ?, ?, ?)",
            ("sys1", "llm_invoke", json.dumps({"layer_llm": {"latency_ms": 500}}), now),
        )
        self.conn.execute(
            "INSERT INTO events (system, event_type, payload, timestamp) VALUES (?, ?, ?, ?)",
            ("sys1", "agent_step", json.dumps({"layer_agent": {"step_id": "s1"}}), now),
        )
        result = scan_sqlite(self.conn)
        self.assertIn("llm_invoke", result["event_types"])
        self.assertIn("agent_step", result["event_types"])
        self.assertEqual(result["events_sampled"], 2)
        self.assertIn("layer_llm.latency_ms", result["all_fields"]["llm_invoke"])

    def test_scan_stratified_sampling(self):
        now = time.time()
        for i in range(150):
            self.conn.execute(
                "INSERT INTO events (system, event_type, payload, timestamp) VALUES (?, ?, ?, ?)",
                (
                    "sys1",
                    "llm_invoke",
                    json.dumps({"layer_llm": {"latency_ms": i}}),
                    now - i,
                ),
            )
        result = scan_sqlite(self.conn)
        self.assertGreater(result["events_sampled"], 50)
        self.assertLessEqual(result["events_sampled"], 100)


class TestEvaluateDiseases(unittest.TestCase):
    def setUp(self):
        self.diseases = [
            {
                "id": "NET-024",
                "name": "网络分区",
                "scope": "observable",
                "depends": {
                    "event_types": ["llm_invoke"],
                    "fields": ["layer_network.tcp_connected_ms"],
                },
            },
            {
                "id": "MDL-035",
                "name": "无限循环",
                "scope": "observable",
                "depends": {
                    "event_types": ["agent_step"],
                    "fields": ["layer_agent.step_id"],
                },
            },
            {
                "id": "SYS-101",
                "name": "代码逻辑幻觉",
                "scope": "mcp_required",
                "depends": {
                    "event_types": ["code_analysis"],
                    "fields": [],
                },
            },
        ]

    def test_ready(self):
        scan = {
            "event_types": ["llm_invoke", "agent_step"],
            "all_fields": {
                "llm_invoke": [
                    "layer_network.tcp_connected_ms",
                    "layer_llm.latency_ms",
                ],
                "agent_step": ["layer_agent.step_id"],
            },
        }
        result = evaluate_diseases(self.diseases, scan)
        self.assertEqual(result["rule_status"]["NET-024"]["status"], "ready")
        self.assertEqual(result["rule_status"]["MDL-035"]["status"], "ready")

    def test_degraded(self):
        diseases = [
            {
                "id": "NET-024",
                "name": "网络分区",
                "scope": "observable",
                "depends": {
                    "event_types": ["llm_invoke"],
                    "fields": [
                        "f1",
                        "f2",
                        "f3",
                        "f4",
                        "f5",
                        "f6",
                        "f7",
                        "f8",
                        "f9",
                        "f10",
                        "f11",
                        "f12",
                    ],
                },
            },
        ]
        scan = {
            "event_types": ["llm_invoke"],
            "all_fields": {
                "llm_invoke": ["f1"],
            },
        }
        result = evaluate_diseases(diseases, scan)
        self.assertEqual(result["rule_status"]["NET-024"]["status"], "degraded")
        self.assertGreater(result["rule_status"]["NET-024"]["confidence_multiplier"], 0)
        self.assertLess(result["rule_status"]["NET-024"]["confidence_multiplier"], 1.0)

    def test_blocked_missing_type(self):
        scan = {
            "event_types": ["agent_step"],
            "all_fields": {
                "agent_step": ["layer_agent.step_id"],
            },
        }
        result = evaluate_diseases(self.diseases, scan)
        self.assertEqual(result["rule_status"]["NET-024"]["status"], "blocked")
        self.assertEqual(result["rule_status"]["NET-024"]["confidence_multiplier"], 0.0)

    def test_blocked_mcp_required(self):
        scan = {
            "event_types": ["code_analysis"],
            "all_fields": {"code_analysis": []},
        }
        result = evaluate_diseases(self.diseases, scan)
        self.assertEqual(result["rule_status"]["SYS-101"]["status"], "degraded")
        self.assertGreater(
            result["rule_status"]["SYS-101"]["confidence_multiplier"], 0.0
        )

    def test_no_fields_required(self):
        diseases = [
            {
                "id": "SYS-001",
                "name": "进程崩溃",
                "scope": "observable",
                "depends": {"event_types": ["system_crash"], "fields": []},
            }
        ]
        scan = {
            "event_types": ["system_crash"],
            "all_fields": {"system_crash": []},
        }
        result = evaluate_diseases(diseases, scan)
        self.assertEqual(result["rule_status"]["SYS-001"]["status"], "ready")


class TestGenerateSuggestions(unittest.TestCase):
    def test_suggestions_ranked_by_disease_count(self):
        diseases = [
            {"id": "A", "name": "病A", "depends": {"fields": ["f1", "f2"]}},
            {"id": "B", "name": "病B", "depends": {"fields": ["f1"]}},
            {"id": "C", "name": "病C", "depends": {"fields": ["f3"]}},
        ]
        eval_result = {
            "rule_status": {
                "A": {"status": "blocked"},
                "B": {"status": "blocked"},
                "C": {"status": "ready"},
            }
        }
        suggestions = generate_suggestions(eval_result, diseases)
        self.assertEqual(len(suggestions), 2)
        self.assertEqual(suggestions[0]["field"], "f1")
        self.assertEqual(suggestions[0]["disease_count"], 2)
        self.assertEqual(suggestions[1]["field"], "f2")
        self.assertEqual(suggestions[1]["disease_count"], 1)


class TestTriageRun(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.snapshot_path = os.path.join(self.tmpdir, "triage_snapshot.json")
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                system TEXT,
                event_type TEXT,
                payload TEXT,
                timestamp REAL
            )
        """)
        now = time.time()
        for i in range(10):
            self.conn.execute(
                "INSERT INTO events (system, event_type, payload, timestamp) VALUES (?, ?, ?, ?)",
                (
                    "sys1",
                    "llm_invoke",
                    json.dumps({"layer_llm": {"latency_ms": i * 100}}),
                    now - i,
                ),
            )
        self.conn.commit()
        self.conn.close()

    def tearDown(self):
        for f in [self.db_path, self.snapshot_path]:
            if os.path.exists(f):
                os.unlink(f)

    def test_run_creates_snapshot(self):
        mock_db = Path(self.db_path)
        mock_snapshot = Path(self.snapshot_path)

        from triage import run as triage_run

        with (
            patch("triage.DB", Path(self.db_path)),
            patch("triage.TRIAGE_SNAPSHOT", Path(self.snapshot_path)),
        ):
            snapshot = triage_run(verbose=False)

        self.assertTrue(os.path.exists(self.snapshot_path))
        data = json.loads(Path(self.snapshot_path).read_text())
        self.assertIn("generated_at", data)
        self.assertIn("rule_status", data)
        self.assertIn("summary", data)
        self.assertGreater(data["summary"]["total"], 0)


if __name__ == "__main__":
    unittest.main()
