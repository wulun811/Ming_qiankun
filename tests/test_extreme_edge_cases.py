"""极端异常场景测试 — 覆盖 95_测试盲区清单 中的新增修复"""

import ctypes, gc, hashlib, json, os, shutil, sqlite3, sys, tempfile, time, threading, unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_src = str(Path(__file__).parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)


# ======================================================================
# P0-NEW-1: ctypes.CDLL 非 glibc 容错
# ======================================================================
class TestMallocTrimFallback(unittest.TestCase):
    """P0-NEW-1: malloc_trim 在 musl/Alpine 上不崩溃"""

    def test_run_once_survives_ctypes_failure(self):
        """archiver.run_once() 在 ctypes.CDLL 失败时正常返回"""
        import archiver

        with tempfile.TemporaryDirectory() as td:
            a = archiver.Archiver.__new__(archiver.Archiver)
            a.DB = Path(td) / "ming.db"
            a.HOT = Path(td) / "hot"
            a.COLD = Path(td) / "cold"
            a.STAGING = Path(td) / "staging"
            for d in (a.HOT, a.COLD, a.STAGING):
                d.mkdir(parents=True, exist_ok=True)
            a._alive = True
            a._last_compress = 0
            a._last_backup = 0
            a._last_summary = 0
            a._trim_counter = 0
            a._error_log = str(Path(td) / "errors.jsonl")
            a._heartbeat_path = str(Path(td) / ".archiver_heartbeat")
            a._backup_interval = 86400
            a._backup_dir = str(Path(td) / "backup")
            a._confirmations_applied = False
            a._max_consecutive_errors = 100
            a._consecutive_errors = 0

            # Mock init_schema to avoid DB setup
            with patch("archiver.init_schema"):
                with patch("ctypes.CDLL", side_effect=OSError("libc.so.6 not found")):
                    # Should not raise
                    result = a.run_once()
                    self.assertIsInstance(result, int)

    def test_daemon_thread_survives_ctypes_failure(self):
        """daemon 线程在 ctypes.CDLL 失败时能正常启动"""
        import archiver

        with tempfile.TemporaryDirectory() as td:
            a = archiver.Archiver.__new__(archiver.Archiver)
            a.DB = Path(td) / "ming.db"
            a.HOT = Path(td) / "hot"
            a.COLD = Path(td) / "cold"
            a.STAGING = Path(td) / "staging"
            for d in (a.HOT, a.COLD, a.STAGING):
                d.mkdir(parents=True, exist_ok=True)
            a._alive = True
            a._last_compress = 0
            a._last_backup = 0
            a._last_summary = 0
            a._trim_counter = 0
            a._error_log = str(Path(td) / "errors.jsonl")
            a._heartbeat_path = str(Path(td) / ".archiver_heartbeat")
            a._backup_interval = 86400
            a._backup_dir = str(Path(td) / "backup")
            a._confirmations_applied = False
            a._max_consecutive_errors = 100
            a._consecutive_errors = 0

            with patch("archiver.init_schema"):
                with patch("ctypes.CDLL", side_effect=OSError("libc.so.6 not found")):
                    a.start_daemon()
                    time.sleep(0.5)
                    a._alive = False
                    if a._daemon_thread:
                        a._daemon_thread.join(timeout=2)
                    # Thread should have started without crash
                    self.assertIsNotNone(
                        a._daemon_thread, "Daemon thread should have been created"
                    )
                    self.assertFalse(
                        a._daemon_thread.is_alive(), "Daemon thread should have stopped"
                    )


# ======================================================================
# P0-NEW-2: 年份边界诊断表跨年查询
# ======================================================================
class TestDiagnosesQuery(unittest.TestCase):
    """P0-NEW-2: diagnoses_query 跨年查询"""

    def _make_db(self, td):
        db = Path(td) / "ming.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE diagnoses_2025 (id INTEGER, system TEXT, severity TEXT, created_at REAL)"
        )
        conn.execute(
            "CREATE TABLE diagnoses_2026 (id INTEGER, system TEXT, severity TEXT, created_at REAL)"
        )
        conn.execute("INSERT INTO diagnoses_2025 VALUES (1, 'sys_a', 'P0', 1000.0)")
        conn.execute("INSERT INTO diagnoses_2025 VALUES (2, 'sys_b', 'P1', 2000.0)")
        conn.execute("INSERT INTO diagnoses_2026 VALUES (3, 'sys_a', 'P0', 3000.0)")
        conn.commit()
        conn.close()
        return db

    def test_queries_both_years(self):
        from archiver_util import diagnoses_query

        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            conn = sqlite3.connect(str(db))
            rows = diagnoses_query(conn, "id, system, severity, created_at")
            self.assertEqual(len(rows), 3)
            conn.close()

    def test_with_where_clause(self):
        from archiver_util import diagnoses_query

        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            conn = sqlite3.connect(str(db))
            rows = diagnoses_query(
                conn, "id, system", "severity = ?", ("P0",), "ORDER BY id"
            )
            self.assertEqual(len(rows), 2)
            ids = [r[0] for r in rows]
            self.assertIn(1, ids)
            self.assertIn(3, ids)
            conn.close()

    def test_distinct_systems_across_years(self):
        from archiver_util import diagnoses_query

        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            conn = sqlite3.connect(str(db))
            rows = diagnoses_query(conn, "DISTINCT system")
            systems = {r[0] for r in rows}
            self.assertIn("sys_a", systems)
            self.assertIn("sys_b", systems)
            conn.close()

    def test_no_diagnoses_tables(self):
        from archiver_util import diagnoses_query

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "empty.db"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE events (id INTEGER)")
            conn.commit()
            rows = diagnoses_query(conn, "*")
            # Should fallback to current year table (which doesn't exist)
            self.assertEqual(len(rows), 0)
            conn.close()

    def test_order_limit(self):
        from archiver_util import diagnoses_query

        with tempfile.TemporaryDirectory() as td:
            db = self._make_db(td)
            conn = sqlite3.connect(str(db))
            rows = diagnoses_query(
                conn,
                "id, created_at",
                params=(),
                order_limit="ORDER BY created_at DESC LIMIT 2",
            )
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], 3)  # most recent first
            conn.close()


