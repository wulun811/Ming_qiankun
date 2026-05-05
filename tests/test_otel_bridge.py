# test_otel_bridge.py —— v0.11.9 OTLP Bridge 单元测试
# 覆盖: OTLP AnyValue 解析 / 属性提取 / Span→事件映射 / 热轨写入 / HTTP 端点
# 依赖: Python 标准库 (unittest, json, tempfile, http.client, threading)

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from otel_bridge import (
    unwrap,
    extract_attrs,
    map_span,
    write_event,
    OTLPHandler,
    ThreadedHTTPServer,
    HOT_DIR,
    OTEL_PORT,
    PID,
    _seq,
    _HAS_FCNTL,
)


class TestUnwrap(unittest.TestCase):
    """OTLP AnyValue → Python 原生值"""

    def test_string_value(self):
        self.assertEqual(unwrap({"stringValue": "hello"}), "hello")

    def test_bytes_value(self):
        self.assertEqual(unwrap({"bytesValue": "ABC"}), "ABC")

    def test_int_value(self):
        self.assertEqual(unwrap({"intValue": "42"}), 42)

    def test_double_value(self):
        self.assertAlmostEqual(unwrap({"doubleValue": "3.14"}), 3.14)

    def test_bool_value(self):
        self.assertTrue(unwrap({"boolValue": True}))
        self.assertFalse(unwrap({"boolValue": False}))

    def test_array_value(self):
        arr = {"arrayValue": {"values": [{"stringValue": "a"}, {"stringValue": "b"}]}}
        self.assertEqual(unwrap(arr), ["a", "b"])

    def test_kvlist_value(self):
        kv = {
            "kvlistValue": {
                "values": [
                    {"key": "x", "value": {"intValue": "1"}},
                    {"key": "y", "value": {"stringValue": "two"}},
                ]
            }
        }
        self.assertEqual(unwrap(kv), {"x": 1, "y": "two"})

    def test_plain_value(self):
        self.assertEqual(unwrap("plain"), "plain")
        self.assertEqual(unwrap(42), 42)


class TestExtractAttrs(unittest.TestCase):
    """OTLP attributes 列表 → Python dict"""

    def test_empty(self):
        self.assertEqual(extract_attrs([]), {})

    def test_none(self):
        self.assertEqual(extract_attrs(None), {})

    def test_key_value_pairs(self):
        attrs = [
            {"key": "service.name", "value": {"stringValue": "myapp"}},
            {"key": "pid", "value": {"intValue": "999"}},
        ]
        self.assertEqual(extract_attrs(attrs), {"service.name": "myapp", "pid": 999})


