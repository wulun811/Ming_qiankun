#!/usr/bin/env python3
"""compress_tools.py —— v0.11.10 三级存储压缩运维工具

子命令:
  verify     验证存量 payload_hash 正确性 (--fix 可修复)
  backfill   回填 missing payload_hash
  downgrade  将压缩事件解压回明文 (降级到 v0.11.9)
"""

import json, hashlib, zlib, sqlite3, sys, time
from pathlib import Path

BATCH = 500
DB = Path.home() / ".ming" / "ming.db"


def _compute_ph(payload_text):
    try:
        d = json.loads(payload_text)
        text = json.dumps(d, ensure_ascii=False, sort_keys=True)
    except (json.JSONDecodeError, TypeError):
        text = payload_text
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cmd_verify(db_path, fix):
    db = Path(db_path) if db_path else DB
    if not db.exists():
        print(f"DB not found: {db}")
        return

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA busy_timeout=10000")
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    print(f"Total events: {total}")

    bad_json = ph_missing = ph_wrong = fixed = 0
    offset = 0
    start = time.time()

    while offset < total:
        rows = conn.execute(
            "SELECT id, payload, payload_hash FROM events LIMIT ? OFFSET ?",
            (BATCH, offset),
        ).fetchall()
        for r in rows:
            ev_id = r["id"]
            payload_text = r["payload"]
            stored_ph = r["payload_hash"]
            if not payload_text:
                continue
            try:
                json.loads(payload_text)
            except (json.JSONDecodeError, TypeError):
                bad_json += 1
                print(f"  [BAD JSON] event {ev_id}")
                continue
            computed_ph = _compute_ph(payload_text)
            if not stored_ph:
                ph_missing += 1
            elif stored_ph != computed_ph:
                ph_wrong += 1
                print(f"  [HASH MISMATCH] event {ev_id}: stored={stored_ph[:16]}...")
        offset += BATCH
        if offset % 5000 == 0:
            rate = offset / (time.time() - start) if (time.time() - start) > 0 else 0
            print(
                f"  Progress: {offset}/{total} ({offset * 100 // total}%)  {rate:.0f} rows/s"
            )

    conn.close()

    if fix and (ph_missing > 0 or ph_wrong > 0):
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA busy_timeout=10000")
        offset = 0
        while offset < total:
            rows = conn.execute(
                "SELECT id, payload FROM events WHERE payload IS NOT NULL AND "
                "(payload_hash = '' OR payload_hash IS NULL) LIMIT ? OFFSET ?",
                (BATCH, offset),
            ).fetchall()
            if not rows:
                break
            conn.execute("BEGIN IMMEDIATE")
            for ev_id, payload_text in rows:
                ph = _compute_ph(payload_text)
                conn.execute(
                    "UPDATE events SET payload_hash = ? WHERE id = ?", (ph, ev_id)
                )
                fixed += 1
            conn.commit()
            offset += BATCH
        conn.close()
        print(f"  Fixed payload_hash:   {fixed}")
    elif fix:
        print("  (no fixes needed)")

    elapsed = time.time() - start
    print(f"\nResults ({elapsed:.1f}s):")
    print(f"  Total events:         {total}")
    print(f"  Bad JSON payloads:    {bad_json}")
    print(f"  Missing payload_hash: {ph_missing}")
    print(f"  Wrong payload_hash:   {ph_wrong}")


def cmd_backfill(db_path, dry_run):
    db = Path(db_path) if db_path else DB
    if not db.exists():
        print(f"DB not found: {db}")
        return

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA busy_timeout=10000")
    total = conn.execute(
        "SELECT COUNT(*) FROM events WHERE payload_hash = '' AND payload IS NOT NULL"
    ).fetchone()[0]
    if total == 0:
        print("All payload_hash already populated.")
        conn.close()
        return

    print(f"Found {total} rows needing backfill...")
    offset = done = 0
    start = time.time()

    while offset < total:
        rows = conn.execute(
            "SELECT id, payload FROM events WHERE payload_hash = '' AND payload IS NOT NULL LIMIT ? OFFSET ?",
            (BATCH, offset),
        ).fetchall()
        if not rows:
            break
        if dry_run:
            done += len(rows)
            offset += BATCH
            continue
        conn.execute("BEGIN IMMEDIATE")
        for ev_id, payload_text in rows:
            ph = _compute_ph(payload_text)
            conn.execute("UPDATE events SET payload_hash = ? WHERE id = ?", (ph, ev_id))
        conn.commit()
        done += len(rows)
        offset += BATCH
        rate = done / (time.time() - start) if (time.time() - start) > 0 else 0
        print(f"  Progress: {done}/{total} ({done * 100 // total}%)  {rate:.0f} rows/s")

    conn.close()
    print(f"Done. {done} rows backfilled in {time.time() - start:.1f}s.")


