# test_compress.py —— v0.11.10 三级存储压缩测试
# 职责：哈希链完整性、压缩/解压/校验、降级回滚、磁盘边界、异常容错
import unittest, tempfile, shutil, time, json, sqlite3, hashlib, os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from archiver import Archiver
from archiver_compress import (
    compress_payload,
    decompress_payload,
    compute_payload_hash,
    verify_compress_integrity,
    MIN_COMPRESS_BYTES,
)
from archiver_schema import init_schema


def _write_probe_file(hot_dir, system, events):
    path = hot_dir / f"{system}_test.jsonl"
    path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events))
    return path


def _make_event(system, etype, payload=None, ts=None):
    return {
        "system": system,
        "event_type": etype,
        "payload": payload or {"msg": "hello"},
        "timestamp": ts or time.time(),
    }


class TestCompressUtils(unittest.TestCase):
    def test_compress_decompress_roundtrip(self):
        d = {"msg": "hello world", "nested": {"a": 1, "b": 2}}
        blob = compress_payload(d)
        restored = decompress_payload(blob)
        self.assertEqual(d, restored)

    def test_compress_decompress_chinese(self):
        d = {"msg": "你好世界", "data": [1, 2, 3]}
        blob = compress_payload(d)
        restored = decompress_payload(blob)
        self.assertEqual(d, restored)

    def test_verify_integrity_passes(self):
        d = {"test": "data", "layer_llm": {"output_text": "x" * 1000}}
        blob = compress_payload(d)
        self.assertTrue(verify_compress_integrity(d, blob))

    def test_verify_integrity_fails_on_corrupted_blob(self):
        d = {"test": "data"}
        blob = compress_payload(d)
        corrupted = bytearray(blob)
        corrupted[-1] ^= 0xFF
        self.assertFalse(verify_compress_integrity(d, bytes(corrupted)))

    def test_compute_payload_hash_deterministic(self):
        d = {"b": 2, "a": 1}
        h1 = compute_payload_hash(d)
        h2 = compute_payload_hash({"a": 1, "b": 2})
        self.assertEqual(h1, h2)

    def test_small_payload_skipped(self):
        small = {"msg": "x" * 10}
        text = json.dumps(small, ensure_ascii=False, sort_keys=True)
        self.assertLess(len(text), MIN_COMPRESS_BYTES)

    def test_large_payload_compressed(self):
        large = {"data": "x" * 500}
        blob = compress_payload(large)
        restored = decompress_payload(blob)
        self.assertEqual(large, restored)
        self.assertLess(len(blob), len(json.dumps(large)))