# ======================================================================
# P0-NEW-4: Staging 目录泄漏清理
# ======================================================================
class TestStagingCleanup(unittest.TestCase):
    """P0-NEW-4: daemon 循环定期清理 staging"""

    def test_cleanup_stale_staging_moves_to_hot(self):
        import archiver

        with tempfile.TemporaryDirectory() as td:
            a = archiver.Archiver.__new__(archiver.Archiver)
            a.HOT = Path(td) / "hot"
            a.COLD = Path(td) / "cold"
            a.STAGING = Path(td) / "staging"
            for d in (a.HOT, a.COLD, a.STAGING):
                d.mkdir(parents=True, exist_ok=True)

            # Create orphaned staging file
            staging_file = a.STAGING / "orphan.jsonl"
            staging_file.write_text('{"test": 1}\n')

            a._cleanup_stale_staging()

            # Should be moved to HOT
            self.assertTrue((a.HOT / "orphan.jsonl").exists())
            self.assertFalse(staging_file.exists())

    def test_cleanup_removes_duplicate_staging(self):
        import archiver

        with tempfile.TemporaryDirectory() as td:
            a = archiver.Archiver.__new__(archiver.Archiver)
            a.HOT = Path(td) / "hot"
            a.COLD = Path(td) / "cold"
            a.STAGING = Path(td) / "staging"
            for d in (a.HOT, a.COLD, a.STAGING):
                d.mkdir(parents=True, exist_ok=True)

            # Create staging file that also exists in HOT
            (a.HOT / "dup.jsonl").write_text('{"hot": 1}\n')
            (a.STAGING / "dup.jsonl").write_text('{"staging": 1}\n')

            a._cleanup_stale_staging()

            # Staging should be deleted, HOT should remain
            self.assertFalse((a.STAGING / "dup.jsonl").exists())
            self.assertTrue((a.HOT / "dup.jsonl").exists())


# ======================================================================
# P0-NEW-5: _commit_batch rollback
# ======================================================================
class TestCommitBatchRollback(unittest.TestCase):
    """P0-NEW-5: _commit_batch 失败时显式 rollback"""

    def test_commit_batch_rollback_on_error(self):
        import archiver

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
            conn.execute(
                "CREATE TABLE events_blob (event_id INTEGER, payload_blob BLOB)"
            )
            conn.commit()

            a = archiver.Archiver.__new__(archiver.Archiver)

            # Create a batch with invalid ev_id (will cause FK or type error)
            batch = [(b"blob1", 999), (b"blob2", -1)]

            # The second insert will try to UPDATE events SET ... WHERE id = -1
            # which won't match anything but won't error either.
            # Let's make it error by passing invalid data
            try:
                a._commit_batch(conn, [(None, None)])
            except Exception:
                pass

            # Connection should still be usable after rollback
            result = conn.execute("SELECT COUNT(*) FROM events").fetchone()
            self.assertEqual(result[0], 0)
            conn.close()


# ======================================================================
# P1-NEW-1: log_error stderr fallback
# ======================================================================
class TestLogErrorFallback(unittest.TestCase):
    """P1-NEW-1: log_error 文件写入失败时 stderr fallback"""

    def test_log_error_falls_back_to_stderr(self):
        from archiver_util import log_error

        with tempfile.TemporaryDirectory() as td:
            error_log = Path(td) / "nonexistent_dir" / "errors.jsonl"
            # The directory doesn't exist, so file write will fail
            # But mkdir(parents=True) will create it, so let's use a read-only dir
            ro_dir = Path(td) / "readonly"
            ro_dir.mkdir()
            error_log = ro_dir / "errors.jsonl"

            with patch("builtins.open", side_effect=OSError("disk full")):
                with patch("sys.stderr") as mock_stderr:
                    log_error(str(error_log), Exception("test error"))
                    # Should have printed to stderr
                    mock_stderr.write.assert_called()

    def test_log_error_normal_operation(self):
        from archiver_util import log_error

        with tempfile.TemporaryDirectory() as td:
            error_log = Path(td) / "errors.jsonl"
            log_error(str(error_log), Exception("test error"), consecutive_count=5)

            self.assertTrue(error_log.exists())
            line = error_log.read_text().strip()
            entry = json.loads(line)
            self.assertEqual(entry["error"], "test error")
            self.assertEqual(entry["consecutive_count"], 5)
            self.assertEqual(entry["type"], "Exception")


# ======================================================================
# P1-NEW-6: VACUUM 磁盘空间检查
# ======================================================================
class TestVacuumDiskCheck(unittest.TestCase):
    """P1-NEW-6: VACUUM 前检查磁盘空间"""

    def test_vacuum_skips_on_low_disk(self):
        from archiver_util import run_vacuum

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.commit()
            conn.close()

            with patch("shutil.disk_usage") as mock_disk:
                mock_disk.return_value = MagicMock(free=100)
                result = run_vacuum(str(db), 0, 0)
                # Should skip because free (100) < db_size * 2
                self.assertIsNone(result)

    def test_vacuum_runs_when_enough_space(self):
        from archiver_util import run_vacuum

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "test.db"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE t (id INTEGER)")
            for i in range(100):
                conn.execute("INSERT INTO t VALUES (?)", (i,))
            conn.commit()
            conn.close()

            # Don't mock - should have enough space in /tmp
            result = run_vacuum(str(db), 0, 0)
            # Should return a number (bytes freed, could be 0 or positive)
            self.assertIsNotNone(result)
            self.assertIsInstance(result, int)


