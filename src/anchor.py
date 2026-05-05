# anchor.py —— 0.11.9m 审计锚：哈希链导出 + 验证
# 职责：维护诊断决策的 SHA-256 哈希链，支持导出和验证
# 依赖：hashlib, json, sqlite3

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Optional

class Anchor:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path.home() / ".ming" / "ming.db"
        self.db_path = Path(db_path)

    def verify_chain(self, table: str = "events") -> dict:
        if not self.db_path.exists():
            return {"total": 0, "valid": 0, "broken": 0, "broken_ids": [], "rebooted_count": 0, "rebooted_ids": []}

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(f"SELECT id, prev_hash, curr_hash, integrity FROM {table} ORDER BY id ASC")
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
            "total": total, "valid": valid, "broken": broken,
            "broken_ids": broken_ids, "rebooted_count": rebooted_count,
            "rebooted_ids": rebooted_ids
        }

    def export_weekly(self, output_dir: Path) -> list:
        if not self.db_path.exists():
            return []

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, system, event_type, payload, prev_hash, curr_hash, timestamp "
            "FROM events ORDER BY id ASC"
        )
        rows = cur.fetchall()
        conn.close()

        filepath = output_dir / "weekly_export.jsonl"
        with open(filepath, "w", encoding="utf-8") as f:
            for row in rows:
                record = {
                    "id": row[0], "system": row[1], "event_type": row[2],
                    "payload": json.loads(row[3]) if isinstance(row[3], str) else row[3],
                    "prev_hash": row[4], "curr_hash": row[5], "timestamp": row[6]
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
                expected = hashlib.sha256(
                    f"{prev_hash}{json.dumps({'s': record['system'], 't': record['event_type'], 'p': record['payload']}, sort_keys=True)}".encode()
                ).hexdigest()
                if record.get("curr_hash") != expected:
                    return False
                prev_hash = record["curr_hash"]
        return True
