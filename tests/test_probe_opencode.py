# test_probe_opencode.py —— 乾坤镜 opencode 适配器测试

import sys, unittest, json, tempfile, os, time, hashlib, sqlite3, types
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from adapters.probe_opencode_wrapper import (
    _next_lamport,
    _init_lamport,
    _persist_lamport,
    _get_content_max_len,
    truncate,
    load_cursor,
    save_cursor,
    connect_db,
    emit,
    poll_messages,
    poll_parts,
    CURSOR_FILE,
    HOT_DIR,
    LAMPORT_FILE,
    SCHEMA_VERSION,
)


def _create_mock_db(path, messages=None, parts=None):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS message (id TEXT, session_id TEXT, data TEXT, time_created REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS part (id TEXT, session_id TEXT, data TEXT, time_created REAL)"
    )
    if messages:
        for m in messages:
            conn.execute(
                "INSERT INTO message (id, session_id, data, time_created) VALUES (?, ?, ?, ?)",
                (
                    m.get("id", "m1"),
                    m.get("session_id", "s1"),
                    json.dumps(m.get("data", {})),
                    m.get("time_created", 1000.0),
                ),
            )
    if parts:
        for p in parts:
            conn.execute(
                "INSERT INTO part (id, session_id, data, time_created) VALUES (?, ?, ?, ?)",
                (
                    p.get("id", "p1"),
                    p.get("session_id", "s1"),
                    json.dumps(p.get("data", {})),
                    p.get("time_created", 1000.0),
                ),
            )
    conn.commit()
    return conn


class TestLamport(unittest.TestCase):
    def test_init_lamport_reads_existing(self):
        import adapters.probe_opencode_wrapper as w

        with tempfile.TemporaryDirectory() as td:
            lf = Path(td) / ".lamport"
            lf.write_text("42")
            w.LAMPORT_FILE = lf
            _init_lamport()
            self.assertEqual(w._lamport, 42)

    def test_next_lamport_monotonic(self):
        old_lamport = 0
        import adapters.probe_opencode_wrapper as w

        w._lamport = 0
        first = _next_lamport()
        second = _next_lamport()
        self.assertEqual(first, 1)
        self.assertEqual(second, 2)

    def test_persist_lamport(self):
        import adapters.probe_opencode_wrapper as w

        w._lamport = 42
        with tempfile.TemporaryDirectory() as td:
            w.LAMPORT_FILE = Path(td) / ".lamport"
            _persist_lamport()
            content = w.LAMPORT_FILE.read_text()
            self.assertEqual(int(content.strip()), 42)


class TestTruncate(unittest.TestCase):
    def test_truncate_short(self):
        self.assertEqual(truncate("hello"), "hello")

    def test_truncate_long(self):
        s = "x" * 3000
        result = truncate(s, max_len=100)
        self.assertEqual(len(result), 100 + len("...[truncated 2900 chars]"))

    def test_truncate_audit_mode(self):
        s = "x" * 5000
        result = truncate(s, max_len=0)
        self.assertEqual(result, s)

    def test_truncate_max_len_none(self):
        with tempfile.TemporaryDirectory() as td:
            old = os.environ.get("MING_CONTENT_MAX_LEN")
            os.environ["MING_CONTENT_MAX_LEN"] = "50"
            try:
                result = truncate("x" * 100)
                self.assertIn("truncated", result)
            finally:
                if old:
                    os.environ["MING_CONTENT_MAX_LEN"] = old
                else:
                    os.environ.pop("MING_CONTENT_MAX_LEN", None)


class TestCursor(unittest.TestCase):
    def test_load_cursor_missing(self):
        cursor = load_cursor()
        self.assertIn("message", cursor)
        self.assertIn("part", cursor)
        self.assertEqual(cursor["message"]["ts"], 0)

    def test_save_and_load_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            import adapters.probe_opencode_wrapper as w

            w.CURSOR_FILE = Path(td) / ".cursor"
            save_cursor(
                {"message": {"ts": 999, "id": "m100"}, "part": {"ts": 888, "id": "p50"}}
            )
            loaded = load_cursor()
            self.assertEqual(loaded["message"]["ts"], 999)
            self.assertEqual(loaded["part"]["id"], "p50")

    def test_load_corrupted_cursor(self):
        with tempfile.TemporaryDirectory() as td:
            import adapters.probe_opencode_wrapper as w

            w.CURSOR_FILE = Path(td) / ".cursor"
            w.CURSOR_FILE.write_text("not json")
            cursor = load_cursor()
            self.assertEqual(cursor["message"]["ts"], 0)


class TestConnectDB(unittest.TestCase):
    def test_connect_db_no_db(self):
        import adapters.probe_opencode_wrapper as w

        w.DB_PATH = Path("/nonexistent/opencode.db")
        conn = connect_db()
        self.assertIsNone(conn)

    def test_connect_db_valid(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "opencode.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE message (id TEXT)")
            conn.close()
            import adapters.probe_opencode_wrapper as w

            w.DB_PATH = db_path
            conn = connect_db()
            self.assertIsNotNone(conn)
            conn.close()