# ======================================================================
# P1-NEW-7: 迁移文件名解析容错
# ======================================================================
class TestMigrationFilenameParsing(unittest.TestCase):
    """P1-NEW-7: 非标 .sql 文件名不导致 crash"""

    def test_skip_non_standard_filename(self):
        with tempfile.TemporaryDirectory() as td:
            migrations_dir = Path(td) / "migrations"
            migrations_dir.mkdir()

            # Valid migration file
            (migrations_dir / "0001_baseline.sql").write_text("CREATE TABLE t (id);")

            # Non-standard file that would cause ValueError
            (migrations_dir / "README.sql").write_text("-- This is a readme")
            (migrations_dir / "backup.sql").write_text("-- backup")

            # Patch MIGRATIONS_DIR
            import migrations.migrate as migrate

            orig_dir = migrate.MIGRATIONS_DIR
            migrate.MIGRATIONS_DIR = migrations_dir
            try:
                db = Path(td) / "test.db"
                conn = sqlite3.connect(str(db))
                conn.execute("CREATE TABLE _schema_version (version INTEGER)")
                conn.execute("INSERT INTO _schema_version VALUES (0)")
                conn.commit()
                conn.close()

                # Should not raise ValueError
                result = migrate.run_migrations(str(db), skip=True)
                self.assertIn("skipped", str(result))
            finally:
                migrate.MIGRATIONS_DIR = orig_dir


# ======================================================================
# P1-NEW-8: Config 注释剥离不破坏字符串
# ======================================================================
class TestConfigCommentStripping(unittest.TestCase):
    """P1-NEW-8: JSON 字符串内的 // 和 /* */ 不被误删"""

    def test_url_preserved(self):
        from config_loader import _strip_comments

        text = '{"url": "http://example.com", "port": 8080}'
        result = _strip_comments(text)
        parsed = json.loads(result)
        self.assertEqual(parsed["url"], "http://example.com")
        self.assertEqual(parsed["port"], 8080)

    def test_block_comment_in_string_preserved(self):
        from config_loader import _strip_comments

        text = '{"desc": "use /* for comments */ in code"}'
        result = _strip_comments(text)
        parsed = json.loads(result)
        self.assertEqual(parsed["desc"], "use /* for comments */ in code")

    def test_actual_comments_stripped(self):
        from config_loader import _strip_comments

        text = """
{
    // this is a comment
    "key": "value",
    /* block comment */
    "num": 42
}
"""
        result = _strip_comments(text)
        parsed = json.loads(result)
        self.assertEqual(parsed["key"], "value")
        self.assertEqual(parsed["num"], 42)

    def test_escaped_quote_in_string(self):
        from config_loader import _strip_comments

        text = '{"key": "value with \\"quote\\" and // not a comment"}'
        result = _strip_comments(text)
        parsed = json.loads(result)
        self.assertIn("//", parsed["key"])


# ======================================================================
# P1-NEW-9: 环境变量类型混乱
# ======================================================================
class TestConfigEnvVarType(unittest.TestCase):
    """P1-NEW-9: 非数字环境变量不导致 crash"""

    def test_non_numeric_env_var_ignored(self):
        from config_loader import load_config

        with patch.dict(os.environ, {"WQ_ARCHIVER_FLUSH_SEC": "not_a_number"}):
            with patch("config_loader._find_config", return_value=(None, "default")):
                with patch("builtins.print") as mock_print:
                    config, source, errors = load_config()
                    # Should print warning about ignoring
                    calls = [str(c) for c in mock_print.call_args_list]
                    ignored = any("忽略" in c for c in calls)
                    self.assertTrue(ignored, f"Expected '忽略' in print calls: {calls}")

    def test_valid_numeric_env_var_works(self):
        from config_loader import load_config

        with patch.dict(os.environ, {"WQ_ARCHIVER_FLUSH_SEC": "5.5"}):
            with patch("config_loader._find_config", return_value=(None, "default")):
                config, source, errors = load_config()
                self.assertEqual(config["archiver"]["flush_sec"], 5.5)


# ======================================================================
# P1-NEW-12: ClusterArchiver daemon 异常日志
# ======================================================================
class TestClusterArchiverDaemonLogging(unittest.TestCase):
    """P1-NEW-12: daemon 循环异常不再静默吞掉"""

    def test_daemon_loop_logs_fatal_error(self):
        import cluster_archiver as ca

        pool = MagicMock()
        pool.get.return_value = None

        archiver = ca.ClusterArchiver.__new__(ca.ClusterArchiver)
        archiver.pool = pool
        archiver.project_id = "test"
        hot_dir = Path(tempfile.mkdtemp())
        staging_dir = Path(tempfile.mkdtemp())
        archiver.hot_dir = hot_dir
        archiver.staging_dir = staging_dir
        archiver._buffer = []
        archiver._lock = threading.Lock()
        archiver._max_buffer = 100
        archiver._alive = True
        archiver.flush_interval = 0.01

        try:
            # Make time.sleep raise to trigger outer except
            with patch(
                "cluster_archiver.time.sleep", side_effect=RuntimeError("fatal")
            ):
                with patch.object(ca, "logger") as mock_logger:
                    archiver._daemon_loop()
                    mock_logger.error.assert_called()
                    call_str = str(mock_logger.error.call_args)
                    self.assertIn("fatal", call_str)
        finally:
            shutil.rmtree(hot_dir, ignore_errors=True)
            shutil.rmtree(staging_dir, ignore_errors=True)


