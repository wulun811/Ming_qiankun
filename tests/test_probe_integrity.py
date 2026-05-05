# test_probe_integrity.py —— 乾坤镜 probe_integrity 测试

import sys, unittest, json, tempfile, hashlib, os, sqlite3
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from probe_integrity import ProbeIntegrity


def _make_event(system, event_type, payload, prev_hash="0" * 64):
    content = json.dumps({"s": system, "t": event_type, "p": payload}, sort_keys=True)
    curr_hash = hashlib.sha256(f"{prev_hash}{content}".encode()).hexdigest()
    return {
        "system": system,
        "event_type": event_type,
        "payload": payload,
        "prev_hash": prev_hash,
        "curr_hash": curr_hash,
    }


class TestProbeIntegrity(unittest.TestCase):
    def test_verify_curr_hash_valid(self):
        ev1 = _make_event("test", "tool_call", {"tool": "git"})
        ev2 = _make_event("test", "llm_invoke", {"model": "gpt-4"}, ev1["curr_hash"])
        ev3 = _make_event("test", "tool_call", {"tool": "bash"}, ev2["curr_hash"])

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for ev in [ev1, ev2, ev3]:
                f.write(json.dumps(ev) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            pi = ProbeIntegrity()
            results = pi.verify_curr_hash(path)
            self.assertEqual(len(results), 3)
            for r in results:
                self.assertTrue(r["valid"], f"Line {r['line_no']} should be valid")
        finally:
            path.unlink(missing_ok=True)

    def test_verify_curr_hash_broken(self):
        ev1 = _make_event("test", "tool_call", {"tool": "git"})
        corrupted = {
            "system": "test",
            "event_type": "llm_invoke",
            "payload": {"model": "gpt-4"},
            "prev_hash": ev1["curr_hash"],
            "curr_hash": "deadbeef",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            for ev in [ev1, corrupted]:
                f.write(json.dumps(ev) + "\n")
            f.flush()
            path = Path(f.name)

        try:
            pi = ProbeIntegrity()
            results = pi.verify_curr_hash(path)
            self.assertEqual(len(results), 2)
            self.assertTrue(results[0]["valid"])
            self.assertFalse(results[1]["valid"])
        finally:
            path.unlink(missing_ok=True)

    def test_verify_curr_hash_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write("not valid json\n")
            f.flush()
            path = Path(f.name)

        try:
            pi = ProbeIntegrity()
            results = pi.verify_curr_hash(path)
            self.assertEqual(len(results), 1)
            self.assertFalse(results[0]["valid"])
        finally:
            path.unlink(missing_ok=True)

    def test_verify_hash_chain_no_db(self):
        pi = ProbeIntegrity(db_path=Path("/nonexistent/ming.db"))
        result = pi.verify_hash_chain()
        self.assertEqual(result["total"], 0)

    def test_verify_hash_chain_with_data(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    system TEXT,
                    prev_hash TEXT,
                    curr_hash TEXT,
                    integrity TEXT DEFAULT 'valid'
                )
            """
            )
            conn.execute(
                "INSERT INTO events (system, prev_hash, curr_hash, integrity) VALUES (?, ?, ?, ?)",
                ("test", "0" * 64, "abc123", "valid"),
            )
            conn.execute(
                "INSERT INTO events (system, prev_hash, curr_hash, integrity) VALUES (?, ?, ?, ?)",
                ("test", "abc123", "def456", "valid"),
            )
            conn.execute(
                "INSERT INTO events (system, prev_hash, curr_hash, integrity) VALUES (?, ?, ?, ?)",
                ("test", "def456", "reboot_marker", "rebooted"),
            )
            conn.execute(
                "INSERT INTO events (system, prev_hash, curr_hash, integrity) VALUES (?, ?, ?, ?)",
                ("test", "reboot_marker", "bad_hash", "corrupted"),
            )
            conn.commit()
            conn.close()

            pi = ProbeIntegrity(db_path=db_path)
            result = pi.verify_hash_chain()
            self.assertEqual(result["total"], 4)
            self.assertGreater(result["valid"], 0)
            self.assertGreater(result["broken"], 0)
            self.assertGreater(result["rebooted_count"], 0)

    def test_init_default_paths(self):
        pi = ProbeIntegrity()
        self.assertIsInstance(pi.db_path, Path)
        self.assertIsInstance(pi.hot_dir, Path)


if __name__ == "__main__":
    unittest.main()
