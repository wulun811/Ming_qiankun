#!/usr/bin/env python3
# probe_opencode_wrapper.py v0.5 —— 乾坤镜 opencode 包装器探针
# 只读 opencode.db，输出标准热轨。包装器模式（默认）。
# 铁律：只读不写库、只写 JSONL、永不阻塞业务、零第三方依赖
# 用法：python probe_opencode_wrapper.py [opencode args...]

import json, os, signal, sqlite3, subprocess, sys, time, threading, hashlib
from pathlib import Path

DB_PATH = Path.home() / ".local/share/opencode/opencode.db"
HOT_DIR = Path.home() / ".ming" / "hot"
CURSOR_FILE = Path.home() / ".ming" / ".opencode_cursor"
LAMPORT_FILE = Path.home() / ".ming" / ".lamport_clock"
POLL_INTERVAL = 3
TOUCH_INTERVAL = 30
HEALTH_INTERVAL = 60
SCHEMA_VERSION = "0.11.9m"

_OPCODE_DB_SCHEMA_CHECKED = False

_lamport = 0
_lamport_lock = threading.Lock()


def _next_lamport():
    global _lamport
    with _lamport_lock:
        _lamport += 1
        return _lamport


def _init_lamport():
    global _lamport
    LAMPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        if LAMPORT_FILE.exists():
            _lamport = int(LAMPORT_FILE.read_text().strip())
    except (ValueError, OSError):
        _lamport = 0


def _persist_lamport():
    try:
        LAMPORT_FILE.write_text(str(_lamport))
    except OSError:
        pass


_emit_count = 0
_drop_count = 0


def emit(event_type, payload, ts=None):
    global _emit_count, _drop_count
    HOT_DIR.mkdir(parents=True, exist_ok=True)
    if ts is not None and ts > 1e12:
        ts = ts / 1000.0
    ev = {
        "system": "opencode",
        "mode": "black",
        "event_type": event_type,
        "payload": payload,
        "timestamp": ts if ts else time.time(),
        "monotonic_ms": time.monotonic() * 1000,
        "lamport": _next_lamport(),
        "_pid": os.getpid(),
        "_schema_version": SCHEMA_VERSION,
    }
    line = json.dumps(ev, ensure_ascii=False) + "\n"
    hot_file = (
        HOT_DIR / f"opencode_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}.jsonl"
    )
    try:
        with open(hot_file, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        _emit_count += 1
    except OSError as e:
        print(f"[MING-DROP] {e}", file=sys.stderr)
        _drop_count += 1


def load_cursor():
    default = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
    if not CURSOR_FILE.exists():
        return default
    try:
        return json.loads(CURSOR_FILE.read_text())
    except Exception:
        return default


def save_cursor(c):
    CURSOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(c))


def connect_db():
    global _OPCODE_DB_SCHEMA_CHECKED
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{DB_PATH}?mode=ro", uri=True, timeout=1, check_same_thread=False
        )
        conn.execute("PRAGMA query_only = ON")
        if not _OPCODE_DB_SCHEMA_CHECKED:
            _check_opencode_schema(conn)
            _OPCODE_DB_SCHEMA_CHECKED = True
        return conn
    except sqlite3.Error:
        return None


def _check_opencode_schema(conn):
    expected = {
        "message": {"id", "session_id", "data", "time_created"},
        "part": {"id", "session_id", "data", "time_created"},
    }
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('message', 'part')"
        )
        found = {row[0] for row in cursor.fetchall()}
        missing_tables = set(expected) - found
        if missing_tables:
            print(
                f"[MING-WARN] opencode DB missing tables: {missing_tables}. "
                f"This probe requires OpenCode schema v1.x (tested up to v1.3.13). "
                f"Upgrade opencode may have changed the schema.",
                file=sys.stderr,
            )
            return
        for tbl, expected_cols in expected.items():
            col_info = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
            actual_cols = {row[1] for row in col_info}
            missing_cols = expected_cols - actual_cols
            if missing_cols:
                print(
                    f"[MING-WARN] opencode DB table '{tbl}' missing columns: {missing_cols}. "
                    f"OpenCode upgrade may have changed schema (tested up to v1.3.13). "
                    f"Probe will likely produce no data.",
                    file=sys.stderr,
                )
    except sqlite3.Error as e:
        print(
            f"[MING-WARN] Could not verify opencode DB schema: {e}",
            file=sys.stderr,
        )