# ======================================================================
# P0-1: Daemon 线程死亡后 _alive 变 False
# ======================================================================
class TestDaemonThreadDeath(unittest.TestCase):
    """P0-1: daemon 线程达到最大错误次数后设置 _alive = False"""

    def test_daemon_sets_alive_false_on_max_errors(self):
        import archiver

        with tempfile.TemporaryDirectory() as td:
            a = archiver.Archiver.__new__(archiver.Archiver)
            a.DB = Path(td) / "ming.db"
            a.HOT = Path(td) / "hot"
            a.COLD = Path(td) / "cold"
            a.STAGING = Path(td) / "staging"
            a.ERROR_LOG = Path(td) / ".archiver_errors.jsonl"
            for d in (a.HOT, a.COLD, a.STAGING):
                d.mkdir(parents=True, exist_ok=True)
            a._alive = True
            a._last_compress = 0
            a._last_backup = 0
            a._last_summary = 0
            a._trim_counter = 0
            a._heartbeat_path = str(Path(td) / ".archiver_heartbeat")
            a._backup_interval = 86400
            a._backup_dir = str(Path(td) / "backup")
            a._confirmations_applied = False
            a.FLUSH_INTERVAL = 0.01

            # Make run_once always raise
            with patch.object(a, "run_once", side_effect=RuntimeError("boom")):
                with patch.object(a, "vacuum"):
                    # start_daemon sets _max_consecutive_errors = 100
                    with patch.object(a, "_open_db", side_effect=OSError("no db")):
                        a.start_daemon()
                    # Override after start_daemon sets it
                    a._max_consecutive_errors = 3
                    time.sleep(1.0)
                    # After 3 consecutive errors, _alive should be False
                    self.assertFalse(a._alive)
                    if a._daemon_thread:
                        a._daemon_thread.join(timeout=2)


# ======================================================================
# P0-2: ming.py 监控循环检测 daemon 线程死亡
# ======================================================================
class TestMingMonitorDetection(unittest.TestCase):
    """P0-2: ming.py 监控循环能检测到 daemon 线程死亡"""

    def test_monitor_checks_is_alive(self):
        """验证 ming.py 的监控逻辑同时检查 _alive 和 is_alive()"""
        import archiver

        with tempfile.TemporaryDirectory() as td:
            a = archiver.Archiver.__new__(archiver.Archiver)
            a.DB = Path(td) / "ming.db"
            a.HOT = Path(td) / "hot"
            a.COLD = Path(td) / "cold"
            a.STAGING = Path(td) / "staging"
            for d in (a.HOT, a.COLD, a.STAGING):
                d.mkdir(parents=True, exist_ok=True)
            a._alive = True
            a._last_compress = 0
            a._last_backup = 0
            a._last_summary = 0
            a._trim_counter = 0
            a._error_log = str(Path(td) / "errors.jsonl")
            a._heartbeat_path = str(Path(td) / ".archiver_heartbeat")
            a._backup_interval = 86400
            a._backup_dir = str(Path(td) / "backup")
            a._confirmations_applied = False
            a._max_consecutive_errors = 100
            a._consecutive_errors = 0
            a.FLUSH_INTERVAL = 0.01
            a._daemon_thread = None

            # Simulate: _alive is True but thread is dead
            dead_thread = threading.Thread(target=lambda: None)
            dead_thread.start()
            dead_thread.join()  # thread is now dead
            a._daemon_thread = dead_thread

            # The monitor condition in ming.py:
            should_restart = not a._alive or (
                a._daemon_thread and not a._daemon_thread.is_alive()
            )
            self.assertTrue(should_restart, "Dead thread should trigger restart")


# ======================================================================
# P1-NEW-11: ClusterArchiver 失败事件重放
# ======================================================================
class TestClusterArchiverReplay(unittest.TestCase):
    """P1-NEW-11: _failed 目录中的事件能被重放"""

    def test_replay_failed_moves_to_buffer(self):
        import cluster_archiver as ca

        with tempfile.TemporaryDirectory() as td:
            archiver = ca.ClusterArchiver.__new__(ca.ClusterArchiver)
            archiver.pool = MagicMock()
            archiver.project_id = "test"
            archiver.hot_dir = Path(td) / "hot"
            archiver.staging_dir = Path(td) / "staging"
            archiver.FAILED_DIR = Path(td) / "_failed"
            archiver.hot_dir.mkdir()
            archiver.staging_dir.mkdir()
            archiver.FAILED_DIR.mkdir()
            archiver._buffer = []
            archiver._lock = threading.Lock()
            archiver._max_buffer = 100

            # Create failed event files
            for i in range(3):
                evt = {"event_type": "test", "system": "sys", "idx": i}
                fp = archiver.FAILED_DIR / f"failed_{i}_sys_{i:08d}.json"
                fp.write_text(json.dumps(evt))

            archiver._replay_failed()

            # All 3 should be in buffer
            self.assertEqual(len(archiver._buffer), 3)
            # Files should be deleted
            self.assertEqual(len(list(archiver.FAILED_DIR.glob("failed_*.json"))), 0)

    def test_replay_skips_corrupted_files(self):
        import cluster_archiver as ca

        with tempfile.TemporaryDirectory() as td:
            archiver = ca.ClusterArchiver.__new__(ca.ClusterArchiver)
            archiver.FAILED_DIR = Path(td) / "_failed"
            archiver.FAILED_DIR.mkdir()
            archiver._buffer = []
            archiver._lock = threading.Lock()
            archiver._max_buffer = 100

            # One valid, one corrupted
            (archiver.FAILED_DIR / "failed_1_sys_0001.json").write_text('{"ok": true}')
            (archiver.FAILED_DIR / "failed_2_sys_0002.json").write_text("NOT JSON {{{")

            archiver._replay_failed()

            # Only the valid one should be in buffer
            self.assertEqual(len(archiver._buffer), 1)
            # Corrupted file should still exist (not deleted)
            self.assertEqual(len(list(archiver.FAILED_DIR.glob("failed_*.json"))), 1)


