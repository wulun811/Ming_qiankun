# test_otel_exporter.py —— v0.11.9 OTLP Exporter 单元测试
# 覆盖: 严重度映射/截断/OTLP结构构建/DB读取/HTTP导出+重试
# 依赖: Python 标准库 (unittest, sqlite3, unittest.mock, tempfile)

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

SRC = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(SRC))

from otel_exporter import (
    SEV_MAP,
    _truncate,
    build_otlp_logs,
    get_diag_tables,
    read_diagnoses,
    export_once,
    MAX_ATTR_SIZE,
    DB_PATH,
    ENDPOINT,
)


class TestSeverityMap(unittest.TestCase):
    """严重度 → OTLP severityNumber + severityText 映射"""

    def test_all_severities_mapped(self):
        self.assertEqual(SEV_MAP["P0"], (21, "FATAL"))
        self.assertEqual(SEV_MAP["P1"], (17, "ERROR"))
        self.assertEqual(SEV_MAP["P2"], (13, "WARN"))
        self.assertEqual(SEV_MAP["P3"], (9, "INFO"))
        self.assertEqual(SEV_MAP["META"], (5, "DEBUG"))


class TestTruncate(unittest.TestCase):
    """_truncate 截断逻辑"""

    def test_short_string_not_truncated(self):
        s, truncated = _truncate("hello")
        self.assertEqual(s, "hello")
        self.assertFalse(truncated)

    def test_long_utf8_truncated(self):
        # _truncate 按字符切片，UTF-8 多字节场景下为近似截断
        # 核心行为: 超长文本被截断并标记 truncated
        long_str = "中" * (MAX_ATTR_SIZE // 2 + 100)
        s, truncated = _truncate(long_str)
        self.assertTrue(truncated, "超长文本应触发 truncated")
        self.assertIn("[truncated]", s, "应包含截断标记")
        self.assertNotEqual(s, long_str, "截断后应与原文不同")

    def test_empty_string(self):
        s, truncated = _truncate("")
        self.assertEqual(s, "")
        self.assertFalse(truncated)


class TestBuildOtlpLogs(unittest.TestCase):
    """build_otlp_logs — 诊断 dict → OTLP LogRecord 结构"""

    def test_empty_diagnoses(self):
        result = build_otlp_logs([])
        records = result["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        self.assertEqual(records, [])

    def test_single_p0_diagnosis(self):
        diag = {
            "fault_id": "SYS-001",
            "diagnosis_name": "内存泄漏",
            "severity": "P0",
            "system": "openclaw",
            "confidence": 0.95,
            "evidence": json.dumps({"rss_mb": 4096}),
            "evidence_quality": 3,
            "created_at": 1714000000.0,
        }
        result = build_otlp_logs([diag])
        records = result["resourceLogs"][0]["scopeLogs"][0]["logRecords"]

        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["severityNumber"], 21)
        self.assertEqual(r["severityText"], "FATAL")
        self.assertEqual(r["body"]["stringValue"], "[SYS-001] 内存泄漏")

        attrs = {a["key"]: a["value"] for a in r["attributes"]}
        self.assertEqual(attrs["qiankun.fault_id"]["stringValue"], "SYS-001")
        self.assertEqual(attrs["qiankun.system"]["stringValue"], "openclaw")
        self.assertAlmostEqual(attrs["qiankun.confidence"]["doubleValue"], 0.95)
        self.assertEqual(attrs["qiankun.severity"]["stringValue"], "P0")
        self.assertEqual(attrs["qiankun.evidence_quality"]["intValue"], 3)

    def test_multiple_severities(self):
        diagnoses = [
            {
                "fault_id": "A",
                "severity": "P0",
                "confidence": 0.9,
                "created_at": 1,
                "evidence_quality": 1,
            },
            {
                "fault_id": "B",
                "severity": "P1",
                "confidence": 0.8,
                "created_at": 2,
                "evidence_quality": 1,
            },
            {
                "fault_id": "C",
                "severity": "P2",
                "confidence": 0.7,
                "created_at": 3,
                "evidence_quality": 1,
            },
        ]
        result = build_otlp_logs(diagnoses)
        records = result["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["severityNumber"], 21)
        self.assertEqual(records[1]["severityNumber"], 17)
        self.assertEqual(records[2]["severityNumber"], 13)

    def test_unknown_severity_defaults_p2(self):
        diag = {
            "fault_id": "X",
            "severity": "FOO",
            "confidence": 0.5,
            "created_at": 1,
            "evidence_quality": 1,
        }
        result = build_otlp_logs([diag])
        records = result["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        self.assertEqual(records[0]["severityNumber"], 13)
        self.assertEqual(records[0]["severityText"], "WARN")

    def test_with_prescription(self):
        diag = {
            "fault_id": "TLT-042",
            "severity": "P1",
            "confidence": 0.85,
            "created_at": 1,
            "evidence_quality": 2,
            "prescription": {
                "summary": "重启对应 Agent 进程",
                "actions": ["kill -HUP $(pgrep agent)", "systemctl restart agent"],
            },
        }
        result = build_otlp_logs([diag])
        attrs = {
            a["key"]: a["value"]
            for a in result["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0][
                "attributes"
            ]
        }
        self.assertIn("qiankun.prescription.summary", attrs)
        self.assertIn(
            "重启对应 Agent 进程", attrs["qiankun.prescription.summary"]["stringValue"]
        )
        self.assertIn("qiankun.prescription.actions", attrs)
        actions = json.loads(attrs["qiankun.prescription.actions"]["stringValue"])
        self.assertEqual(len(actions), 2)

    def test_truncated_evidence_marked(self):
        huge_payload = "x" * (MAX_ATTR_SIZE + 100)
        diag = {
            "fault_id": "TEST",
            "severity": "P2",
            "confidence": 0.5,
            "created_at": 1,
            "evidence_quality": 1,
            "evidence": json.dumps({"data": huge_payload}),
        }
        result = build_otlp_logs([diag])
        records = result["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        attrs = {a["key"]: a["value"] for a in records[0]["attributes"]}
        self.assertIn("qiankun.truncated", attrs)
        self.assertTrue(attrs["qiankun.truncated"]["boolValue"])

    def test_resource_metadata(self):
        result = build_otlp_logs([])
        resource_attrs = result["resourceLogs"][0]["resource"]["attributes"]
        attr_map = {a["key"]: a["value"]["stringValue"] for a in resource_attrs}
        self.assertEqual(attr_map["service.name"], "qiankun-diagnoser")

    def test_time_nano_conversion(self):
        ts = 1714000000.0
        diag = {
            "fault_id": "T",
            "severity": "P0",
            "confidence": 1,
            "created_at": ts,
            "evidence_quality": 1,
        }
        result = build_otlp_logs([diag])
        records = result["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
        self.assertEqual(records[0]["timeUnixNano"], int(ts * 1e9))


class TestDbFunctions(unittest.TestCase):
    """get_diag_tables / read_diagnoses — 内存 SQLite 测试"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS diagnoses_2026 ("
            "  fault_id TEXT, system TEXT, severity TEXT, confidence REAL,"
            "  evidence TEXT, evidence_quality INTEGER, created_at REAL"
            ")"
        )
        self.conn.execute("CREATE TABLE other_table (x INTEGER)")

    def tearDown(self):
        self.conn.close()

    def test_get_diag_tables_filters(self):
        tables = get_diag_tables(self.conn)
        self.assertEqual(len(tables), 1)
        self.assertTrue(tables[0].startswith("diagnoses_"))

    def test_no_tables_returns_empty(self):
        conn = sqlite3.connect(":memory:")
        tables = get_diag_tables(conn)
        self.assertEqual(tables, [])
        conn.close()

    def test_read_diagnoses_since_filter(self):
        self.conn.execute(
            "INSERT INTO diagnoses_2026 VALUES (?,?,?,?,?,?,?)",
            ("SYS-001", "openclaw", "P0", 0.9, "{}", 3, 100),
        )
        self.conn.execute(
            "INSERT INTO diagnoses_2026 VALUES (?,?,?,?,?,?,?)",
            ("SYS-002", "openclaw", "P1", 0.8, "{}", 2, 200),
        )
        self.conn.execute(
            "INSERT INTO diagnoses_2026 VALUES (?,?,?,?,?,?,?)",
            ("SYS-003", "openclaw", "P2", 0.7, "{}", 1, 300),
        )
        self.conn.commit()

        rows = read_diagnoses(self.conn, since=150, limit=10)
        self.assertEqual(len(rows), 2)
        timestamps = [r["created_at"] for r in rows]
        self.assertNotIn(100, timestamps)
        self.assertIn(200, timestamps)
        self.assertIn(300, timestamps)

    def test_read_diagnoses_limit(self):
        for i in range(5):
            self.conn.execute(
                "INSERT INTO diagnoses_2026 VALUES (?,?,?,?,?,?,?)",
                (f"SYS-00{i}", "test", "P2", 0.5, "{}", 1, float(i)),
            )
        self.conn.commit()
        rows = read_diagnoses(self.conn, since=0, limit=3)
        self.assertEqual(len(rows), 3)


class TestExportOnce(unittest.TestCase):
    """export_once — mock HTTP 导出 + 重试"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE diagnoses_2026 ("
            "  fault_id TEXT, system TEXT, severity TEXT, confidence REAL,"
            "  evidence TEXT, evidence_quality INTEGER, created_at REAL"
            ")"
        )

    def tearDown(self):
        self.conn.close()

    def _seed(self, count=3):
        for i in range(count):
            self.conn.execute(
                "INSERT INTO diagnoses_2026 VALUES (?,?,?,?,?,?,?)",
                (f"SYS-00{i}", "test", "P2", 0.5, "{}", 1, 1000.0 + i),
            )
        self.conn.commit()

    @patch("otel_exporter.urllib.request.urlopen")
    @patch("otel_exporter.ENDPOINT", "http://mock:4318/v1/logs")
    def test_successful_export_returns_max_ts(self, mock_urlopen):
        self._seed(3)
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value.read.return_value = b"{}"
        mock_resp.__exit__.return_value = False
        mock_urlopen.return_value = mock_resp

        result = export_once(self.conn, since=0, limit=10)
        self.assertAlmostEqual(result, 1002.0)
        mock_urlopen.assert_called_once()

    @patch("otel_exporter.urllib.request.urlopen")
    @patch("otel_exporter.ENDPOINT", "http://mock:4318/v1/logs")
    def test_client_error_returns_original_since(self, mock_urlopen):
        self._seed(1)
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError("http://mock", 400, "Bad", {}, None)

        result = export_once(self.conn, since=50.0, limit=10)
        self.assertEqual(result, 50.0)

    @patch("otel_exporter.urllib.request.urlopen")
    @patch("otel_exporter.ENDPOINT", "http://mock:4318/v1/logs")
    @patch("otel_exporter.time.sleep", return_value=None)
    def test_server_error_retries_then_returns_original_since(
        self, mock_sleep, mock_urlopen
    ):
        self._seed(1)
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError("http://mock", 503, "Down", {}, None)

        result = export_once(self.conn, since=100.0, limit=10)
        self.assertEqual(result, 100.0)
        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("otel_exporter.urllib.request.urlopen")
    @patch("otel_exporter.ENDPOINT", "http://mock:4318/v1/logs")
    def test_empty_diagnoses_returns_since(self, mock_urlopen):
        result = export_once(self.conn, since=42.0, limit=10)
        self.assertEqual(result, 42.0)
        mock_urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