class TestEmit(unittest.TestCase):
    def test_emit_writes_file(self):
        with tempfile.TemporaryDirectory() as td:
            import adapters.probe_opencode_wrapper as w

            w.HOT_DIR = Path(td)
            emit("llm_invoke", {"model": "gpt-4"})
            files = list(Path(td).glob("*.jsonl"))
            self.assertTrue(len(files) > 0)
            content = Path(files[0]).read_text()
            self.assertIn("llm_invoke", content)
            self.assertIn("opencode", content)
            self.assertIn("gpt-4", content)

    def test_emit_increments_count(self):
        with tempfile.TemporaryDirectory() as td:
            import adapters.probe_opencode_wrapper as w

            w.HOT_DIR = Path(td)
            old_emit = w._emit_count
            emit("test", {"k": "v"})
            self.assertEqual(w._emit_count, old_emit + 1)

    def test_emit_invalid_dir(self):
        import adapters.probe_opencode_wrapper as w

        w.HOT_DIR = Path("/nonexistent_dir_12345")


class TestPollMessages(unittest.TestCase):
    def test_poll_messages_empty(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(db_path)
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_messages(conn, cursor, limit=100)
            self.assertEqual(events, [])

    def test_poll_messages_skip_user(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                messages=[
                    {"id": "m1", "data": {"role": "user", "content": "hi"}},
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_messages(conn, cursor, limit=100)
            self.assertEqual(events, [])

    def test_poll_messages_assistant(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                messages=[
                    {
                        "id": "m1",
                        "session_id": "s1",
                        "data": {
                            "role": "assistant",
                            "modelID": "gpt-4",
                            "providerID": "openai",
                            "tokens": {"input": 100, "output": 50},
                            "time": {"created": 100, "completed": 200},
                            "finish": "stop",
                        },
                        "time_created": 1000,
                    },
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_messages(conn, cursor, limit=100)
            self.assertEqual(len(events), 1)
            et, payload, ts, rid = events[0]
            self.assertEqual(et, "llm_invoke")
            self.assertEqual(payload["layer_llm"]["model"], "gpt-4")
            self.assertEqual(payload["layer_llm"]["latency_ms"], 100)

    def test_poll_messages_invalid_json(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                "CREATE TABLE message (id TEXT, session_id TEXT, data TEXT, time_created REAL)"
            )
            conn.execute(
                "INSERT INTO message VALUES (?, ?, ?, ?)",
                ("m1", "s1", "not json", 1000),
            )
            conn.commit()
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_messages(conn, cursor, limit=100)
            self.assertEqual(events, [])

    def test_poll_messages_cursor_respects_ts(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                messages=[
                    {
                        "id": "m1",
                        "data": {
                            "role": "assistant",
                            "modelID": "gpt-4",
                            "finish": "stop",
                        },
                        "time_created": 100,
                    },
                    {
                        "id": "m2",
                        "data": {
                            "role": "assistant",
                            "modelID": "gpt-4",
                            "finish": "stop",
                        },
                        "time_created": 200,
                    },
                ],
            )
            cursor = {"message": {"ts": 150, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_messages(conn, cursor, limit=100)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0][3], "m2")

    def test_poll_messages_skip_incomplete(self):
        """finish=null 的消息（API 尚未返回）应被跳过，不归档、不推进 cursor"""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                messages=[
                    {
                        "id": "m1",
                        "session_id": "s1",
                        "data": {
                            "role": "assistant",
                            "modelID": "gpt-4",
                            "providerID": "openai",
                            "tokens": {"input": 0, "output": 0},
                            "time": {"created": 100},
                        },
                        "time_created": 1000,
                    },
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_messages(conn, cursor, limit=100)
            self.assertEqual(events, [])

    def test_poll_messages_skip_empty_finish(self):
        """finish='' 的消息应被跳过"""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                messages=[
                    {
                        "id": "m1",
                        "session_id": "s1",
                        "data": {
                            "role": "assistant",
                            "modelID": "gpt-4",
                            "finish": "",
                            "tokens": {"input": 0, "output": 0},
                        },
                        "time_created": 1000,
                    },
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_messages(conn, cursor, limit=100)
            self.assertEqual(events, [])

    def test_poll_messages_incomplete_then_complete(self):
        """竞态场景：先读到 incomplete（跳过），下次轮询读到 complete"""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                messages=[
                    {
                        "id": "m1",
                        "session_id": "s1",
                        "data": {
                            "role": "assistant",
                            "modelID": "gpt-4",
                            "tokens": {"input": 0, "output": 0},
                        },
                        "time_created": 1000,
                    },
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            # 第一轮：finish=null，跳过
            events = poll_messages(conn, cursor, limit=100)
            self.assertEqual(events, [])
            # 模拟 opencode 完成写入（更新 data）
            conn.execute(
                "UPDATE message SET data = ? WHERE id = ?",
                (
                    json.dumps(
                        {
                            "role": "assistant",
                            "modelID": "gpt-4",
                            "finish": "stop",
                            "tokens": {"input": 906, "output": 138},
                            "time": {"created": 1000, "completed": 1500},
                        }
                    ),
                    "m1",
                ),
            )
            conn.commit()
            # 第二轮：cursor 未推进，重新读取同一条消息，现在 finish="stop"，通过
            events = poll_messages(conn, cursor, limit=100)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0][1]["layer_llm"]["input_tokens"], 906)
            self.assertEqual(events[0][1]["layer_llm"]["output_tokens"], 138)


class TestPollParts(unittest.TestCase):
    def test_poll_parts_text(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                parts=[
                    {
                        "id": "p1",
                        "session_id": "s1",
                        "data": {"type": "text", "text": "hello world"},
                        "time_created": 100,
                    },
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_parts(conn, cursor, limit=100)
            self.assertEqual(len(events), 1)
            et, payload, ts, rid = events[0]
            self.assertEqual(et, "llm_output")
            self.assertEqual(payload["layer_llm"]["output_text"], "hello world")

    def test_poll_parts_reasoning(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                parts=[
                    {
                        "id": "p1",
                        "data": {"type": "reasoning", "text": "thinking step 1"},
                        "time_created": 200,
                    },
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_parts(conn, cursor, limit=100)
            self.assertEqual(len(events), 1)
            et, payload, ts, rid = events[0]
            self.assertEqual(et, "llm_reasoning")
            self.assertIn("reasoning_text_hash", payload["layer_llm"])

    def test_poll_parts_step_start(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                parts=[
                    {"id": "p1", "data": {"type": "step-start"}, "time_created": 300},
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_parts(conn, cursor, limit=100)
            self.assertEqual(len(events), 1)
            et, payload, _, _ = events[0]
            self.assertEqual(et, "agent_step_start")
            self.assertEqual(payload["layer_agent"]["step_status"], "start")

    def test_poll_parts_step_finish(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                parts=[
                    {"id": "p1", "data": {"type": "step-finish"}, "time_created": 400},
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_parts(conn, cursor, limit=100)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0][0], "agent_step_finish")

    def test_poll_parts_tool_success(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                parts=[
                    {
                        "id": "p1",
                        "data": {
                            "type": "tool",
                            "tool": "bash",
                            "state": {
                                "status": "completed",
                                "input": {"command": "ls"},
                                "metadata": {"exit": 0, "output": "ok"},
                            },
                        },
                        "time_created": 500,
                    },
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_parts(conn, cursor, limit=100)
            self.assertEqual(len(events), 1)
            et, payload, _, _ = events[0]
            self.assertEqual(et, "tool_call")
            self.assertEqual(payload["layer_tool"]["tool_name"], "bash")
            self.assertEqual(payload["layer_tool"]["tool_status"], "success")

    def test_poll_parts_tool_error(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(
                db_path,
                parts=[
                    {
                        "id": "p1",
                        "data": {
                            "type": "tool",
                            "tool": "git",
                            "state": {
                                "status": "error",
                                "input": {},
                                "metadata": {"output": "merge conflict"},
                            },
                        },
                        "time_created": 600,
                    },
                ],
            )
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_parts(conn, cursor, limit=100)
            self.assertEqual(len(events), 1)
            et, payload, _, _ = events[0]
            self.assertEqual(et, "error")
            self.assertEqual(payload["layer_tool"]["tool_status"], "error")

    def test_poll_parts_empty(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = _create_mock_db(db_path)
            cursor = {"message": {"ts": 0, "id": ""}, "part": {"ts": 0, "id": ""}}
            events = poll_parts(conn, cursor, limit=100)
            self.assertEqual(events, [])


class TestBackfillBuildEvent(unittest.TestCase):
    def test_build_event(self):
        from adapters.backfill_opencode import _build_event

        line = _build_event("test_event", {"k": "v"}, timestamp=12345.0)
        ev = json.loads(line)
        self.assertEqual(ev["event_type"], "test_event")
        self.assertEqual(ev["payload"]["k"], "v")
        self.assertEqual(ev["timestamp"], 12345.0)
        self.assertEqual(ev["system"], "opencode")
        self.assertEqual(ev["mode"], "black")


class TestDaemonAndBackfillImport(unittest.TestCase):
    def test_daemon_import(self):
        from adapters.daemon_opencode import main as daemon_main

        self.assertTrue(callable(daemon_main))

    def test_backfill_import(self):
        from adapters.backfill_opencode import main as backfill_main

        self.assertTrue(callable(backfill_main))

    def test_backfill_build_event_export(self):
        from adapters.backfill_opencode import _build_event

        self.assertTrue(callable(_build_event))


if __name__ == "__main__":
    unittest.main()