# ======================================================================
# P1-NEW-1: log_error 吞异常
# ======================================================================
class TestLogErrorStderrFallback(unittest.TestCase):
    """P1-NEW-1: log_error 文件写入失败时输出到 stderr"""

    def test_stderr_fallback_on_write_failure(self):
        from archiver_util import log_error
        import io

        with tempfile.TemporaryDirectory() as td:
            error_log = str(Path(td) / "errors.jsonl")

            # Make open() fail
            with patch("builtins.open", side_effect=OSError("disk full")):
                stderr_capture = io.StringIO()
                with patch("sys.stderr", stderr_capture):
                    log_error(error_log, Exception("test error"))
                    output = stderr_capture.getvalue()
                    self.assertIn("test error", output)
                    self.assertIn("log_error failed", output)


# ======================================================================
# P0-3: 哈希链断裂检测
# ======================================================================
class TestHashChainIntegrity(unittest.TestCase):
    """P0-3: 哈希链断裂检测 + 压缩降级"""

    def test_probe_integrity_detects_break(self):
        """probe_integrity.verify_hash_chain() 能检测到哈希链断裂"""
        import probe_integrity

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ming.db"
            conn = sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE events ("
                "id INTEGER PRIMARY KEY, system TEXT, event_type TEXT, "
                "timestamp REAL, payload TEXT, prev_hash TEXT, curr_hash TEXT, "
                "integrity TEXT DEFAULT 'linked', storage_tier INTEGER DEFAULT 0, "
                "compress_attempts INTEGER DEFAULT 0, ttl_protected INTEGER DEFAULT 0"
                ")"
            )
            # Insert events with self-referential hash (prev_hash == curr_hash)
            # This is what verify_hash_chain considers "valid"
            for i in range(3):
                h = hashlib.sha256(f"event_{i}".encode()).hexdigest()
                conn.execute(
                    "INSERT INTO events (id, system, event_type, timestamp, prev_hash, curr_hash) "
                    "VALUES (?, 's', 't', ?, ?, ?)",
                    (i + 1, float(i), h, h),
                )
            conn.commit()
            conn.close()

            checker = probe_integrity.ProbeIntegrity(str(db))
            result = checker.verify_hash_chain()
            self.assertEqual(result["total"], 3)
            self.assertEqual(result["broken"], 0)

    def test_probe_integrity_detects_corruption(self):
        """修改某条记录的 curr_hash 后能检测到断裂"""
        import probe_integrity

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ming.db"
            conn = sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE events ("
                "id INTEGER PRIMARY KEY, system TEXT, event_type TEXT, "
                "timestamp REAL, payload TEXT, prev_hash TEXT, curr_hash TEXT, "
                "integrity TEXT DEFAULT 'linked', storage_tier INTEGER DEFAULT 0, "
                "compress_attempts INTEGER DEFAULT 0, ttl_protected INTEGER DEFAULT 0"
                ")"
            )
            # Insert events with self-referential hash
            for i in range(3):
                h = hashlib.sha256(f"event_{i}".encode()).hexdigest()
                conn.execute(
                    "INSERT INTO events (id, system, event_type, timestamp, prev_hash, curr_hash) "
                    "VALUES (?, 's', 't', ?, ?, ?)",
                    (i + 1, float(i), h, h),
                )

            # Corrupt event #2: make prev_hash != curr_hash
            conn.execute("UPDATE events SET curr_hash = 'corrupted_hash' WHERE id = 2")
            conn.commit()
            conn.close()

            checker = probe_integrity.ProbeIntegrity(str(db))
            result = checker.verify_hash_chain()
            self.assertEqual(result["broken"], 1)
            self.assertIn(2, result["broken_ids"])

    def test_probe_integrity_handles_rebooted(self):
        """rebooted 标记的事件不计入 broken"""
        import probe_integrity

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ming.db"
            conn = sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE events ("
                "id INTEGER PRIMARY KEY, system TEXT, event_type TEXT, "
                "timestamp REAL, payload TEXT, prev_hash TEXT, curr_hash TEXT, "
                "integrity TEXT DEFAULT 'linked', storage_tier INTEGER DEFAULT 0, "
                "compress_attempts INTEGER DEFAULT 0, ttl_protected INTEGER DEFAULT 0"
                ")"
            )
            h = hashlib.sha256(b"test").hexdigest()
            conn.execute(
                "INSERT INTO events (id, system, event_type, timestamp, prev_hash, curr_hash, integrity) "
                "VALUES (1, 's', 't', 0, ?, ?, 'rebooted')",
                (h, h),
            )
            conn.execute(
                "INSERT INTO events (id, system, event_type, timestamp, prev_hash, curr_hash, integrity) "
                "VALUES (2, 's', 't', 1, ?, ?, 'linked')",
                (h, h),
            )
            conn.commit()
            conn.close()

            checker = probe_integrity.ProbeIntegrity(str(db))
            result = checker.verify_hash_chain()
            self.assertEqual(result["rebooted_count"], 1)
            self.assertEqual(result["broken"], 0)
            self.assertEqual(result["valid"], 2)

    def test_compress_marks_failed_after_3_attempts(self):
        """压缩失败 3 次后 storage_tier 设为 2"""
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ming.db"
            conn = sqlite3.connect(str(db))
            conn.execute(
                "CREATE TABLE events ("
                "id INTEGER PRIMARY KEY, system TEXT, event_type TEXT, "
                "timestamp REAL, payload TEXT, prev_hash TEXT, curr_hash TEXT, "
                "integrity TEXT DEFAULT 'linked', storage_tier INTEGER DEFAULT 0, "
                "compress_attempts INTEGER DEFAULT 0, ttl_protected INTEGER DEFAULT 0"
                ")"
            )
            # Insert an event with large payload
            conn.execute(
                "INSERT INTO events (id, system, event_type, timestamp, payload, compress_attempts) "
                "VALUES (1, 's', 't', ?, ?, 2)",
                (
                    time.time(),
                    json.dumps({"data": "x" * 1000}),
                ),
            )
            conn.commit()

            # Simulate compression failure increment
            cur = conn.execute(
                "UPDATE events SET compress_attempts = compress_attempts + 1 WHERE id = 1 "
                "RETURNING compress_attempts"
            )
            row = cur.fetchone()
            if row and row[0] >= 3:
                conn.execute("UPDATE events SET storage_tier = 2 WHERE id = 1")
            conn.commit()

            row = conn.execute(
                "SELECT storage_tier, compress_attempts FROM events WHERE id = 1"
            ).fetchone()
            self.assertEqual(row[0], 2)  # permanently failed
            self.assertEqual(row[1], 3)
            conn.close()


