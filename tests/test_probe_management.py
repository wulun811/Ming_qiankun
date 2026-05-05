# test_probe_management.py —— v0.11.9m 探针管理命令测试（list / uninstall）
import sys, unittest, json, sqlite3, tempfile, time, io, os
from pathlib import Path
from unittest.mock import patch

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from cli_admin import _cmd_probe_list, _cmd_probe_uninstall, _PROTECTED_SYSTEMS


def _make_test_home(tmpdir):
    """创建隔离的 ~/.ming 目录结构并返回各路径"""
    home = Path(tmpdir) / "home"
    ming = home / ".ming"
    ming.mkdir(parents=True)
    hot = ming / "hot"
    hot.mkdir(parents=True, exist_ok=True)
    cold = ming / "cold"
    cold.mkdir(parents=True, exist_ok=True)
    db = ming / "ming.db"
    paused = ming / ".paused"
    return home, ming, hot, cold, db, paused


def _setup_db(db_path):
    """创建基础表结构"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_pid (
            system TEXT PRIMARY KEY, pid INTEGER, last_seen REAL, mode TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system TEXT, event_type TEXT, payload TEXT, timestamp REAL,
            prev_hash TEXT, curr_hash TEXT
        )
    """)
    year = time.strftime("%Y")
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS diagnoses_{year} (
            diagnosis_id INTEGER PRIMARY KEY AUTOINCREMENT,
            system TEXT, fault_id TEXT, diagnosis_name TEXT,
            confidence REAL, severity TEXT, status TEXT,
            evidence TEXT, created_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expectations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system TEXT, name TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS probe_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system TEXT, score REAL
        )
    """)
    conn.commit()
    conn.close()


def _seed_test_data(db_path, system="test_sys", events=5, last_seen_offset=0):
    """填充测试数据"""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    now = time.time()
    conn.execute(
        "INSERT OR REPLACE INTO system_pid (system, pid, last_seen, mode) VALUES (?,?,?,?)",
        (system, 99999, now - last_seen_offset, "white"),
    )
    for i in range(events):
        conn.execute(
            "INSERT INTO events (system, event_type, payload, timestamp) VALUES (?,?,?,?)",
            (system, f"evt_{i}", json.dumps({"seq": i}), now + i),
        )
    year = time.strftime("%Y")
    conn.execute(
        f"INSERT INTO diagnoses_{year} (system, fault_id, diagnosis_name, confidence, severity, status, created_at) VALUES (?,?,?,?,?,?,?)",
        (system, "DX-001", "Test diagnosis", 0.9, "P1", "pending", now),
    )
    conn.execute(
        "INSERT INTO expectations (system, name) VALUES (?,?)",
        (system, "expected_always"),
    )
    conn.execute(
        "INSERT INTO probe_health (system, score) VALUES (?,?)",
        (system, 0.85),
    )
    conn.commit()
    conn.close()


class TestProbeList(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home, self.ming, self.hot, self.cold, self.db, self.paused = _make_test_home(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def _run_list(self):
        captured = io.StringIO()
        with patch("cli_admin.Path.home", return_value=self.home):
            with patch("sys.stdout", captured):
                _cmd_probe_list(self.db)
        return captured.getvalue()

    def test_no_db(self):
        output = self._run_list()
        self.assertIn("暂无已注册", output)

    def test_empty_db(self):
        _setup_db(self.db)
        output = self._run_list()
        self.assertIn("暂无已注册", output)

    def test_single_probe(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "test_sys", events=3)
        output = self._run_list()
        self.assertIn("test_sys", output)
        self.assertIn("white", output)
        self.assertIn("3", output)

    def test_active_probe(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "live_sys", last_seen_offset=0)
        output = self._run_list()
        self.assertIn("活跃", output)
        self.assertIn("live_sys", output)

    def test_offline_probe(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "old_sys", last_seen_offset=100000)
        output = self._run_list()
        self.assertIn("离线", output)
        self.assertIn("old_sys", output)

    def test_paused_probe(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "sleepy_sys", last_seen_offset=100000)
        self.paused.mkdir(parents=True, exist_ok=True)
        (self.paused / "sleepy_sys").write_text("")
        output = self._run_list()
        self.assertIn("已暂停", output)

    def test_multiple_sorted(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "system_a", last_seen_offset=0)
        _seed_test_data(self.db, "system_b", last_seen_offset=100)
        output = self._run_list()
        pos_a = output.index("system_a")
        pos_b = output.index("system_b")
        self.assertLess(pos_a, pos_b, "最近的排在前")


class TestProbeUninstall(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.home, self.ming, self.hot, self.cold, self.db, self.paused = _make_test_home(self.td.name)

    def tearDown(self):
        self.td.cleanup()

    def _make_args(self, system, **kwargs):
        """构造模拟的 argparse.Namespace"""
        class Args:
            pass
        a = Args()
        a.system = system
        a.dry_run = kwargs.get("dry_run", False)
        a.force = kwargs.get("force", False)
        a.keep_data = kwargs.get("keep_data", False)
        return a

    def _run_uninstall(self, args):
        with patch("cli_admin.Path.home", return_value=self.home):
            with patch("archiver_exclude.Path.home", return_value=self.home):
                try:
                    _cmd_probe_uninstall(self.db, self.hot, self.cold, self.paused, args)
                except SystemExit:
                    pass

    def _capture_uninstall(self, args):
        captured = io.StringIO()
        with patch("cli_admin.Path.home", return_value=self.home):
            with patch("archiver_exclude.Path.home", return_value=self.home):
                with patch("sys.stdout", captured):
                    try:
                        _cmd_probe_uninstall(self.db, self.hot, self.cold, self.paused, args)
                    except SystemExit:
                        pass
        return captured.getvalue()

    # ── 受保护系统 ──
    def test_protected_admin(self):
        args = self._make_args("__admin__", force=True)
        output = self._capture_uninstall(args)
        self.assertIn("不能卸载", output)

    def test_protected_self_health(self):
        args = self._make_args("__self_health__", force=True)
        output = self._capture_uninstall(args)
        self.assertIn("不能卸载", output)

    def test_protected_host(self):
        args = self._make_args("__host__", force=True)
        output = self._capture_uninstall(args)
        self.assertIn("不能卸载", output)

    def test_protected_unknown(self):
        args = self._make_args("unknown", force=True)
        output = self._capture_uninstall(args)
        self.assertIn("不能卸载", output)

    # ── dry-run ──
    def test_dry_run_no_changes(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "dry_test", events=5)
        args = self._make_args("dry_test", dry_run=True)
        output = self._capture_uninstall(args)
        self.assertIn("dry-run", output)
        self.assertIn("dry_test", output)
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        cnt = conn.execute("SELECT COUNT(*) FROM events WHERE system='dry_test'").fetchone()[0]
        conn.close()
        self.assertEqual(cnt, 5, "dry-run 不应删除数据")

    # ── keep-data ──
    def test_keep_data_retains_db(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "keep_test", events=3)
        self.hot.mkdir(parents=True, exist_ok=True)
        hot_file = self.hot / "keep_test_20260101_000000_12345.jsonl"
        hot_file.write_text('{"test":1}\n')
        args = self._make_args("keep_test", force=True, keep_data=True)
        self._run_uninstall(args)
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        cnt = conn.execute("SELECT COUNT(*) FROM events WHERE system='keep_test'").fetchone()[0]
        conn.close()
        self.assertEqual(cnt, 3, "keep-data 应保留 DB 记录")
        self.assertFalse(hot_file.exists(), "热轨文件应被删除")
        self.assertTrue((self.paused / "keep_test").exists(), "应创建暂停标记")

    # ── 活跃探针警告 ──
    def test_active_probe_warning(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "alive_sys", events=2, last_seen_offset=0)
        args = self._make_args("alive_sys", dry_run=True)
        output = self._capture_uninstall(args)
        self.assertIn("可能仍在运行", output)

    # ── 完整卸载 ──
    def test_full_uninstall(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "full_test", events=10)
        self.hot.mkdir(parents=True, exist_ok=True)
        for i in range(3):
            (self.hot / f"full_test_2026010{i}_000000_{i}2345.jsonl").write_text('{"x":' + str(i) + '}\n')
        self.cold.mkdir(parents=True, exist_ok=True)
        (self.cold / "full_test_cold.jsonl").write_text('{"cold":true}\n')
        args = self._make_args("full_test", force=True)
        self._run_uninstall(args)

        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        evt_cnt = conn.execute("SELECT COUNT(*) FROM events WHERE system='full_test'").fetchone()[0]
        pid_cnt = conn.execute("SELECT COUNT(*) FROM system_pid WHERE system='full_test'").fetchone()[0]
        exp_cnt = conn.execute("SELECT COUNT(*) FROM expectations WHERE system='full_test'").fetchone()[0]
        hlth_cnt = conn.execute("SELECT COUNT(*) FROM probe_health WHERE system='full_test'").fetchone()[0]
        year = time.strftime("%Y")
        dx_cnt = conn.execute(f"SELECT COUNT(*) FROM diagnoses_{year} WHERE system='full_test'").fetchone()[0]
        conn.close()

        self.assertEqual(evt_cnt, 0, "events 应清空")
        self.assertEqual(pid_cnt, 0, "system_pid 应清空")
        self.assertEqual(exp_cnt, 0, "expectations 应清空")
        self.assertEqual(hlth_cnt, 0, "probe_health 应清空")
        self.assertEqual(dx_cnt, 0, "诊断应清空")
        self.assertTrue((self.paused / "full_test").exists(), "应创建暂停标记")

        hot_left = list(self.hot.glob("full_test_*.jsonl"))
        self.assertEqual(len(hot_left), 0, "热轨文件应清空")

        backup_files = list(self.cold.glob("full_test_backup_*.jsonl"))
        self.assertEqual(len(backup_files), 1, "应有备份文件")
        cold_left = list(self.cold.glob("full_test_*.jsonl"))
        cold_others = [f for f in cold_left if "backup" not in f.name]
        self.assertEqual(len(cold_others), 0, "原冷轨文件应清空（备份除外）")
        backup_content = backup_files[0].read_text().strip().split("\n")
        self.assertEqual(len(backup_content), 10, "备份应包含所有事件")

    # ── 无DB记录仅清理文件 ──
    def test_no_db_records_file_only(self):
        self.hot.mkdir(parents=True, exist_ok=True)
        (self.hot / "ghost_20260101_000000_12345.jsonl").write_text('{"x":1}\n')
        args = self._make_args("ghost", force=True)
        self._run_uninstall(args)
        self.assertFalse((self.hot / "ghost_20260101_000000_12345.jsonl").exists(), "应清理文件")
        self.assertTrue((self.paused / "ghost").exists(), "应创建暂停标记")

    # ── 审计日志 ──
    def test_audit_log_created(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "audit_test", events=1)
        args = self._make_args("audit_test", force=True)
        self._run_uninstall(args)
        audit_files = list(self.hot.glob("__probe_uninstall__*.jsonl"))
        self.assertEqual(len(audit_files), 1)
        audit = json.loads(audit_files[0].read_text())
        self.assertEqual(audit["event_type"], "__probe_uninstall__")
        self.assertEqual(audit["payload"]["target_system"], "audit_test")

    # ── 多年诊断表 ──
    def test_multi_year_diagnosis_cleanup(self):
        _setup_db(self.db)
        _seed_test_data(self.db, "old_dx_test", events=2)
        conn = sqlite3.connect(str(self.db))
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS diagnoses_2025 (
                diagnosis_id INTEGER PRIMARY KEY AUTOINCREMENT,
                system TEXT, fault_id TEXT, diagnosis_name TEXT,
                confidence REAL, severity TEXT, status TEXT,
                evidence TEXT, created_at REAL
            )
        """)
        conn.execute(
            "INSERT INTO diagnoses_2025 (system, fault_id, diagnosis_name, confidence, severity, status, created_at) VALUES (?,?,?,?,?,?,?)",
            ("old_dx_test", "OLD-001", "Old diagnosis", 0.5, "P2", "resolved", time.time() - 365 * 86400),
        )
        conn.commit()
        conn.close()
        args = self._make_args("old_dx_test", force=True)
        self._run_uninstall(args)

        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        dx2025 = conn.execute("SELECT COUNT(*) FROM diagnoses_2025 WHERE system='old_dx_test'").fetchone()[0]
        dx2026 = conn.execute(f"SELECT COUNT(*) FROM diagnoses_{time.strftime('%Y')} WHERE system='old_dx_test'").fetchone()[0]
        conn.close()
        self.assertEqual(dx2025, 0, "2025年诊断应清空")
        self.assertEqual(dx2026, 0, "今年诊断应清空")

    # ── 系统退出状态 ──
    def test_protected_system_exits_with_code_1(self):
        args = self._make_args("__admin__", force=True)
        with patch("cli_admin.Path.home", return_value=self.home):
            with patch("archiver_exclude.Path.home", return_value=self.home):
                with self.assertRaises(SystemExit) as ctx:
                    _cmd_probe_uninstall(self.db, self.hot, self.cold, self.paused, args)
                self.assertEqual(ctx.exception.code, 1)


if __name__ == "__main__":
    unittest.main()
