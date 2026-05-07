# test_stress_short.py —— 乾坤镜短时压力测试（pytest 版本）
# 模拟 3 秒 x 500 eps 高吞吐，验证归档成功率

import sys, unittest, json, tempfile, os, time, random, hashlib, sqlite3, shutil
from pathlib import Path
import pytest

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


@pytest.mark.stress
class TestStressShort(unittest.TestCase):
    """短时压力测试：3 秒 x 500 eps = 1500 事件，归档成功率 >= 99%"""

    SYSTEMS = ["stress-test-a", "stress-test-b"]
    EVENT_TYPES = [
        "tool_call",
        "llm_invoke",
        "agent_step",
        "error",
        "__health__",
        "llm_output",
        "platform_snapshot",
    ]
    TOOLS = ["bash", "git", "read", "write", "edit", "grep"]
    MODELS = ["gpt-4", "gpt-4o", "claude-3", "gemini-pro"]

    @classmethod
    def setUpClass(cls):
        cls.orig_hot = os.environ.get("MING_HOT_DIR")
        cls.orig_db = os.environ.get("MING_DB_PATH")
        cls.td = tempfile.TemporaryDirectory()
        cls.hot_dir = Path(cls.td.name) / "hot"
        cls.db_path = Path(cls.td.name) / "ming.db"
        os.environ["MING_HOT_DIR"] = str(cls.hot_dir)
        os.environ["MING_DB_PATH"] = str(cls.db_path)
        cls.hot_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        if cls.orig_hot:
            os.environ["MING_HOT_DIR"] = cls.orig_hot
        else:
            os.environ.pop("MING_HOT_DIR", None)
        if cls.orig_db:
            os.environ["MING_DB_PATH"] = cls.orig_db
        else:
            os.environ.pop("MING_DB_PATH", None)
        cls.td.cleanup()

    def _make_event(self, system, event_type):
        payload = {"system": system, "event_type": event_type}
        if event_type == "tool_call":
            payload["layer_tool"] = {
                "tool_name": random.choice(self.TOOLS),
                "tool_status": random.choice(["success", "fail"]),
            }
        elif event_type == "llm_invoke":
            payload["layer_llm"] = {
                "model": random.choice(self.MODELS),
                "input_tokens": random.randint(100, 1000),
                "output_tokens": random.randint(50, 500),
            }
        elif event_type == "agent_step":
            payload["layer_agent"] = {
                "step_id": f"s{random.randint(1, 50)}",
                "session_id": "stress-session",
                "agent_name": random.choice(self.SYSTEMS),
            }
        elif event_type == "error":
            payload["error_type"] = random.choice(["tool_failure", "timeout"])
        elif event_type == "platform_snapshot":
            payload["vm_rss_kb"] = random.randint(100000, 500000)
        return {
            "system": system,
            "mode": "white",
            "event_type": event_type,
            "payload": payload,
            "timestamp": time.time(),
            "monotonic_ms": time.monotonic() * 1000,
            "lamport": 1,
        }

    def _write_hot(self, system, events_batch):
        ts = time.strftime("%Y%m%d_%H%M%S")
        fpath = self.hot_dir / f"{system}_{ts}_{os.getpid()}_stress.jsonl"
        with open(fpath, "a", encoding="utf-8") as f:
            for ev in events_batch:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                f.flush()
        return fpath

    def _setup_archiver_db(self):
        from archiver_schema import init_schema, init_diagnoses_table

        conn = sqlite3.connect(str(self.db_path))
        init_schema(conn)
        init_diagnoses_table(conn)
        conn.commit()
        conn.close()

    def _diagnoses_table(self):
        return f"diagnoses_{time.strftime('%Y')}"

    def test_stress_500eps_3s(self):
        target = 1500

        self._setup_archiver_db()

        from archiver import Archiver

        archiver = Archiver(db_path=str(self.db_path), hot_dir=str(self.hot_dir))

        emitted = 0
        batch_size = 50
        batch_count = target // batch_size

        for b in range(batch_count):
            for system in self.SYSTEMS:
                events = []
                for _ in range(batch_size // 2):
                    etype = random.choice(self.EVENT_TYPES)
                    events.append(self._make_event(system, etype))
                self._write_hot(system, events)
                emitted += len(events)

        total_ingested = 0
        deadline = time.time() + 5
        while time.time() < deadline:
            total_ingested += archiver.run_once()
            if total_ingested == emitted:
                break
            time.sleep(0.05)
        self.assertEqual(
            total_ingested,
            emitted,
            f"归档率不足: {total_ingested}/{emitted}，应全部归档",
        )

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            db_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            self.assertEqual(
                db_count, emitted, f"数据库事件数不足: {db_count}/{emitted}"
            )
            for system in self.SYSTEMS:
                sys_count = conn.execute(
                    "SELECT COUNT(*) FROM events WHERE system=?", (system,)
                ).fetchone()[0]
                self.assertGreater(sys_count, 0, f"系统 {system} 无事件写入")
            dx_table = self._diagnoses_table()
            total_diag = conn.execute(f"SELECT COUNT(*) FROM {dx_table}").fetchone()[0]
            print(
                f"\n[STRESS] emitted={emitted} ingested={total_ingested} db_events={db_count} diagnoses={total_diag}"
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