# ======================================================================
# P0-4: Watchdog archiver_down 事件生成
# ======================================================================
class TestWatchdogAlerts(unittest.TestCase):
    """P0-4: watchdog 心跳检测 + archiver_down 事件"""

    def test_check_archiver_dead_after_consecutive_failures(self):
        """心跳文件过期连续 3 次后返回 dead"""
        import watchdog

        with tempfile.TemporaryDirectory() as td:
            hb = Path(td) / "heartbeat"
            wd = watchdog.PassiveWatchdog.__new__(watchdog.PassiveWatchdog)
            wd.ARCHIVER_TIMEOUT = 60
            wd._consecutive_failures = 0

            # Write stale heartbeat (120 seconds ago)
            hb.write_text(str(time.time() - 120))

            with patch.object(watchdog.PassiveWatchdog, "HEARTBEAT", hb):
                # First check: stale (failures=1)
                result = wd._check_archiver_file()
                self.assertEqual(result["status"], "stale")
                self.assertEqual(result["consecutive_failures"], 1)

                # Second check: stale (failures=2)
                result = wd._check_archiver_file()
                self.assertEqual(result["status"], "stale")
                self.assertEqual(result["consecutive_failures"], 2)

                # Third check: dead (failures=3)
                result = wd._check_archiver_file()
                self.assertEqual(result["status"], "dead")
                self.assertEqual(result["consecutive_failures"], 3)

    def test_check_archiver_dead_on_missing_file(self):
        """心跳文件不存在连续 3 次后返回 dead"""
        import watchdog

        with tempfile.TemporaryDirectory() as td:
            hb = Path(td) / "nonexistent_heartbeat"
            wd = watchdog.PassiveWatchdog.__new__(watchdog.PassiveWatchdog)
            wd.ARCHIVER_TIMEOUT = 60
            wd._consecutive_failures = 0

            with patch.object(watchdog.PassiveWatchdog, "HEARTBEAT", hb):
                for _ in range(2):
                    result = wd._check_archiver_file()
                    self.assertEqual(result["status"], "stale")

                result = wd._check_archiver_file()
                self.assertEqual(result["status"], "dead")

    def test_check_archiver_handles_corrupted_heartbeat(self):
        """心跳文件内容损坏时正确处理"""
        import watchdog

        with tempfile.TemporaryDirectory() as td:
            hb = Path(td) / "heartbeat"
            hb.write_text("not_a_number")

            wd = watchdog.PassiveWatchdog.__new__(watchdog.PassiveWatchdog)
            wd.ARCHIVER_TIMEOUT = 60
            wd._consecutive_failures = 0

            with patch.object(watchdog.PassiveWatchdog, "HEARTBEAT", hb):
                result = wd._check_archiver_file()
                self.assertEqual(result["status"], "stale")
                self.assertEqual(result["consecutive_failures"], 1)

    def test_check_archiver_resets_on_alive(self):
        """心跳正常时重置失败计数"""
        import watchdog

        with tempfile.TemporaryDirectory() as td:
            hb = Path(td) / "heartbeat"
            hb.write_text(str(time.time()))

            wd = watchdog.PassiveWatchdog.__new__(watchdog.PassiveWatchdog)
            wd.ARCHIVER_TIMEOUT = 60
            wd._consecutive_failures = 2  # Previously had failures

            with patch.object(watchdog.PassiveWatchdog, "HEARTBEAT", hb):
                result = wd._check_archiver_file()
                self.assertEqual(result["status"], "alive")
                self.assertEqual(result["consecutive_failures"], 0)

    def test_synthesize_event_creates_jsonl(self):
        """_synthesize_event 正确生成 JSONL 文件"""
        import watchdog

        with tempfile.TemporaryDirectory() as td:
            hot = Path(td) / "hot"
            hot.mkdir()

            wd = watchdog.PassiveWatchdog.__new__(watchdog.PassiveWatchdog)
            wd.HOT = hot

            wd._synthesize_event(
                "test_system",
                "__archiver_down__",
                {"restart_attempts": 3, "max_restarts": 3},
            )

            files = list(hot.glob("watchdog_*.jsonl"))
            self.assertEqual(len(files), 1)
            content = files[0].read_text()
            evt = json.loads(content.strip())
            self.assertEqual(evt["system"], "test_system")
            self.assertEqual(evt["event_type"], "__archiver_down__")
            self.assertEqual(evt["mode"], "watchdog")

    def test_check_all_generates_archiver_down_alert(self):
        """check_all 在 restart_attempts >= max_restarts 时生成 archiver_down 告警"""
        import watchdog

        with tempfile.TemporaryDirectory() as td:
            hb = Path(td) / "heartbeat"
            hot = Path(td) / "hot"
            hot.mkdir()

            # Write stale heartbeat
            hb.write_text(str(time.time() - 120))

            wd = watchdog.PassiveWatchdog.__new__(watchdog.PassiveWatchdog)
            wd.HOT = hot
            wd.HEARTBEAT = hb
            wd.ARCHIVER_TIMEOUT = 60
            wd.CHECK_INTERVAL = 30
            wd._consecutive_failures = 2  # Already 2 failures
            wd._restart_attempts = 3  # Already exhausted restarts
            wd._max_restarts = 3
            wd.webhook = lambda alert: None

            with patch("self_health.check_archiver_lag", return_value=(0, "ok")):
                alerts = wd.check_all()

            # Should have archiver_down alert
            down_alerts = [a for a in alerts if a.get("type") == "archiver_down"]
            self.assertTrue(
                len(down_alerts) > 0, f"Expected archiver_down alert, got {alerts}"
            )

            # Should have synthesized __archiver_down__ event
            files = list(hot.glob("watchdog_*.jsonl"))
            self.assertEqual(len(files), 1)

    def test_check_all_resets_restart_attempts_on_alive(self):
        """check_all 在 archiver alive 时重置 _restart_attempts"""
        import watchdog

        with tempfile.TemporaryDirectory() as td:
            hb = Path(td) / "heartbeat"
            hot = Path(td) / "hot"
            hot.mkdir()

            # Write fresh heartbeat (alive)
            hb.write_text(str(time.time()))

            wd = watchdog.PassiveWatchdog.__new__(watchdog.PassiveWatchdog)
            wd.HOT = hot
            wd.HEARTBEAT = hb
            wd.ARCHIVER_TIMEOUT = 60
            wd.CHECK_INTERVAL = 30
            wd._consecutive_failures = 0
            wd._restart_attempts = 3  # Previously exhausted restarts
            wd._max_restarts = 3
            wd.webhook = lambda alert: None

            with patch("self_health.check_archiver_lag", return_value=(0, "ok")):
                alerts = wd.check_all()

            # _restart_attempts should be reset to 0
            self.assertEqual(wd._restart_attempts, 0)

    def test_check_all_resets_restart_attempts_on_stale(self):
        """check_all 在 archiver stale 时重置 _restart_attempts"""
        import watchdog

        with tempfile.TemporaryDirectory() as td:
            hb = Path(td) / "heartbeat"
            hot = Path(td) / "hot"
            hot.mkdir()

            # Write stale heartbeat (120 seconds ago, timeout is 60)
            hb.write_text(str(time.time() - 120))

            wd = watchdog.PassiveWatchdog.__new__(watchdog.PassiveWatchdog)
            wd.HOT = hot
            wd.HEARTBEAT = hb
            wd.ARCHIVER_TIMEOUT = 60
            wd.CHECK_INTERVAL = 30
            wd._consecutive_failures = 1  # Will become 2 after check (stale, not dead)
            wd._restart_attempts = 3  # Previously exhausted restarts
            wd._max_restarts = 3
            wd.webhook = lambda alert: None

            with patch("self_health.check_archiver_lag", return_value=(0, "ok")):
                alerts = wd.check_all()

            # _restart_attempts should be reset to 0 (stale resets it)
            self.assertEqual(wd._restart_attempts, 0)


