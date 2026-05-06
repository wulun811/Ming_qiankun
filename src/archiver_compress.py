# archiver_compress.py —— 0.11.10 压缩/解压/校验模块
# 职责：payload 透明压缩解压、哈希校验、行级 BLOB 存取
# 安全：纯工具函数，不操作 DB 连接，不 import sqlite3
import json, hashlib, zlib


def compress_payload(payload_dict: dict, level=6) -> bytes:
    text = json.dumps(payload_dict, ensure_ascii=False, sort_keys=True)
    return zlib.compress(text.encode("utf-8"), level=level)


def decompress_payload(blob: bytes) -> dict:
    text = zlib.decompress(blob).decode("utf-8")
    return json.loads(text)


def compute_payload_hash(payload_dict: dict) -> str:
    text = json.dumps(payload_dict, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_compress_integrity(payload_dict: dict, blob: bytes) -> bool:
    try:
        decompressed = decompress_payload(blob)
    except Exception:
        return False
    original_hash = compute_payload_hash(payload_dict)
    decompressed_hash = compute_payload_hash(decompressed)
    return original_hash == decompressed_hash


MIN_COMPRESS_BYTES = 256

PAYLOAD_JOIN = "LEFT JOIN events_blob ON events.id = events_blob.event_id"


def resolve_payload(
    row, payload_col="payload", tier_col="storage_tier", blob_col="payload_blob"
):
    d = (
        dict(row)
        if hasattr(row, "keys")
        else {payload_col: row[0], tier_col: row[1], blob_col: row[2]}
    )
    if d.get(tier_col) == 1:
        blob = d.get(blob_col)
        if blob:
            return decompress_payload(blob)
        return None
    raw = d.get(payload_col)
    if raw:
        return json.loads(raw) if isinstance(raw, str) else raw
    return None
