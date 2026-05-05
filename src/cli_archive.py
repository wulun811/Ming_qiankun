# cli_archive.py —— 0.11.9m 归档/验真命令
# 职责：cmd_archive / cmd_verify
import json, hashlib, sys, time
from pathlib import Path
import sqlite3


def cmd_archive(args):
    archive_dir = Path.home() / ".ming" / "archive"
    if args.action == "list":
        if not archive_dir.exists():
            print("无归档文件")
            return
        files = sorted(archive_dir.glob("diagnoses_*.jsonl"))
        for f in files:
            size = f.stat().st_size / (1024 * 1024)
            print(f"{f.name:<35} {size:>8.2f} MB")
    elif args.action == "cat":
        if not args.file or ".." in args.file:
            print("错误: 无效的文件名")
            return
        target = (archive_dir / args.file).resolve()
        if not str(target).startswith(str(archive_dir.resolve())):
            print("错误: 文件路径超出归档目录")
            return
        if not target.exists():
            print("File not found")
            return
        print(target.read_text(encoding="utf-8"))
    elif args.action == "verify":
        if not args.file or ".." in args.file:
            print("错误: 无效的文件名")
            return
        target = (archive_dir / args.file).resolve()
        if not str(target).startswith(str(archive_dir.resolve())):
            print("错误: 文件路径超出归档目录")
            return
        if not target.exists():
            print("File not found")
            return
        lines = target.read_text(encoding="utf-8").strip().splitlines()
        ok = 0
        bad = 0
        for line in lines:
            try:
                d = json.loads(line)
                if d.get("evidence_hash"):
                    ok += 1
                else:
                    bad += 1
            except json.JSONDecodeError:
                bad += 1
        print(f"Verified: {ok} OK, {bad} bad ({ok + bad} total)")


def cmd_verify(args):
    """离线证据链验真"""
    DB = Path.home() / ".ming" / "ming.db"
    if not DB.exists():
        print("数据库不存在: {}".format(DB))
        return
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        year = time.strftime("%Y")
        tbl = f"diagnoses_{year}"
        try:
            rows = conn.execute(
                f"SELECT diagnosis_id, evidence, evidence_hash FROM {tbl}"
            ).fetchall()
            ok = 0
            bad = 0
            for diag_id, evidence, stored_hash in rows:
                try:
                    evidence_data = json.loads(evidence)
                except (json.JSONDecodeError, TypeError):
                    bad += 1
                    print(f"PARSE_ERROR: {diag_id}")
                    continue

                computed = hashlib.sha256(
                    json.dumps(evidence_data, sort_keys=True).encode()
                ).hexdigest()[:16]
                if computed == stored_hash:
                    ok += 1
                else:
                    bad += 1
                    print(f"MISMATCH: {diag_id}")
            print(f"校验: {ok} 通过, {bad} 不匹配 (共 {ok + bad} 条)")
        except sqlite3.OperationalError as e:
            print("查询出错: {}".format(e))
    finally:
        conn.close()