def cmd_downgrade(db_path, dry_run):
    db = Path(db_path) if db_path else DB
    if not db.exists():
        print(f"DB not found: {db}")
        return

    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode = DELETE")

    total = conn.execute(
        "SELECT COUNT(*) FROM events WHERE storage_tier = 1"
    ).fetchone()[0]
    if total == 0:
        print("No compressed events found.")
        conn.close()
        return

    print(
        f"Found {total} compressed events. {'[DRY RUN]' if dry_run else 'Downgrading...'}"
    )
    offset = done = 0
    start = time.time()

    while offset < total:
        rows = conn.execute(
            """SELECT e.id, COALESCE(b.payload_blob, e.payload_blob) as blob
               FROM events e LEFT JOIN events_blob b ON e.id = b.event_id
               WHERE e.storage_tier = 1 LIMIT ? OFFSET ?""",
            (BATCH, offset),
        ).fetchall()
        if not rows:
            break
        if dry_run:
            done += len(rows)
            offset += BATCH
            continue
        conn.execute("BEGIN IMMEDIATE")
        for ev_id, blob in rows:
            if not blob:
                conn.execute(
                    "UPDATE events SET storage_tier = 2 WHERE id = ?", (ev_id,)
                )
                print(f"  WARNING: event {ev_id} has no blob, marked failed")
                continue
            try:
                payload_text = zlib.decompress(blob).decode("utf-8")
            except Exception as e:
                conn.execute(
                    "UPDATE events SET storage_tier = 2 WHERE id = ?", (ev_id,)
                )
                print(f"  WARNING: event {ev_id} decompress failed: {e}")
                continue
            conn.execute(
                "UPDATE events SET payload = ?, payload_blob = NULL, storage_tier = 0 WHERE id = ?",
                (payload_text, ev_id),
            )
        conn.commit()
        done += len(rows)
        offset += BATCH
        rate = done / (time.time() - start) if (time.time() - start) > 0 else 0
        print(f"  Progress: {done}/{total} ({done * 100 // total}%)  {rate:.0f} rows/s")

    if not dry_run:
        bc = conn.execute("SELECT COUNT(*) FROM events_blob").fetchone()[0]
        if bc > 0:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM events_blob")
            conn.execute("COMMIT")
            print(f"  Cleared events_blob table ({bc} rows)")
    conn.close()
    print(f"Done. {done} events downgraded in {time.time() - start:.1f}s.")
    if dry_run:
        print("Run without --dry-run to apply.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            f"Usage: {sys.argv[0]} {{verify|backfill|downgrade}} [--fix] [--dry-run] [--db PATH]"
        )
        sys.exit(1)

    cmd = sys.argv[1]
    fix = "--fix" in sys.argv
    dry = "--dry-run" in sys.argv
    path = None
    for i, a in enumerate(sys.argv):
        if a == "--db" and i + 1 < len(sys.argv):
            path = sys.argv[i + 1]

    try:
        if cmd == "verify":
            cmd_verify(path, fix)
        elif cmd == "backfill":
            cmd_backfill(path, dry)
        elif cmd == "downgrade":
            cmd_downgrade(path, dry)
        else:
            print(f"Unknown command: {cmd}")
            print("Available: verify, backfill, downgrade")
            sys.exit(1)
    except sqlite3.OperationalError as e:
        print(f"Error: {e}")
        print("Hint: ensure this DB is v0.11.10+ (run archiver once to migrate schema)")
        sys.exit(1)
