#!/usr/bin/env python3
"""集成测试：Probe → Archiver → QueryBridge 端到端"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from probe_uni import ProbeUni
from archiver import Archiver
from query_bridge import QueryBridge


@contextmanager
def isolated_context(prefix="ming_int_test_"):
    temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    hot_dir = temp_dir / "hot"
    cold_dir = temp_dir / "cold"
    db_path = temp_dir / "ming.db"
    hot_dir.mkdir()
    cold_dir.mkdir()
    orig_hot = ProbeUni.HOT_DIR
    ProbeUni.HOT_DIR = hot_dir
    try:
        yield {
            "temp_dir": temp_dir,
            "hot_dir": hot_dir,
            "cold_dir": cold_dir,
            "db_path": db_path,
        }
    finally:
        ProbeUni.HOT_DIR = orig_hot
        shutil.rmtree(temp_dir, ignore_errors=True)


class TestIntegration(unittest.TestCase):
    """Probe → Archiver → QueryBridge 端到端集成测试"""

    def test_full_pipeline(self):
        with isolated_context() as ctx:
            hot_dir = ctx["hot_dir"]
            cold_dir = ctx["cold_dir"]
            db_path = ctx["db_path"]

            # 1. 探针写入
            p = ProbeUni("test_sys", "white")
            p.emit("event_a", {"msg": "a"})
            p.emit("event_b", {"msg": "b"})
            p.emit("tool_fail", {"tool": "git", "error": "test"})
            p._flush_batch()
            time.sleep(1.1)

            # 2. 归档
            a = Archiver(
                db_path=str(db_path), hot_dir=str(hot_dir), cold_dir=str(cold_dir)
            )
            a._alive = True
            archived = a.run_once()
            self.assertGreaterEqual(
                archived, 3, f"Expected >= 3 archived, got {archived}"
            )
            print(f"Archived {archived} events")

            # 3. 查询
            qb = QueryBridge(db_path=str(db_path), hot_dir=str(hot_dir))
            events = qb.query_events(system="test_sys")
            print(f"Found {len(events)} events")
            self.assertGreaterEqual(
                len(events), 3, f"Expected >= 3 events, got {len(events)}"
            )

            # 4. PID 查询
            qb_pid = qb.query_system_pid("test_sys")
            self.assertIsNotNone(qb_pid, "PID info should not be None")
            self.assertEqual(
                qb_pid["source"],
                "sqlite",
                f"Expected sqlite source, got {qb_pid['source']}",
            )
            print(
                f"PID: system={qb_pid['system']}, pid={qb_pid['pid']}, source={qb_pid['source']}"
            )

            # 5. 元数据隔离验证
            conn = sqlite3.connect(str(db_path))
            meta_count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE event_type IN ('__register__', '__touch__')"
            ).fetchone()[0]
            conn.close()
            self.assertEqual(
                meta_count,
                0,
                f"Events table should not contain metadata events, found {meta_count}",
            )
            print("Metadata isolation: PASSED (__register__/__touch__ not in events)")

            qb.close()
            print("\nAll integration tests PASSED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