def _get_content_max_len():
    """Read MING_CONTENT_MAX_LEN env var. 0=audit(no truncate), default=2000."""
    val = os.environ.get("MING_CONTENT_MAX_LEN")
    if val is None:
        return 2000
    try:
        v = int(val)
        return 0 if v == 0 else v
    except ValueError:
        return 2000


def truncate(s, max_len=None):
    if max_len is None:
        max_len = _get_content_max_len()
    if max_len == 0:
        return s
    if s and len(s) > max_len:
        return s[:max_len] + f"...[truncated {len(s) - max_len} chars]"
    return s


def poll_messages(conn, cursor, limit=500):
    c = cursor["message"]
    sql = """SELECT id, session_id, data, time_created FROM message
             WHERE (time_created > ?) OR (time_created = ? AND id > ?)
             ORDER BY time_created, id LIMIT ?"""
    rows = conn.execute(sql, (c["ts"], c["ts"], c["id"], limit)).fetchall()
    events = []
    for rid, sid, data_json, ts in rows:
        try:
            d = json.loads(data_json)
        except json.JSONDecodeError:
            continue
        if d.get("role") != "assistant":
            continue
        time_info = d.get("time", {}) or {}
        latency_ms = None
        if "created" in time_info and "completed" in time_info:
            latency_ms = time_info["completed"] - time_info["created"]
        tokens = d.get("tokens", {}) or {}
        event = {
            "layer_agent": {
                "step_id": rid,
                "session_id": sid,
                "agent_name": d.get("agent", "opencode"),
            },
            "layer_llm": {
                "model": d.get("modelID"),
                "provider": d.get("providerID"),
                "input_tokens": tokens.get("input"),
                "output_tokens": tokens.get("output"),
                "reasoning_tokens": tokens.get("reasoning"),
                "finish_reason": d.get("finish"),
                "cost_usd": d.get("cost"),
                "latency_ms": latency_ms,
            },
            "layer_network": {"target_host": d.get("providerID", "unknown")},
        }
        events.append(("llm_invoke", event, ts, rid))
    return events


def poll_parts(conn, cursor, limit=500):
    c = cursor["part"]
    sql = """SELECT id, session_id, data, time_created FROM part
             WHERE (time_created > ?) OR (time_created = ? AND id > ?)
             ORDER BY time_created, id LIMIT ?"""
    rows = conn.execute(sql, (c["ts"], c["ts"], c["id"], limit)).fetchall()
    events = []
    for rid, sid, data_json, ts in rows:
        try:
            d = json.loads(data_json)
        except json.JSONDecodeError:
            continue
        ptype = d.get("type", "")

        # 处理 text 类型（LLM 输出文本）
        if ptype == "text":
            text = d.get("text", "")
            if text:
                text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
                event = {
                    "layer_agent": {
                        "step_id": rid,
                        "session_id": sid,
                        "agent_name": "opencode",
                    },
                    "layer_llm": {
                        "output_text": truncate(text),
                        "output_text_hash": text_hash,
                    },
                    "layer_network": {"target_host": "local"},
                }
                events.append(("llm_output", event, ts, rid))

        # 处理 reasoning 类型（LLM 推理过程）
        elif ptype == "reasoning":
            text = d.get("text", "")
            time_info = d.get("time", {}) or {}
            if text:
                text_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
                event = {
                    "layer_agent": {
                        "step_id": rid,
                        "session_id": sid,
                        "agent_name": "opencode",
                    },
                    "layer_llm": {
                        "reasoning_text": truncate(text),
                        "reasoning_text_hash": text_hash,
                        "reasoning_start": time_info.get("start"),
                        "reasoning_end": time_info.get("end"),
                    },
                    "layer_network": {"target_host": "local"},
                }
                events.append(("llm_reasoning", event, ts, rid))

        # 处理 step-start 类型（步骤开始）
        elif ptype == "step-start":
            event = {
                "layer_agent": {
                    "step_id": rid,
                    "session_id": sid,
                    "agent_name": "opencode",
                    "step_status": "start",
                },
                "layer_network": {"target_host": "local"},
            }
            events.append(("agent_step_start", event, ts, rid))

        # 处理 step-finish 类型（步骤结束）
        elif ptype == "step-finish":
            event = {
                "layer_agent": {
                    "step_id": rid,
                    "session_id": sid,
                    "agent_name": "opencode",
                    "step_status": "finish",
                },
                "layer_network": {"target_host": "local"},
            }
            events.append(("agent_step_finish", event, ts, rid))

        # 处理 tool 类型（原有逻辑）
        elif ptype == "tool":
            state = d.get("state", {}) or {}
            meta = state.get("metadata", {}) or {}
            inp = state.get("input", {}) or {}
            status = state.get("status", "")
            tool_name = d.get("tool", "unknown")
            tool_args = {}
            for k in ("command", "content", "path"):
                if k in inp:
                    tool_args[k] = inp[k]
                    break
            tool_output = meta.get("output") or state.get("output") or ""
            if status == "error":
                event = {
                    "layer_agent": {
                        "step_id": rid,
                        "session_id": sid,
                        "agent_name": "opencode",
                    },
                    "layer_tool": {
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "tool_result": truncate(tool_output),
                        "tool_status": "error",
                    },
                    "layer_network": {"target_host": "local"},
                }
                events.append(("error", event, ts, rid))
            else:
                exit_code = meta.get("exit")
                success = status in ("completed", "success")
                event = {
                    "layer_agent": {
                        "step_id": rid,
                        "session_id": sid,
                        "agent_name": "opencode",
                    },
                    "layer_tool": {
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "tool_result": truncate(tool_output),
                        "exit_code": exit_code,
                        "tool_status": "success" if success else status,
                    },
                    "layer_network": {"target_host": "local"},
                }
                events.append(("tool_call", event, ts, rid))
    return events


