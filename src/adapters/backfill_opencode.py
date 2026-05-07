#!/usr/bin/env python3
# backfill_opencode.py —— 乾坤镜 opencode 回溯模式
# 一次性归档历史数据后退出
# 用法：./ming-run opencode --backfill
# 优化：批量写入临时 JSONL，最后一次性 fsync（3.6 万条从 75min → 1-2min）

import json
import os
import sys
import time
from pathlib import Path

from adapters.probe_opencode_wrapper import (
    connect_db,
    poll_messages,
    poll_parts,
    load_cursor,
    save_cursor,
    _init_lamport,
    _persist_lamport,
    _next_lamport,
    CURSOR_FILE,
    DB_PATH,
    HOT_DIR,
    SCHEMA_VERSION,
)


def _build_event(event_type, payload, timestamp=None):
    """构建事件 JSON 行（不写文件）"""
    ev = {
        "system": "opencode",
        "mode": "black",
        "event_type": event_type,
        "payload": payload,
        "timestamp": timestamp if timestamp is not None else time.time(),
        "monotonic_ms": time.monotonic() * 1000,
        "lamport": _next_lamport(),
        "_pid": os.getpid(),
        "_schema_version": SCHEMA_VERSION,
    }
    return json.dumps(ev, ensure_ascii=False) + "\n"


def main():
    _init_lamport()

    if not DB_PATH.exists():
        print(f"[MING] 数据库不存在: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = connect_db()
    if not conn:
        print("[MING] 无法连接数据库", file=sys.stderr)
        sys.exit(1)

    # 注册事件直接写入（只有 1 条，不影响性能）
    HOT_DIR.mkdir(parents=True, exist_ok=True)
    reg_file = (
        HOT_DIR / f"opencode_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_reg.jsonl"
    )
    with open(reg_file, "w", encoding="utf-8") as f:
        f.write(
            _build_event(
                "__register__",
                {
                    "pid": os.getpid(),
                    "mode": "black",
                    "schema_version": SCHEMA_VERSION,
                    "backfill": True,
                    "registered_at": time.time(),
                },
            )
        )
        f.flush()
        os.fsync(f.fileno())

    cursor = load_cursor()
    total = 0
    batch_size = 2000
    start_time = time.time()

    # 批量写入：收集到 buffer，最后一次性 fsync
    buffer = []
    buffer_size = 10000  # 每 1 万条写一次磁盘

    print(
        f"[MING] 开始回溯历史数据 (batch={batch_size}, buffer={buffer_size})...",
        file=sys.stderr,
    )

    def flush_buffer():
        nonlocal buffer
        if not buffer:
            return
        ts_str = time.strftime("%Y%m%d_%H%M%S")
        hot_file = HOT_DIR / f"opencode_{ts_str}_{os.getpid()}_batch.jsonl"
        with open(hot_file, "a", encoding="utf-8") as f:
            f.writelines(buffer)
            f.flush()
            os.fsync(f.fileno())
        buffer = []

    try:
        while True:
            msgs = poll_messages(conn, cursor, limit=batch_size)
            for event_type, payload, ts, rid in msgs:
                buffer.append(_build_event(event_type, payload, ts))
                cursor["message"] = {"ts": ts, "id": rid}
                total += 1

                if len(buffer) >= buffer_size:
                    flush_buffer()

            parts = poll_parts(conn, cursor, limit=batch_size)
            for event_type, payload, ts, rid in parts:
                buffer.append(_build_event(event_type, payload, ts))
                cursor["part"] = {"ts": ts, "id": rid}
                total += 1

                if len(buffer) >= buffer_size:
                    flush_buffer()

            if msgs or parts:
                save_cursor(cursor)
                elapsed = time.time() - start_time
                rate = total / elapsed if elapsed > 0 else 0
                print(
                    f"[MING] 已处理 {total} 条事件 ({rate:.0f} events/sec)...",
                    file=sys.stderr,
                )

            if not msgs and not parts:
                break

        # 最后刷新剩余 buffer
        flush_buffer()

    except KeyboardInterrupt:
        print("\n[MING] 用户中断", file=sys.stderr)
        flush_buffer()  # 确保不丢失数据
    finally:
        conn.close()
        _persist_lamport()
        elapsed = time.time() - start_time
        print(f"[MING] 回溯完成: {total} 条事件, 耗时 {elapsed:.1f}s", file=sys.stderr)

        # 补发 __health__ 事件，确保 probe_health 表有记录（前端心跳显示依赖）
        try:
            stat = os.statvfs(str(HOT_DIR))
            disk_free_mb = round((stat.f_bavail * stat.f_frsize) / (1024 * 1024), 1)
        except OSError:
            disk_free_mb = -1

        health_event = _build_event(
            "__health__",
            {
                "emit_success_count_1m": total,
                "emit_drop_count_1m": 0,
                "disk_free_mb": disk_free_mb,
                "last_errors": [],
                "backfill": True,
            },
        )
        ts_str = time.strftime("%Y%m%d_%H%M%S")
        health_file = HOT_DIR / f"opencode_{ts_str}_{os.getpid()}_health.jsonl"
        try:
            with open(health_file, "w", encoding="utf-8") as f:
                f.write(health_event)
                f.flush()
                os.fsync(f.fileno())
            print(f"[MING] 健康事件已写入: {health_file.name}", file=sys.stderr)
        except OSError as e:
            print(f"[MING] 健康事件写入失败: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