class TestMapSpan(unittest.TestCase):
    """Span → 乾坤镜事件映射"""

    def test_llm_span(self):
        span = {
            "name": "chat completion",
            "traceId": "abcdef1234567890abcdef1234567890",
            "spanId": "deadbeefdeadbeef",
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1001000000",
            "status": {"code": 1},
            "attributes": [
                {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                {"key": "gen_ai.response.model", "value": {"stringValue": "gpt-4"}},
                {"key": "gen_ai.usage.input_tokens", "value": {"intValue": "100"}},
                {"key": "gen_ai.usage.output_tokens", "value": {"intValue": "50"}},
                {
                    "key": "gen_ai.response.finish_reason",
                    "value": {"stringValue": "stop"},
                },
                {"key": "gen_ai.request.temperature", "value": {"doubleValue": "0.7"}},
            ],
        }
        r_attrs = {"service.name": "openclaw"}
        ev = map_span(span, r_attrs)

        self.assertIsNotNone(ev)
        self.assertEqual(ev["event_type"], "llm_invoke")
        self.assertEqual(ev["system"], "openclaw")
        self.assertEqual(ev["mode"], "black")
        self.assertEqual(ev["_source"], "otel_bridge")
        self.assertEqual(ev["payload"]["layer_llm"]["model"], "gpt-4")
        self.assertEqual(ev["payload"]["layer_llm"]["input_tokens"], 100)
        self.assertEqual(ev["payload"]["layer_llm"]["output_tokens"], 50)
        self.assertEqual(ev["payload"]["layer_agent"]["agent_name"], "openclaw")
        self.assertEqual(ev["payload"]["_ingest_channel"], "otel")

    def test_tool_span(self):
        span = {
            "name": "tool_execution",
            "traceId": "a" * 32,
            "spanId": "b" * 16,
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1050000000",
            "status": {"code": 1},
            "attributes": [
                {"key": "tool.name", "value": {"stringValue": "read_file"}},
                {"key": "tool.input", "value": {"stringValue": "/etc/config"}},
            ],
        }
        r_attrs = {"service.name": "openclaw-agent"}
        ev = map_span(span, r_attrs)

        self.assertIsNotNone(ev)
        self.assertEqual(ev["event_type"], "tool_call")
        self.assertEqual(ev["payload"]["layer_tool"]["tool_name"], "read_file")
        self.assertEqual(ev["payload"]["layer_tool"]["tool_status"], "success")
        self.assertAlmostEqual(ev["payload"]["layer_tool"]["execution_ms"], 50.0)

    def test_tool_span_name_heuristic(self):
        span = {
            "name": "tool: write_file",
            "traceId": "a" * 32,
            "spanId": "b" * 16,
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1000020000",
            "status": {"code": 0},
            "attributes": [],
        }
        r_attrs = {"service.name": "test"}
        ev = map_span(span, r_attrs)

        self.assertIsNotNone(ev)
        self.assertEqual(ev["event_type"], "tool_call")
        self.assertEqual(ev["payload"]["layer_tool"]["tool_status"], "fail")

    def test_memory_span(self):
        span = {
            "name": "memory_retrieval",
            "traceId": "c" * 32,
            "spanId": "d" * 16,
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1000010000",
            "status": {"code": 1},
            "attributes": [
                {"key": "db.system", "value": {"stringValue": "chromadb"}},
                {"key": "db.response.returned_rows", "value": {"intValue": "5"}},
            ],
        }
        r_attrs = {"service.name": "rag"}
        ev = map_span(span, r_attrs)

        self.assertIsNotNone(ev)
        self.assertEqual(ev["event_type"], "memory_retrieve")
        self.assertEqual(ev["payload"]["layer_memory"]["memory_store"], "chromadb")
        self.assertEqual(ev["payload"]["layer_memory"]["results_count"], 5)

    def test_error_span(self):
        span = {
            "name": "some_operation",
            "traceId": "e" * 32,
            "spanId": "f" * 16,
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1000000000",
            "status": {"code": 2},
            "attributes": [
                {"key": "error.type", "value": {"stringValue": "TimeoutError"}},
                {
                    "key": "exception.message",
                    "value": {"stringValue": "Connect timeout"},
                },
                {"key": "exception.stacktrace", "value": {"stringValue": "at foo()"}},
            ],
        }
        r_attrs = {"service.name": "test"}
        ev = map_span(span, r_attrs)

        self.assertIsNotNone(ev)
        self.assertEqual(ev["event_type"], "error")
        self.assertEqual(ev["payload"]["error_type"], "TimeoutError")

    def test_network_attrs_appear(self):
        span = {
            "name": "chat completion",
            "traceId": "a" * 32,
            "spanId": "b" * 16,
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1000000000",
            "status": {"code": 1},
            "attributes": [
                {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                {"key": "http.status_code", "value": {"intValue": "200"}},
                {"key": "server.address", "value": {"stringValue": "api.openai.com"}},
            ],
        }
        r_attrs = {"service.name": "test"}
        ev = map_span(span, r_attrs)

        self.assertIsNotNone(ev)
        self.assertIn("layer_network", ev["payload"])
        self.assertEqual(ev["payload"]["layer_network"]["status_code"], 200)
        self.assertEqual(
            ev["payload"]["layer_network"]["target_host"], "api.openai.com"
        )

    def test_unrecognised_span_returns_none(self):
        span = {
            "name": "unknown_thing",
            "traceId": "a" * 32,
            "spanId": "b" * 16,
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1000000000",
            "status": {"code": 1},
            "attributes": [],
        }
        r_attrs = {"service.name": "test"}
        ev = map_span(span, r_attrs)
        self.assertIsNone(ev)

    def test_llm_by_name_heuristic(self):
        # name 包含 llm → 识别为 llm_invoke
        span = {
            "name": "my_llm_call",
            "traceId": "a" * 32,
            "spanId": "b" * 16,
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1000000000",
            "status": {"code": 1},
            "attributes": [],
        }
        r_attrs = {"service.name": "test"}
        ev = map_span(span, r_attrs)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["event_type"], "llm_invoke")

    def test_default_system_name(self):
        span = {
            "name": "chat",
            "traceId": "a" * 32,
            "spanId": "b" * 16,
            "startTimeUnixNano": "1000000000",
            "endTimeUnixNano": "1000000000",
            "status": {"code": 1},
            "attributes": [],
        }
        ev = map_span(span, {})
        self.assertEqual(ev["system"], "otel_unknown")


class TestWriteEvent(unittest.TestCase):
    """热轨 JSONL 写入"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_hot = os.environ.get("MING_HOT_DIR")
        os.environ["MING_HOT_DIR"] = self.tmpdir

        # reset HOT_DIR in module
        global HOT_DIR
        import otel_bridge

        otel_bridge.HOT_DIR = self.tmpdir

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        if self._orig_hot:
            os.environ["MING_HOT_DIR"] = self._orig_hot
        else:
            os.environ.pop("MING_HOT_DIR", None)

    def test_write_event_creates_jsonl(self):
        ev = {
            "system": "test_sys",
            "mode": "black",
            "event_type": "llm_invoke",
            "payload": {"x": 1},
            "timestamp": time.time(),
            "monotonic_ms": time.monotonic() * 1000,
            "_pid": 9999,
            "_schema_version": "test",
            "_source": "test",
        }
        write_event(ev)

        files = list(Path(self.tmpdir).glob("*.jsonl"))
        self.assertGreater(len(files), 0, "应生成 JSONL 文件")

        with open(files[0]) as f:
            written = json.loads(f.readline())
        self.assertEqual(written["system"], "test_sys")
        self.assertEqual(written["event_type"], "llm_invoke")
        self.assertEqual(written["payload"]["x"], 1)


class TestOtelBridgeHttp(unittest.TestCase):
    """OTLP HTTP 端点集成测试"""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp()
        cls._orig_hot = os.environ.get("MING_HOT_DIR")
        os.environ["MING_HOT_DIR"] = cls.tmpdir

        import otel_bridge

        otel_bridge.HOT_DIR = cls.tmpdir

        cls.port = 44319

        cls.server = ThreadedHTTPServer(("127.0.0.1", cls.port), OTLPHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.2)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        shutil.rmtree(cls.tmpdir, ignore_errors=True)
        if cls._orig_hot:
            os.environ["MING_HOT_DIR"] = cls._orig_hot
        else:
            os.environ.pop("MING_HOT_DIR", None)

    def _post_traces(self, payload):
        import http.client

        conn = http.client.HTTPConnection(f"127.0.0.1:{self.port}", timeout=5)
        body = json.dumps(payload).encode("utf-8")
        conn.request(
            "POST",
            "/v1/traces",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        return conn.getresponse()

    def test_post_llm_trace_writes_event(self):
        before = set(Path(self.tmpdir).glob("*.jsonl"))

        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {
                                "key": "service.name",
                                "value": {"stringValue": "openclaw-test"},
                            }
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "name": "chat completion",
                                    "traceId": "abcdef1234567890abcdef1234567890",
                                    "spanId": "deadbeefdeadbeef",
                                    "startTimeUnixNano": "1000000000",
                                    "endTimeUnixNano": "1001000000",
                                    "status": {"code": 1},
                                    "attributes": [
                                        {
                                            "key": "gen_ai.system",
                                            "value": {"stringValue": "openai"},
                                        },
                                        {
                                            "key": "gen_ai.response.model",
                                            "value": {"stringValue": "gpt-4"},
                                        },
                                        {
                                            "key": "gen_ai.usage.input_tokens",
                                            "value": {"intValue": "100"},
                                        },
                                        {
                                            "key": "gen_ai.usage.output_tokens",
                                            "value": {"intValue": "50"},
                                        },
                                    ],
                                }
                            ]
                        }
                    ],
                }
            ]
        }

        resp = self._post_traces(payload)
        self.assertEqual(resp.status, 200)

        after = set(Path(self.tmpdir).glob("*.jsonl"))
        new_files = after - before
        self.assertGreater(len(new_files), 0, "应生成新 JSONL 文件")

    def test_post_404_wrong_path(self):
        import http.client

        conn = http.client.HTTPConnection(f"127.0.0.1:{self.port}", timeout=5)
        conn.request(
            "POST", "/wrong", body=b"{}", headers={"Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 404)

    def test_post_rejects_protobuf(self):
        import http.client

        conn = http.client.HTTPConnection(f"127.0.0.1:{self.port}", timeout=5)
        conn.request(
            "POST",
            "/v1/traces",
            body=b"binary",
            headers={"Content-Type": "application/x-protobuf"},
        )
        resp = conn.getresponse()
        self.assertEqual(resp.status, 415)


if __name__ == "__main__":
    unittest.main()
