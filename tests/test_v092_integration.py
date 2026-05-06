#!/usr/bin/env python3
# test_v092_integration.py —— v0.11.9m 端到端集成测试
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from probe_uni import ProbeUni
from archiver import Archiver


class TestV092Integration(unittest.TestCase):
    """v0.9.2 端到端集成测试"""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="ming_v092_test_"))
        cls.hot_dir = cls.temp_dir / "hot"
        cls.cold_dir = cls.temp_dir / "cold"
        cls.db_path = cls.temp_dir / "ming.db"
        cls.heartbeat = cls.temp_dir / ".archiver_heartbeat"
        cls.hot_dir.mkdir()
        cls.cold_dir.mkdir()
        cls._orig_hot = ProbeUni.HOT_DIR
        cls._orig_arch_hot = Archiver.HOT
        cls._orig_arch_cold = Archiver.COLD
        cls._orig_arch_db = Archiver.DB
        cls._orig_arch_hb = Archiver.HEARTBEAT
        ProbeUni.HOT_DIR = cls.hot_dir
        Archiver.HOT = cls.hot_dir
        Archiver.COLD = cls.cold_dir
        Archiver.DB = cls.db_path
        Archiver.HEARTBEAT = cls.heartbeat

    @classmethod
    def tearDownClass(cls):
        ProbeUni.HOT_DIR = cls._orig_hot
        Archiver.HOT = cls._orig_arch_hot
        Archiver.COLD = cls._orig_arch_cold
        Archiver.DB = cls._orig_arch_db
        Archiver.HEARTBEAT = cls._orig_arch_hb
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_01_probe_write(self):
        p = ProbeUni(system="test_v092", mode="white")
        p.emit(
            "llm_invoke",
            {
                "layer_agent": {"step_id": "plan_001"},
                "layer_llm": {
                    "model": "gpt-4o",
                    "latency_ms": 100,
                    "input_tokens": 50000,
                    "output_tokens": 60000,
                },
                "layer_network": {
                    "target_host": "api.openai.com",
                    "tcp_connected": True,
                },
            },
        )
        p.emit("agent_step", {"layer_agent": {"step_id": "loop_step"}})
        for _ in range(5):
            p.emit("agent_step", {"layer_agent": {"step_id": "loop_step"}})
        p.emit(
            "tool_call",
            {
                "layer_agent": {"step_id": "s1"},
                "layer_tool": {"tool_name": "git_force_push", "tool_status": "success"},
                "layer_network": {"target_host": "github.com", "tcp_connected": True},
            },
        )
        p.expect("git_commit", 30)
        p.touch()
        p.health()
        p._flush_batch()
        files = list(ProbeUni.HOT_DIR.glob("*.jsonl"))
        self.assertGreater(len(files), 0, "热轨文件不存在")
        print("  PASS: 探针写入")

    def test_02_archiver_run(self):
        time.sleep(1.1)
        a = Archiver()
        a._alive = True
        n = a.run_once()
        self.assertGreater(n, 0, f"归档事件数为 0")
        print(f"  PASS: 归档 {n} 条事件")

    def test_03_four_tables(self):
        time.sleep(1.1)
        a = Archiver()
        a._alive = True
        a.run_once()
        conn = sqlite3.connect(str(Archiver.DB))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        self.assertIn("events", tables, "events 表不存在")
        self.assertIn("system_pid", tables, "system_pid 表不存在")
        self.assertIn("probe_health", tables, "probe_health 表不存在")
        self.assertIn("expectations", tables, "expectations 表不存在")
        self.assertTrue(
            any(t.startswith("diagnoses_") for t in tables), "diagnoses 表不存在"
        )
        print("  PASS: 四表 Schema")

    def test_04_integrity_score(self):
        time.sleep(1.1)
        a = Archiver()
        a._alive = True
        a.run_once()
        conn = sqlite3.connect(str(Archiver.DB))
        scores = conn.execute("SELECT DISTINCT integrity_score FROM events").fetchall()
        conn.close()
        self.assertGreater(len(scores), 0, "integrity_score 不存在")
        self.assertTrue(any(r[0] == 1.0 for r in scores), "无满分事件")
        print("  PASS: integrity_score")

    def test_05_lamport_clock(self):
        time.sleep(1.1)
        a = Archiver()
        a._alive = True
        a.run_once()
        conn = sqlite3.connect(str(Archiver.DB))
        lamps = conn.execute(
            "SELECT lamport FROM events WHERE lamport IS NOT NULL ORDER BY lamport"
        ).fetchall()
        conn.close()
        self.assertGreater(len(lamps), 0, "Lamport 字段不存在")
        if len(lamps) > 1:
            self.assertTrue(
                all(lamps[i][0] <= lamps[i + 1][0] for i in range(len(lamps) - 1)),
                "Lamport 非单调递增",
            )
        print("  PASS: Lamport 时钟")

    def test_06_expectations(self):
        time.sleep(1.1)
        a = Archiver()
        a._alive = True
        a.run_once()
        conn = sqlite3.connect(str(Archiver.DB))
        exp = conn.execute(
            "SELECT expected_event, fulfilled FROM expectations"
        ).fetchall()
        conn.close()
        self.assertGreater(len(exp), 0, "expectations 未写入")
        self.assertTrue(
            any("git_commit" in str(r[0]) for r in exp), "git_commit 预期不存在"
        )
        print("  PASS: 预期事件")

    def test_07_cold_migration(self):
        time.sleep(1.1)
        a = Archiver()
        a._alive = True
        a.run_once()
        self.assertGreater(
            len(list(Archiver.COLD.glob("*.jsonl"))), 0, "冷轨文件不存在"
        )
        self.assertEqual(len(list(Archiver.HOT.glob("*.jsonl"))), 0, "热轨未清空")
        print("  PASS: 冷轨迁移")

    def test_08_heartbeat(self):
        time.sleep(1.1)
        a = Archiver()
        a._alive = True
        a.run_once()
        self.assertTrue(Archiver.HEARTBEAT.exists(), "心跳文件不存在")
        if Archiver.HEARTBEAT.exists():
            age = time.time() - float(Archiver.HEARTBEAT.read_text().strip())
            self.assertLess(age, 5, f"心跳过期: {age:.1f} 秒")
        print("  PASS: 归档器心跳")

    def test_09_backup(self):
        a = Archiver()
        a._alive = True
        a._startup_time = 0
        a._last_backup = 0
        a._auto_backup()
        backups = list(Archiver.DB.parent.glob("ming_*.db.bak"))
        self.assertGreater(len(backups), 0, "备份文件不存在")
        print("  PASS: 自动备份")


if __name__ == "__main__":
    unittest.main(verbosity=2)
