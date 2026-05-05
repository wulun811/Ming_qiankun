# test_otel.py —— 乾坤镜 otel_bridge + otel_exporter 测试

import sys, unittest, json, os, sqlite3, tempfile
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from otel_bridge import unwrap, extract_attrs, map_span, write_event, emit_meta
from otel_exporter import (
    build_otlp_logs,
    read_diagnoses,
    get_diag_tables,
    export_once,
    SEV_MAP,
    _truncate,
)


class TestOTELBridge(unittest.TestCase):
    def test_unwrap_string(self):
        self.assertEqual(unwrap({"stringValue": "hello"}), "hello")

    def test_unwrap_int(self):
        self.assertEqual(unwrap({"intValue": "42"}), 42)

    def test_unwrap_double(self):
        self.assertEqual(unwrap({"doubleValue": "3.14"}), 3.14)

    def test_unwrap_bool(self):
        self.assertTrue(unwrap({"boolValue": True}))

    def test_unwrap_scalar(self):
        self.assertEqual(unwrap("plain_string"), "plain_string")
        self.assertEqual(unwrap(42), 42)

    def test_unwrap_array(self):
        val = {"arrayValue": {"values": [{"stringValue": "a"}, {"intValue": "1"}]}}
        self.assertEqual(unwrap(val), ["a", 1])

    def test_extract_attrs_empty(self):
        self.assertEqual(extract_attrs([]), {})
        self.assertEqual(extract_attrs(None), {})

    def test_extract_attrs_basic(self):
        raw = [
            {"key": "service.name", "value": {"stringValue": "test_svc"}},
            {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
        ]
        result = extract_attrs(raw)
        self.assertEqual(result["service.name"], "test_svc")
        self.assertEqual(result["gen_ai.system"], "openai")

    def test_map_span_llm(self):
        span = {
            "name": "chat completion",
            "traceId": "abc123def4560000",
            "spanId": "span0010",
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1002000000",
            "status": {"code": 1},
            "attributes": [
                {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                {"key": "gen_ai.response.model", "value": {"stringValue": "gpt-4"}},
                {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "1000"}},
            ],
        }
        r_attrs = {"service.name": "myagent"}
        result = map_span(span, r_attrs)
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "llm_invoke")
        self.assertEqual(result["system"], "myagent")
        self.assertEqual(result["payload"]["layer_llm"]["model"], "gpt-4")

    def test_map_span_tool(self):
        span = {
            "name": "tool_execution",
            "traceId": "abc123def4560000",
            "spanId": "span0020",
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1001000000",
            "status": {"code": 1},
            "attributes": [
                {"key": "tool.name", "value": {"stringValue": "bash"}},
                {"key": "tool.input", "value": {"stringValue": "ls -la"}},
            ],
        }
        r_attrs = {"service.name": "runner"}
        result = map_span(span, r_attrs)
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "tool_call")
        self.assertEqual(result["payload"]["layer_tool"]["tool_name"], "bash")

    def test_map_span_error(self):
        span = {
            "name": "unknown",
            "traceId": "abc123def4560000",
            "spanId": "span0030",
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1001000000",
            "status": {"code": 2},
            "attributes": [
                {"key": "error.type", "value": {"stringValue": "TimeoutError"}},
                {"key": "exception.message", "value": {"stringValue": "timed out"}},
            ],
        }
        r_attrs = {"service.name": "worker"}
        result = map_span(span, r_attrs)
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "error")
        self.assertEqual(result["payload"]["error_type"], "TimeoutError")

    def test_map_span_memory(self):
        span = {
            "name": "memory_retrieval",
            "traceId": "abc123def4560000",
            "spanId": "span0040",
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1001000000",
            "status": {"code": 1},
            "attributes": [
                {"key": "db.system", "value": {"stringValue": "redis"}},
                {"key": "db.response.returned_rows", "value": {"intValue": "5"}},
            ],
        }
        r_attrs = {"service.name": "cache"}
        result = map_span(span, r_attrs)
        self.assertIsNotNone(result)
        self.assertEqual(result["event_type"], "memory_retrieve")

    def test_map_span_none(self):
        span = {
            "name": "misc",
            "traceId": "a",
            "spanId": "b",
            "status": {"code": 0},
            "attributes": [],
        }
        r_attrs = {"service.name": "x"}
        result = map_span(span, r_attrs)
        self.assertIsNone(result)

    def test_write_event(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("MING_HOT_DIR")
            os.environ["MING_HOT_DIR"] = td
            import otel_bridge

            otel_bridge.HOT_DIR = td
            try:
                ev = {"system": "test", "event_type": "test_ev", "payload": {}}
                write_event(ev)
                files = list(Path(td).glob("*.jsonl"))
                self.assertTrue(len(files) > 0)
            finally:
                if old:
                    os.environ["MING_HOT_DIR"] = old
                    otel_bridge.HOT_DIR = os.path.expanduser(old)
                else:
                    os.environ.pop("MING_HOT_DIR", None)

    def test_emit_meta(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("MING_HOT_DIR")
            os.environ["MING_HOT_DIR"] = td
            import otel_bridge

            otel_bridge.HOT_DIR = td
            try:
                emit_meta("__health__", {"status": "ok"})
                files = list(Path(td).glob("*.jsonl"))
                self.assertTrue(len(files) > 0)
                content = Path(files[0]).read_text()
                self.assertIn("__health__", content)
                self.assertIn("otel_bridge", content)
            finally:
                if old:
                    os.environ["MING_HOT_DIR"] = old
                    otel_bridge.HOT_DIR = os.path.expanduser(old)
                else:
                    os.environ.pop("MING_HOT_DIR", None)


class TestOTELExporter(unittest.TestCase):
    def test_sev_map(self):
        self.assertEqual(SEV_MAP["P0"], (21, "FATAL"))
        self.assertEqual(SEV_MAP["P1"], (17, "ERROR"))
        self.assertEqual(SEV_MAP["P2"], (13, "WARN"))

    def test_truncate_short(self):
        s, truncated = _truncate("hello")
        self.assertEqual(s, "hello")
        self.assertFalse(truncated)

    def test_build_otlp_logs_empty(self):
        result = build_otlp_logs([])
        self.assertIn("resourceLogs", result)
        self.assertEqual(
            len(result["resourceLogs"][0]["scopeLogs"][0]["logRecords"]), 0
        )

    def test_build_otlp_logs_basic(self):
        diag = {
            "fault_id": "E001",
            "diagnosis_name": "Test Diagnosis",
            "severity": "P1",
            "system": "test",
            "confidence": 0.85,
            "evidence_quality": 2,
            "evidence": '{"key": "value"}',
            "created_at": 1714000000.0,
        }
        result = build_otlp_logs([diag])
        records = result["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["severityText"], "ERROR")

    def test_get_diag_tables_empty(self):
        conn = sqlite3.connect(":memory:")
        tables = get_diag_tables(conn)
        self.assertEqual(tables, [])
        conn.close()

    def test_get_diag_tables_with_data(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE diagnoses_test (id INTEGER, created_at REAL)")
        tables = get_diag_tables(conn)
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0], "diagnoses_test")
        conn.close()

    def test_read_diagnoses_empty(self):
        conn = sqlite3.connect(":memory:")
        result = read_diagnoses(conn, 0, 10)
        self.assertEqual(result, [])
        conn.close()

    def test_read_diagnoses_with_data(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE TABLE diagnoses_t1 (fault_id TEXT, severity TEXT, system TEXT, confidence REAL, created_at REAL)"
        )
        conn.execute(
            "INSERT INTO diagnoses_t1 VALUES (?, ?, ?, ?, ?)",
            ("E001", "P1", "test", 0.9, 1714000000.0),
        )
        conn.commit()
        result = read_diagnoses(conn, 0, 10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["fault_id"], "E001")
        conn.close()

    def test_export_once_empty(self):
        conn = sqlite3.connect(":memory:")
        result = export_once(conn, 0, 10)
        self.assertEqual(result, 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
