# test_anchor_fingerprint.py —— 乾坤镜 anchor + fingerprint 测试

import sys, unittest, json, tempfile, hashlib
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from fingerprint import Fingerprint
from anchor import Anchor


class TestFingerprint(unittest.TestCase):
    def test_generate_deterministic(self):
        event = {
            "system": "test",
            "event_type": "tool_call",
            "payload": {"tool": "git", "exit_code": 0},
        }
        fp1 = Fingerprint.generate(event)
        fp2 = Fingerprint.generate(event)
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 16)

    def test_generate_different_system(self):
        e1 = {"system": "a", "event_type": "t", "payload": {}}
        e2 = {"system": "b", "event_type": "t", "payload": {}}
        self.assertNotEqual(Fingerprint.generate(e1), Fingerprint.generate(e2))

    def test_match_exact(self):
        e1 = {"system": "s", "event_type": "e", "payload": {}}
        results = Fingerprint.match(e1, [e1])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["similarity"], 1.0)

    def test_match_no_match(self):
        e1 = {"system": "a", "event_type": "x", "payload": {}}
        e2 = {"system": "b", "event_type": "y", "payload": {}}
        results = Fingerprint.match(e1, [e2], threshold=0.8)
        self.assertEqual(len(results), 0)

    def test_deduplicate_basic(self):
        events = [
            {"system": "s", "event_type": "e", "payload": {}, "timestamp": 100},
            {"system": "s", "event_type": "e", "payload": {}, "timestamp": 101},
        ]
        result = Fingerprint.deduplicate(events, window_sec=10)
        self.assertEqual(len(result), 1)

    def test_deduplicate_outside_window(self):
        events = [
            {"system": "s", "event_type": "e", "payload": {}, "timestamp": 100},
            {"system": "s", "event_type": "e", "payload": {}, "timestamp": 112},
        ]
        result = Fingerprint.deduplicate(events, window_sec=10)
        self.assertEqual(len(result), 2)

    def test_deduplicate_empty(self):
        self.assertEqual(Fingerprint.deduplicate([], window_sec=10), [])

    def test_deduplicate_different_types(self):
        events = [
            {"system": "s", "event_type": "a", "payload": {}},
            {"system": "s", "event_type": "b", "payload": {}},
        ]
        result = Fingerprint.deduplicate(events, window_sec=10)
        self.assertEqual(len(result), 2)


class TestAnchor(unittest.TestCase):
    def test_import_and_instantiate(self):
        anchor = Anchor()
        self.assertIsInstance(anchor, Anchor)

    def test_verify_chain_no_db(self):
        anchor = Anchor(db_path=Path("/nonexistent/ming.db"))
        result = anchor.verify_chain()
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["valid"], 0)

    def test_export_weekly_no_db(self):
        with tempfile.TemporaryDirectory() as td:
            anchor = Anchor(db_path=Path("/nonexistent/ming.db"))
            result = anchor.export_weekly(Path(td))
            self.assertEqual(result, [])

    def test_verify_export_no_file(self):
        anchor = Anchor()
        self.assertFalse(anchor.verify_export(Path("/nonexistent/file.jsonl")))


if __name__ == "__main__":
    unittest.main()