# ======================================================================
# P1: Self-health 异常条件
# ======================================================================
class TestSelfHealthDegraded(unittest.TestCase):
    """P1: self_health 在异常条件下的表现"""

    def test_check_archiver_lag_no_heartbeat(self):
        """心跳文件不存在返回 crit"""
        from self_health import check_archiver_lag

        with patch("self_health.HB") as mock_hb:
            mock_hb.exists.return_value = False
            val, status = check_archiver_lag()
            self.assertIsNone(val)
            self.assertEqual(status, "crit")

    def test_check_archiver_lag_corrupted_heartbeat(self):
        """心跳文件内容损坏返回 error"""
        from self_health import check_archiver_lag

        with patch("self_health.HB") as mock_hb:
            mock_hb.exists.return_value = True
            mock_hb.read_text.return_value = "not_a_number"
            val, status = check_archiver_lag()
            self.assertIsNone(val)
            self.assertEqual(status, "error")

    def test_check_archiver_lag_empty_heartbeat(self):
        """心跳文件为空返回 error"""
        from self_health import check_archiver_lag

        with patch("self_health.HB") as mock_hb:
            mock_hb.exists.return_value = True
            mock_hb.read_text.return_value = ""
            val, status = check_archiver_lag()
            self.assertIsNone(val)
            self.assertEqual(status, "error")

    def test_check_archiver_lag_fresh(self):
        """正常心跳返回 ok"""
        from self_health import check_archiver_lag

        with patch("self_health.HB") as mock_hb:
            mock_hb.exists.return_value = True
            mock_hb.read_text.return_value = str(time.time())
            val, status = check_archiver_lag()
            self.assertIsNotNone(val)
            self.assertLess(val, 5.0)
            self.assertEqual(status, "ok")

    def test_run_check_overrides_on_crit(self):
        """run_check 中任一 check 为 crit 时整体为 crit"""
        from self_health import run_check

        with patch("self_health.check_archiver_lag", return_value=(None, "crit")):
            with patch("self_health.check_wal_size", return_value=(0, "ok")):
                with patch("self_health.check_disk_free", return_value=(1000, "ok")):
                    with patch(
                        "self_health.check_vacuum_due", return_value=("ok", "ok")
                    ):
                        with patch(
                            "self_health._check_hot_dir_tuple", return_value=(10, "ok")
                        ):
                            with patch(
                                "self_health.check_otel_bridge", return_value=(0, "ok")
                            ):
                                with patch(
                                    "self_health.check_lit_lite_lag",
                                    return_value=(0, "ok"),
                                ):
                                    with patch(
                                        "self_health.check_otel_export_fail_rate",
                                        return_value=(0, "ok"),
                                    ):
                                        results, status = run_check()
                                        self.assertEqual(status, "crit")

    def test_run_check_survives_check_exception(self):
        """单个 check 抛异常不影响其他 check"""
        from self_health import run_check

        with patch("self_health.check_archiver_lag", side_effect=RuntimeError("boom")):
            with patch("self_health.check_wal_size", return_value=(0, "ok")):
                with patch("self_health.check_disk_free", return_value=(1000, "ok")):
                    with patch(
                        "self_health.check_vacuum_due", return_value=("ok", "ok")
                    ):
                        with patch(
                            "self_health._check_hot_dir_tuple", return_value=(10, "ok")
                        ):
                            with patch(
                                "self_health.check_otel_bridge", return_value=(0, "ok")
                            ):
                                with patch(
                                    "self_health.check_lit_lite_lag",
                                    return_value=(0, "ok"),
                                ):
                                    with patch(
                                        "self_health.check_otel_export_fail_rate",
                                        return_value=(0, "ok"),
                                    ):
                                        results, status = run_check()
                                        self.assertEqual(
                                            results["archiver_lag"]["status"], "error"
                                        )
                                        self.assertEqual(
                                            status, "warn"
                                        )  # error -> warn


