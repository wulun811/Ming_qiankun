#!/usr/bin/env python3
# daemon_opencode.py —— 乾坤镜 opencode 守护模式
# 作为独立守护进程持续轮询 opencode.db
# 用法：./ming-run opencode --daemon

import os
import signal
import sys
import time

from adapters.probe_opencode_wrapper import (
    connect_db,
    poll_messages,
    poll_parts,
    load_cursor,
    save_cursor,
    emit,
    _init_lamport,
    _persist_lamport,
    POLL_INTERVAL,
    TOUCH_INTERVAL,
    HEALTH_INTERVAL,
    CURSOR_FILE,
    DB_PATH,
    HOT_DIR,
    SCHEMA_VERSION,
)


def main():
    _init_lamport()

    if not DB_PATH.exists():
        print(f"[MING] 数据库不存在: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    emit(
        "__register__",
        {
            "pid": os.getpid(),
            "mode": "black",
            "schema_version": SCHEMA_VERSION,
            "daemon": True,
            "registered_at": time.time(),
        },
    )

    cursor = load_cursor()
    running = True
    last_touch = 0
    last_health = 0
    daemon_emit_count = 0
    daemon_drop_count = 0

    def on_exit(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, on_exit)
    signal.signal(signal.SIGTERM, on_exit)

    print("[MING] 守护模式启动，持续轮询 opencode.db...", file=sys.stderr)

    try:
        while running:
            conn = connect_db()
            if not conn:
                time.sleep(POLL_INTERVAL)
                continue

            try:
                msgs = poll_messages(conn, cursor)
                for event_type, payload, ts, rid in msgs:
                    emit(event_type, payload, ts)
                    daemon_emit_count += 1
                    cursor["message"] = {"ts": ts, "id": rid}

                parts = poll_parts(conn, cursor)
                for event_type, payload, ts, rid in parts:
                    emit(event_type, payload, ts)
                    daemon_emit_count += 1
                    cursor["part"] = {"ts": ts, "id": rid}

                if msgs or parts:
                    save_cursor(cursor)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

            time.sleep(POLL_INTERVAL)

            now = time.time()
            if now - last_touch >= TOUCH_INTERVAL:
                emit("__touch__", {"pid": os.getpid()})
                last_touch = now

            if now - last_health >= HEALTH_INTERVAL:
                try:
                    stat = os.statvfs(str(HOT_DIR))
                    disk_free_mb = round(
                        (stat.f_bavail * stat.f_frsize) / (1024 * 1024), 1
                    )
                except OSError:
                    disk_free_mb = -1
                emit(
                    "__health__",
                    {
                        "emit_success_count_1m": daemon_emit_count,
                        "emit_drop_count_1m": daemon_drop_count,
                        "disk_free_mb": disk_free_mb,
                        "last_errors": [],
                    },
                )
                daemon_emit_count = 0
                daemon_drop_count = 0
                last_health = now

    except KeyboardInterrupt:
        print("\n[MING] 用户中断", file=sys.stderr)
    finally:
        conn = connect_db()
        if conn:
            try:
                msgs = poll_messages(conn, cursor)
                for event_type, payload, ts, rid in msgs:
                    emit(event_type, payload, ts)
                    cursor["message"] = {"ts": ts, "id": rid}
                parts = poll_parts(conn, cursor)
                for event_type, payload, ts, rid in parts:
                    emit(event_type, payload, ts)
                    cursor["part"] = {"ts": ts, "id": rid}
                save_cursor(cursor)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        _persist_lamport()
        print(f"[MING] 守护进程退出。事件已写入 {HOT_DIR}", file=sys.stderr)


if __name__ == "__main__":
    main()
