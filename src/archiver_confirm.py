# archiver_confirm.py —— 0.11.9m 诊断确认更新
# 职责：UPDATE diagnoses 表的 status/confirmed_by/confirmed_at 字段
# 安全：不 import sqlite3，不知道 DB 路径，conn 由 archiver.py 传入
import json
from pathlib import Path


def apply_confirmations(conn, tbl, confirmations_path):
    """读取 CLI 提交的确认请求文件，更新诊断状态"""
    path = Path(confirmations_path)
    if not path.exists():
        return 0

    try:
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        if not lines or lines == [""]:
            return 0
    except Exception:
        return 0

    applied = 0
    for line in lines:
        try:
            conf = json.loads(line)
            if conf.get("type") != "confirm":
                continue
            conn.execute(
                f"""
                UPDATE {tbl} SET status = ?, confirmed_by = ?, confirmed_at = ?
                WHERE diagnosis_id = ?
            """,
                (
                    conf.get("verdict"),
                    conf.get("confirmed_by"),
                    conf.get("confirmed_at"),
                    conf.get("diagnosis_id"),
                ),
            )
            applied += 1
        except Exception:
            pass

    if applied > 0:
        conn.commit()
        try:
            path.unlink()
        except Exception:
            pass

    return applied