def _poll_and_emit(conn, cursor):
    msgs = poll_messages(conn, cursor)
    for event_type, payload, ts, rid in msgs:
        emit(event_type, payload, ts)
        cursor["message"] = {"ts": ts, "id": rid}
    parts = poll_parts(conn, cursor)
    for event_type, payload, ts, rid in parts:
        emit(event_type, payload, ts)
        cursor["part"] = {"ts": ts, "id": rid}
    if msgs or parts:
        save_cursor(cursor)


def _emit_health(wrapper_pid):
    global _emit_count, _drop_count
    try:
        stat = os.statvfs(str(HOT_DIR))
        disk_free_mb = round((stat.f_bavail * stat.f_frsize) / (1024 * 1024), 1)
    except OSError:
        disk_free_mb = -1
    emit(
        "__health__",
        {
            "emit_success_count_1m": _emit_count,
            "emit_drop_count_1m": _drop_count,
            "disk_free_mb": disk_free_mb,
            "last_errors": [],
        },
    )
    _emit_count = 0
    _drop_count = 0


def main():
    _init_lamport()
    if len(sys.argv) < 2:
        print(
            "Usage: python probe_opencode_wrapper.py [opencode args...]",
            file=sys.stderr,
        )
        sys.exit(1)

    cmd = subprocess.Popen(
        [sys.argv[1]] + sys.argv[2:],
        env=os.environ.copy(),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    child_pid, wrapper_pid = cmd.pid, os.getpid()

    emit(
        "__register__",
        {
            "pid": child_pid,
            "mode": "black",
            "schema_version": SCHEMA_VERSION,
            "wrapper_pid": wrapper_pid,
            "registered_at": time.time(),
        },
    )

    cursor = load_cursor()
    running = True
    last_touch = last_health = 0

    def on_exit(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, on_exit)
    signal.signal(signal.SIGTERM, on_exit)
    print(
        f"[MING] Watching opencode (pid={child_pid}) via DB polling...", file=sys.stderr
    )

    try:
        while running and cmd.poll() is None:
            conn = connect_db()
            if not conn:
                time.sleep(POLL_INTERVAL)
                continue
            try:
                _poll_and_emit(conn, cursor)
            except sqlite3.Error as e:
                emit(
                    "error",
                    {
                        "layer_agent": {"agent_name": "opencode", "source": "probe"},
                        "error_message": f"DB poll failed: {e}",
                    },
                )
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
            time.sleep(POLL_INTERVAL)
            now = time.time()
            if now - last_touch >= TOUCH_INTERVAL:
                emit("__touch__", {"pid": wrapper_pid})
                last_touch = now
            if now - last_health >= HEALTH_INTERVAL:
                _emit_health(wrapper_pid)
                last_health = now
    finally:
        conn = connect_db()
        if conn:
            try:
                _poll_and_emit(conn, cursor)
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        cmd.terminate()
        try:
            cmd.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cmd.kill()
        _persist_lamport()
        print(f"[MING] Session ended. Events written to {HOT_DIR}", file=sys.stderr)


if __name__ == "__main__":
    main()
