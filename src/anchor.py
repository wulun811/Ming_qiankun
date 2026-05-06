# anchor.py —— 0.11.9m 审计锚：哈希链导出 + 验证
# 职责：维护诊断决策的 SHA-256 哈希链，支持导出和验证
# 依赖：hashlib, json, sqlite3

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Optional
from archiver_compress import resolve_payload, compute_payload_hash


class Anchor:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path.home() / ".ming" / "ming.db"
        self.db_path = Path(db_path)

    def verify_chain(self, table: str = "events") -> dict:
        if not self.db_path.exists():
            return {
                "total": 0,
                "valid": 0,
                "broken": 0,
                "broken_ids": [],
                "rebooted_count": 0,
                "rebooted_ids": [],
            }

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, prev_hash, curr_hash, integrity FROM {table} ORDER BY id ASC"
        )
        rows = cur.fetchall()
        conn.close()

        total = len(rows)
        valid = 0
        broken = 0
        broken_ids = []
        rebooted_count = 0
        rebooted_ids = []

        for eid, prev_hash, curr_hash, integrity in rows:
            if integrity == "rebooted":
                rebooted_count += 1
                rebooted_ids.append(eid)
                valid += 1
            elif integrity == "corrupted":
                broken += 1
                broken_ids.append(eid)
            else:
                valid += 1

        return {
            "total": total,
            "valid": valid,
            "broken": broken,
            "broken_ids": broken_ids,
            "rebooted_count": rebooted_count,
            "rebooted_ids": rebooted_ids,
        }

    def export_weekly(self, output_dir: Path) -> list:
        if not self.db_path.exists():
            return []

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT e.id, e.system, e.event_type, e.payload, "
            "  e.storage_tier, b.payload_blob, "
            "  e.prev_hash, e.curr_hash, e.timestamp "
            "FROM events e LEFT JOIN events_blob b ON e.id = b.event_id "
            "ORDER BY e.id ASC"
        ).fetchall()
        conn.close()

        filepath = output_dir / "weekly_export.jsonl"
        with open(filepath, "w", encoding="utf-8") as f:
            for row in rows:
                d = dict(row)
                pl = resolve_payload(d)
                record = {
                    "id": d["id"],
                    "system": d["system"],
                    "event_type": d["event_type"],
                    "payload": pl,
                    "prev_hash": d["prev_hash"],
                    "curr_hash": d["curr_hash"],
                    "timestamp": d["timestamp"],
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return [str(filepath)]

    def verify_export(self, filepath: Path) -> bool:
        filepath = Path(filepath)
        if not filepath.exists():
            return False

        prev_hash = "0" * 64
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                record = json.loads(line.strip())
                payload_hash = compute_payload_hash(record.get("payload", {}))
                content = json.dumps(
                    {
                        "system": record["system"],
                        "event_type": record["event_type"],
                        "payload_hash": payload_hash,
                        "timestamp": record.get("timestamp", 0),
                    },
                    sort_keys=True,
                )
                expected = hashlib.sha256(f"{prev_hash}{content}".encode()).hexdigest()
                if record.get("curr_hash") != expected:
                    return False
                prev_hash = record["curr_hash"]
        return True