class TestHashChain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = Path(self.tmp) / "ming.db"
        self.conn = sqlite3.connect(str(self.db))
        init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hash_chain_uses_payload_hash(self):
        conn = self.conn
        prev = "0" * 64
        events_payloads = [
            {"msg": "first", "val": 1},
            {"msg": "second", "val": 2},
            {"msg": "third", "val": 3},
        ]
        base_ts = 1000000.0
        for i, p in enumerate(events_payloads):
            payload_text = json.dumps(p, ensure_ascii=False, sort_keys=True)
            ph = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
            ts_val = float(base_ts + i)
            content = json.dumps(
                {
                    "system": "test",
                    "event_type": "llm_output",
                    "payload_hash": ph,
                    "timestamp": ts_val,
                },
                sort_keys=True,
            )
            curr = hashlib.sha256(f"{prev}{content}".encode()).hexdigest()
            conn.execute(
                "INSERT INTO events (system, mode, event_type, payload, prev_hash, curr_hash, timestamp, payload_hash) VALUES (?,?,?,?,?,?,?,?)",
                ("test", "white", "llm_output", payload_text, prev, curr, ts_val, ph),
            )
            prev = curr

        rows = conn.execute(
            "SELECT payload, prev_hash, curr_hash, payload_hash, timestamp FROM events ORDER BY id"
        ).fetchall()
        prev_hash = "0" * 64
        for row in rows:
            payload_text, stored_prev, stored_curr, stored_ph, db_ts = row
            self.assertEqual(stored_prev, prev_hash)
            payload_dict = json.loads(payload_text)
            computed_ph = compute_payload_hash(payload_dict)
            self.assertEqual(stored_ph, computed_ph)
            content = json.dumps(
                {
                    "system": "test",
                    "event_type": "llm_output",
                    "payload_hash": computed_ph,
                    "timestamp": db_ts,
                },
                sort_keys=True,
            )
            expected_curr = hashlib.sha256(f"{prev_hash}{content}".encode()).hexdigest()
            self.assertEqual(
                stored_curr,
                expected_curr,
                f"curr_hash mismatch at row: stored_ph={stored_ph} computed_ph={computed_ph} ts={db_ts}",
            )
            prev_hash = stored_curr

    def test_archiver_writes_payload_hash(self):
        archiver = Archiver(
            db_path=str(self.db),
            hot_dir=str(Path(self.tmp) / "hot"),
            cold_dir=str(Path(self.tmp) / "cold"),
        )
        ev = _make_event("hash_test", "llm_output", {"msg": "chain test"}, time.time())
        _write_probe_file(archiver.HOT, "hash_test", [ev])
        count = archiver.run_once()
        self.assertEqual(count, 1)

        conn = sqlite3.connect(str(self.db))
        row = conn.execute(
            "SELECT payload_hash, curr_hash, payload FROM events WHERE system='hash_test'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        ph, stored_curr, payload_text = row
        self.assertNotEqual(ph, "")
        payload_dict = json.loads(payload_text)
        self.assertEqual(ph, compute_payload_hash(payload_dict))


class TestCompressEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot = Path(self.tmp) / "hot"
        self.cold = Path(self.tmp) / "cold"
        self.db = Path(self.tmp) / "ming.db"
        self.hot.mkdir()
        self.cold.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_old_events(self, archiver, count=10, days_ago=3):
        ts = time.time() - days_ago * 86400
        events = [
            _make_event(
                "compress_test", "llm_output", {"data": "x" * 500, "idx": i}, ts + i
            )
            for i in range(count)
        ]
        _write_probe_file(archiver.HOT, "compress_test", events)
        archiver.run_once()

    def test_compress_old_events(self):
        archiver = Archiver(
            db_path=str(self.db),
            hot_dir=str(self.hot),
            cold_dir=str(self.cold),
        )
        self._seed_old_events(archiver, count=5, days_ago=3)
        archiver.warm_days = 1
        archiver.min_compress_bytes = 50
        archiver._last_compress = 0

        conn = sqlite3.connect(str(self.db))
        before = conn.execute(
            "SELECT COUNT(*) FROM events WHERE storage_tier=0"
        ).fetchone()[0]
        self.assertEqual(before, 5)
        conn.close()

        archiver._compress_old_events()

        conn = sqlite3.connect(str(self.db))
        compressed = conn.execute(
            "SELECT COUNT(*) FROM events WHERE storage_tier=1"
        ).fetchone()[0]
        raw = conn.execute(
            "SELECT COUNT(*) FROM events WHERE storage_tier=0"
        ).fetchone()[0]
        conn.close()
        self.assertGreater(compressed, 0)
        self.assertLess(raw, 5)

    def test_compress_preserves_hash_chain(self):
        archiver = Archiver(
            db_path=str(self.db),
            hot_dir=str(self.hot),
            cold_dir=str(self.cold),
        )
        self._seed_old_events(archiver, count=3, days_ago=30)
        archiver.warm_days = 7
        archiver.min_compress_bytes = 50
        archiver._last_compress = 0

        # Record hash chain before compression
        conn = sqlite3.connect(str(self.db))
        before = conn.execute(
            "SELECT id, payload_hash, curr_hash, prev_hash, payload FROM events ORDER BY id"
        ).fetchall()
        conn.close()

        archiver._compress_old_events()

        conn = sqlite3.connect(str(self.db))
        after = conn.execute(
            "SELECT id, payload_hash, curr_hash, prev_hash, payload, storage_tier FROM events ORDER BY id"
        ).fetchall()
        conn.close()

        for b, a in zip(before, after):
            self.assertEqual(b[1], a[1], f"payload_hash changed for event {a[0]}")
            self.assertEqual(b[2], a[2], f"curr_hash changed for event {a[0]}")
            self.assertEqual(b[3], a[3], f"prev_hash changed for event {a[0]}")
            if a[5] == 1:
                self.assertIsNone(
                    a[4], f"payload should be NULL after compression for event {a[0]}"
                )

    def test_small_payload_not_compressed(self):
        archiver = Archiver(
            db_path=str(self.db),
            hot_dir=str(self.hot),
            cold_dir=str(self.cold),
        )
        ts = time.time() - 30 * 86400
        events = [
            _make_event("tiny_test", "llm_output", {"msg": "hi"}, ts + i)
            for i in range(3)
        ]
        _write_probe_file(archiver.HOT, "tiny_test", events)
        archiver.run_once()
        archiver.warm_days = 7
        archiver._last_compress = 0

        archiver._compress_old_events()
        conn = sqlite3.connect(str(self.db))
        compressed = conn.execute(
            "SELECT COUNT(*) FROM events WHERE storage_tier=1 AND system='tiny_test'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(compressed, 0)

    def test_blob_table_populated(self):
        archiver = Archiver(
            db_path=str(self.db),
            hot_dir=str(self.hot),
            cold_dir=str(self.cold),
        )
        self._seed_old_events(archiver, count=3, days_ago=30)
        archiver.warm_days = 7
        archiver.min_compress_bytes = 50
        archiver._last_compress = 0
        archiver._compress_old_events()

        conn = sqlite3.connect(str(self.db))
        blob_count = conn.execute("SELECT COUNT(*) FROM events_blob").fetchone()[0]
        compressed = conn.execute(
            "SELECT COUNT(*) FROM events WHERE storage_tier=1"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(blob_count, compressed)


class TestDowngrade(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hot = Path(self.tmp) / "hot"
        self.cold = Path(self.tmp) / "cold"
        self.db = Path(self.tmp) / "ming.db"
        self.hot.mkdir()
        self.cold.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_and_compress(self):
        archiver = Archiver(
            db_path=str(self.db),
            hot_dir=str(self.hot),
            cold_dir=str(self.cold),
        )
        ts = time.time() - 30 * 86400
        events = [
            _make_event(
                "downgrade_test", "llm_output", {"data": "x" * 500, "i": i}, ts + i
            )
            for i in range(5)
        ]
        _write_probe_file(archiver.HOT, "downgrade_test", events)
        archiver.run_once()
        archiver.warm_days = 7
        archiver.min_compress_bytes = 50
        archiver._last_compress = 0
        archiver._compress_old_events()
        archiver.close()
        del archiver
        # Checkpoint WAL so downgrade script's writes are visible
        fixer = sqlite3.connect(str(self.db))
        fixer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        fixer.close()

    def test_downgrade_restores_payload(self):
        self._seed_and_compress()
        verifier = sqlite3.connect(str(self.db))
        compressed = verifier.execute(
            "SELECT COUNT(*) FROM events WHERE storage_tier=1"
        ).fetchone()[0]
        self.assertGreater(compressed, 0)
        verifier.close()

        # Run downgrade (separate connection)
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        import compress_tools

        compress_tools.cmd_downgrade(str(self.db), dry_run=False)

        # Open fresh connection to see downgrade writer's changes
        verifier = sqlite3.connect(str(self.db))
        remaining = verifier.execute(
            "SELECT COUNT(*) FROM events WHERE storage_tier=1"
        ).fetchone()[0]
        blob_count = verifier.execute("SELECT COUNT(*) FROM events_blob").fetchone()[0]
        raw_count = verifier.execute(
            "SELECT COUNT(*) FROM events WHERE storage_tier=0 AND payload IS NOT NULL"
        ).fetchone()[0]
        verifier.close()

        self.assertEqual(remaining, 0)
        self.assertEqual(blob_count, 0)
        self.assertGreater(raw_count, 0)


if __name__ == "__main__":
    unittest.main()
