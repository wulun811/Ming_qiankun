# probe_integrity.py —— 0.11.9m 探针完整性校验
# 职责：验证热轨事件 curr_hash 和 SQLite 哈希链连续性
# 依赖：hashlib, json, sqlite3, pathlib

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Optional


class ProbeIntegrity:
    def __init__(self, db_path: Optional[Path] = None, hot_dir: Optional[Path] = None):
        if db_path is None:
            db_path = Path.home() / ".ming" / "ming.db"
        if hot_dir is None:
            hot_dir = Path.home() / ".ming" / "hot"
        self.db_path = Path(db_path)
        self.hot_dir = Path(hot_dir)

    def verify_curr_hash(self, filepath: Path) -> list:
        results = []
        prev_hash = "0" * 64
        with open(filepath, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    results.append(
                        {
                            "line_no": line_no,
                            "valid": False,
                            "expected": None,
                            "actual": None,
                        }
                    )
                    continue

                content = json.dumps(
                    {
                        "s": event.get("system", ""),
                        "t": event.get("event_type", ""),
                        "p": event.get("payload", {}),
                    },
                    sort_keys=True,
                )
                expected = hashlib.sha256(f"{prev_hash}{content}".encode()).hexdigest()
                actual = event.get("curr_hash", "")
                results.append(
                    {
                        "line_no": line_no,
                        "valid": expected == actual,
                        "expected": expected,
                        "actual": actual,
                    }
                )
                prev_hash = actual
        return results

    def verify_hash_chain(self, system: Optional[str] = None) -> dict:
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
        query = "SELECT id, system, prev_hash, curr_hash, integrity FROM events"
        params = []
        if system:
            query += " WHERE system = ?"
            params.append(system)
        query += " ORDER BY id ASC"

        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()

        total = len(rows)
        valid = 0
        broken = 0
        broken_ids = []
        rebooted_count = 0
        rebooted_ids = []

        for i, (eid, esys, prev_hash, curr_hash, integrity) in enumerate(rows):
            if integrity == "rebooted":
                rebooted_count += 1
                rebooted_ids.append(eid)
                valid += 1
                prev_hash = curr_hash
                continue

            if integrity == "corrupted":
                broken += 1
                broken_ids.append(eid)
                prev_hash = curr_hash
                continue

            if i > 0 and prev_hash != "0" * 64:
                expected = prev_hash
                if expected == curr_hash:
                    valid += 1
                else:
                    broken += 1
                    broken_ids.append(eid)
            else:
                valid += 1

            prev_hash = curr_hash

        return {
            "total": total,
            "valid": valid,
            "broken": broken,
            "broken_ids": broken_ids,
            "rebooted_count": rebooted_count,
            "rebooted_ids": rebooted_ids,
        }