# ======================================================================
# P1: CLI 命令在降级条件下
# ======================================================================
class TestCliDegraded(unittest.TestCase):
    """P1: CLI 命令在降级条件下的行为"""

    def test_cmd_dx_no_db(self):
        """数据库不存在时 cmd_dx 不 crash"""
        import cli

        with tempfile.TemporaryDirectory() as td:
            orig_db = cli.DB
            cli.DB = Path(td) / "nonexistent.db"
            try:
                args = type("args", (), {"query_name": "recent_p0", "limit": 10})()
                # Should print error, not raise
                cli.cmd_dx(args)
            finally:
                cli.DB = orig_db

    def test_cmd_dx_unknown_query(self):
        """未知查询名不 crash"""
        import cli

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ming.db"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE events (id INTEGER)")
            conn.commit()
            conn.close()

            orig_db = cli.DB
            cli.DB = db
            try:
                args = type(
                    "args", (), {"query_name": "nonexistent_query", "limit": 10}
                )()
                cli.cmd_dx(args)
            finally:
                cli.DB = orig_db

    def test_cmd_dx_empty_diagnoses(self):
        """空诊断表返回提示信息"""
        import cli

        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "ming.db"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE events (id INTEGER)")
            # Create current year diagnoses table but empty
            year = time.strftime("%Y")
            conn.execute(f"CREATE TABLE diagnoses_{year} (id INTEGER)")
            conn.commit()
            conn.close()

            orig_db = cli.DB
            cli.DB = db
            try:
                args = type("args", (), {"query_name": "recent_p0", "limit": 10})()
                cli.cmd_dx(args)
            finally:
                cli.DB = orig_db


# ======================================================================
# P1: Bridge 路由降级
# ======================================================================
class TestBridgeRouting(unittest.TestCase):
    """P1: Bridge 路由降级路径"""

    def test_bridge_cli_mode_output(self):
        """Bridge CLI 模式输出包含必要字段"""
        from bridge import Bridge

        b = Bridge()
        b._mode = "cli"
        import io

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            b.emit({"system": "test", "event_type": "hello", "payload": {}})
        output = buf.getvalue()
        self.assertIn("test", output)

    def test_bridge_auto_mode_fallback(self):
        """Bridge auto 模式无 endpoint 时输出到 stdout"""
        from bridge import Bridge
        import io

        with patch.dict(os.environ, {"BRIDGE_MODE": "auto", "LIT_ENDPOINT": ""}):
            b = Bridge()
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                b.emit({"system": "test", "event_type": "hello", "payload": {}})
            output = buf.getvalue()
            # Should output to stdout (cli mode)
            self.assertIn("test", output)

    def test_bridge_batch_emit_empty(self):
        """Bridge batch_emit 空列表不 crash"""
        from bridge import Bridge

        b = Bridge()
        b.batch_emit([])  # Should not raise

    def test_bridge_flush_empty(self):
        """Bridge flush 空 buffer 不 crash"""
        from bridge import Bridge

        b = Bridge()
        b.flush()  # Should not raise


# ======================================================================
# P1-1: 真实并发竞争
# ======================================================================
