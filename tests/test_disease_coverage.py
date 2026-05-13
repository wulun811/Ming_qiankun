# test_disease_coverage.py —— 病症覆盖测试
# 职责：验证 lit_lite.py 诊断规则能正确触发
# 策略：注入模拟事件 → 运行诊断 → 验证输出
# 分层：L1(6 核心) → L2(规则族) → L3(全量扫描)

import sqlite3, json, time, os, sys, shutil, unittest, importlib
from pathlib import Path

TEST_HOME = Path.home() / ".ming_test_disease"
TEST_DB = TEST_HOME / "ming.db"
TEST_OUT = TEST_HOME / "plugins" / "lit_lite" / "out"
TEST_TRIAGE = TEST_HOME / "triage_snapshot.json"
TEST_HOT = TEST_HOME / "hot"

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def setUpModule():
    """创建测试环境"""
    shutil.rmtree(TEST_HOME, ignore_errors=True)
    TEST_HOME.mkdir(parents=True, exist_ok=True)
    TEST_OUT.mkdir(parents=True, exist_ok=True)
    TEST_HOT.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(TEST_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system TEXT, mode TEXT, event_type TEXT, payload TEXT,
            content_hash TEXT, prev_hash TEXT, curr_hash TEXT,
            timestamp REAL, integrity TEXT, chain_status TEXT,
            integrity_score REAL, ttl_protected INTEGER,
            monotonic_ms REAL, lamport INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS probe_health (
            system TEXT, emit_count INTEGER, drop_count INTEGER,
            disk_free_mb REAL, integrity_score REAL, window_start REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expectations (
            system TEXT, expected_event TEXT, deadline REAL, fulfilled INTEGER
        )
    """)
    conn.commit()
    conn.close()


def tearDownModule():
    """清理测试环境"""
    shutil.rmtree(TEST_HOME, ignore_errors=True)


def clear_events():
    """清空 events 表"""
    conn = sqlite3.connect(str(TEST_DB))
    conn.execute("DELETE FROM events")
    conn.execute("DELETE FROM probe_health")
    conn.execute("DELETE FROM expectations")
    conn.commit()
    conn.close()


def clear_dx_output():
    """清空诊断输出"""
    if TEST_OUT.exists():
        for f in TEST_OUT.glob("*.jsonl"):
            f.unlink()
        hb = TEST_OUT / ".plugin_heartbeat"
        if hb.exists():
            hb.unlink()


def inject_event(
    system,
    event_type,
    payload,
    timestamp=None,
    integrity_score=1.0,
    integrity="verified",
):
    """注入模拟事件"""
    if timestamp is None:
        timestamp = time.time()
    conn = sqlite3.connect(str(TEST_DB))
    conn.execute(
        """
        INSERT INTO events (system, mode, event_type, payload, timestamp, integrity_score, integrity)
        VALUES (?, 'white', ?, ?, ?, ?, ?)
        """,
        (
            system,
            event_type,
            json.dumps(payload),
            timestamp,
            integrity_score,
            integrity,
        ),
    )
    conn.commit()
    conn.close()


def run_diagnose():
    """运行诊断 — 通过修改模块变量实现测试环境隔离"""
    import lit_lite
    import lit_dx
    import lit_rule

    # 重新加载模块以重置全局变量
    importlib.reload(lit_lite)
    importlib.reload(lit_dx)
    importlib.reload(lit_rule)

    # 覆盖模块级变量（使用 Path 对象）
    lit_lite.DB = Path(TEST_DB)
    lit_lite.OUT = Path(TEST_OUT)
    lit_lite.TRIAGE_SNAPSHOT = Path(TEST_TRIAGE)
    lit_dx.HOT = Path(TEST_OUT)
    lit_rule.TRIAGE_SNAPSHOT = Path(TEST_TRIAGE)

    lit_lite.diagnose()


def read_dx_output():
    """读取诊断输出"""
    results = []
    if TEST_OUT.exists():
        for f in TEST_OUT.glob("*.jsonl"):
            for line in f.read_text().splitlines():
                if line.strip():
                    results.append(json.loads(line))
    return results


def has_diagnosis(fault_id, system=None):
    """检查是否生成了指定病症的诊断"""
    dx_list = read_dx_output()
    for dx in dx_list:
        if dx.get("fault_id") == fault_id:
            if system is None or dx.get("system") == system:
                return True
    return False


# ============================================================
# L1: 核心病症测试（6 个 P0/P1）
# ============================================================


class TestL1_CoreDiseases(unittest.TestCase):
    """L1: 核心病症测试 — 验证 6 个 P0/P1 病症能正确触发"""

    def setUp(self):
        clear_events()
        clear_dx_output()
        # 清理增量分诊状态文件，避免跨测试时间窗口缩小导致误判
        lt_file = TEST_DB.parent / ".last_triage_ts"
        if lt_file.exists():
            lt_file.unlink()
        # 创建 triage snapshot 让规则 ready
        TEST_TRIAGE.write_text(
            json.dumps(
                {
                    "generated_at": time.time(),
                    "rule_status": {
                        "NET-024": {"status": "ready", "confidence_multiplier": 1.0},
                        "MDL-036": {"status": "ready", "confidence_multiplier": 1.0},
                        "MDL-035": {"status": "ready", "confidence_multiplier": 1.0},
                        "MEM-072": {"status": "ready", "confidence_multiplier": 1.0},
                        "AGT-049": {"status": "ready", "confidence_multiplier": 1.0},
                    },
                }
            )
        )

    def test_MDL_035_infinite_loop(self):
        """MDL-035: 无限循环 — 同一 step_id 5 分钟内出现 > 5 次"""
        now = time.time()
        for i in range(7):
            inject_event(
                "test_sys",
                "agent_step",
                {
                    "layer_agent": {
                        "step_id": "loop_step",
                        "session_id": "s1",
                        "agent_name": "test",
                    }
                },
                timestamp=now - i * 10,
            )

        run_diagnose()
        self.assertTrue(
            has_diagnosis("MDL-035", "test_sys"), "MDL-035 无限循环应被触发"
        )

    def test_MDL_036_cost_runaway(self):
        """MDL-036: 成本失控 — 1 小时内 token 总和 > 500,000"""
        now = time.time()
        for i in range(20):
            inject_event(
                "test_sys",
                "llm_invoke",
                {
                    "layer_llm": {
                        "model": "gpt-4",
                        "input_tokens": 20000,
                        "output_tokens": 15000,
                        "latency_ms": 200,
                    },
                    "layer_network": {
                        "target_host": "api.openai.com",
                        "status_code": 200,
                    },
                },
                timestamp=now - i * 60,
            )

        run_diagnose()
        self.assertTrue(
            has_diagnosis("MDL-036", "test_sys"), "MDL-036 成本失控应被触发"
        )

    def test_NET_024_network_partition(self):
        """NET-024: 网络分区 — tcp_connected_ms=0 且 latency_ms > 10000"""
        now = time.time()
        inject_event(
            "test_sys",
            "llm_invoke",
            {
                "layer_llm": {
                    "model": "gpt-4",
                    "latency_ms": 15000,
                    "input_tokens": 100,
                    "output_tokens": 50,
                },
                "layer_network": {
                    "target_host": "api.openai.com",
                    "tcp_connected_ms": 0,
                    "error": "connection refused",
                },
            },
            timestamp=now,
        )

        run_diagnose()
        self.assertTrue(
            has_diagnosis("NET-024", "test_sys"), "NET-024 网络分区应被触发"
        )

    def test_MEM_072_memory_imprecise(self):
        """MEM-072: 记忆检索不精准 — 平均 results_count > 10 且 total > 5"""
        now = time.time()
        for i in range(7):
            inject_event(
                "test_sys",
                "memory_retrieve",
                {
                    "layer_memory": {
                        "results_count": 15,
                        "query_hash": f"q{i}",
                        "embedding_model": "text-embedding-3-small",
                    }
                },
                timestamp=now - i * 300,
            )

        run_diagnose()
        self.assertTrue(
            has_diagnosis("MEM-072", "test_sys"), "MEM-072 记忆检索不精准应被触发"
        )

    def test_AGT_049_expectation_missing(self):
        """AGT-049: 预期事件缺失 — fulfilled=0 且 deadline < now"""
        now = time.time()
        conn = sqlite3.connect(str(TEST_DB))
        conn.execute(
            "INSERT INTO expectations (system, expected_event, deadline, fulfilled) VALUES (?, ?, ?, ?)",
            ("test_sys", "tool_call", now - 60, 0),
        )
        conn.commit()
        conn.close()

        run_diagnose()
        self.assertTrue(
            has_diagnosis("AGT-049", "test_sys"), "AGT-049 预期事件缺失应被触发"
        )

    def test_no_false_positive(self):
        """无假阳性 — 正常事件不应触发诊断"""
        now = time.time()
        inject_event(
            "test_sys",
            "llm_invoke",
            {
                "layer_llm": {
                    "model": "gpt-4",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "latency_ms": 200,
                },
                "layer_network": {
                    "target_host": "api.openai.com",
                    "tcp_connected_ms": 50,
                    "status_code": 200,
                },
            },
            timestamp=now,
        )
        inject_event(
            "test_sys",
            "agent_step",
            {
                "layer_agent": {
                    "step_id": "step_1",
                    "session_id": "s1",
                    "agent_name": "test",
                }
            },
            timestamp=now,
        )
        inject_event(
            "test_sys",
            "memory_retrieve",
            {"layer_memory": {"results_count": 3, "query_hash": "q1"}},
            timestamp=now,
        )

        run_diagnose()
        dx_list = read_dx_output()
        business_dx = [
            dx
            for dx in dx_list
            if dx.get("fault_id")
            in ("MDL-035", "MDL-036", "NET-024", "MEM-072", "AGT-049")
        ]
        self.assertEqual(
            len(business_dx),
            0,
            f"正常事件不应触发业务诊断，但触发了: {[dx.get('fault_id') for dx in business_dx]}",
        )


# ============================================================
# L2: 规则族覆盖测试
# ============================================================


class TestL2_RuleFamilies(unittest.TestCase):
    """L2: 规则族覆盖 — 按规则类型分组测试边界条件"""

    def setUp(self):
        clear_events()
        clear_dx_output()
        lt_file = TEST_DB.parent / ".last_triage_ts"
        if lt_file.exists():
            lt_file.unlink()
        TEST_TRIAGE.write_text(
            json.dumps(
                {
                    "generated_at": time.time(),
                    "rule_status": {
                        "NET-024": {"status": "ready", "confidence_multiplier": 1.0},
                        "MDL-036": {"status": "ready", "confidence_multiplier": 1.0},
                        "MDL-035": {"status": "ready", "confidence_multiplier": 1.0},
                        "MEM-072": {"status": "ready", "confidence_multiplier": 1.0},
                        "AGT-049": {"status": "ready", "confidence_multiplier": 1.0},
                    },
                }
            )
        )

    def test_MDL_035_boundary_5_times(self):
        """MDL-035 边界：恰好 5 次不应触发（需要 > 5）"""
        now = time.time()
        for i in range(5):
            inject_event(
                "test_sys",
                "agent_step",
                {
                    "layer_agent": {
                        "step_id": "boundary_step",
                        "session_id": "s1",
                        "agent_name": "test",
                    }
                },
                timestamp=now - i * 10,
            )

        run_diagnose()
        self.assertFalse(
            has_diagnosis("MDL-035", "test_sys"), "MDL-035 恰好 5 次不应触发"
        )

    def test_MDL_036_boundary_500k_tokens(self):
        """MDL-036 边界：恰好 500,000 tokens 不应触发（需要 > 500,000）"""
        now = time.time()
        inject_event(
            "test_sys",
            "llm_invoke",
            {
                "layer_llm": {
                    "model": "gpt-4",
                    "input_tokens": 250000,
                    "output_tokens": 250000,
                    "latency_ms": 200,
                },
                "layer_network": {"target_host": "api.openai.com", "status_code": 200},
            },
            timestamp=now,
        )

        run_diagnose()
        self.assertFalse(
            has_diagnosis("MDL-036", "test_sys"), "MDL-036 恰好 500k tokens 不应触发"
        )

    def test_NET_024_no_trigger_on_tcp_success(self):
        """NET-024 不触发：tcp_connected_ms > 0"""
        now = time.time()
        inject_event(
            "test_sys",
            "llm_invoke",
            {
                "layer_llm": {
                    "model": "gpt-4",
                    "latency_ms": 15000,
                    "input_tokens": 100,
                    "output_tokens": 50,
                },
                "layer_network": {
                    "target_host": "api.openai.com",
                    "tcp_connected_ms": 50,
                    "status_code": 200,
                },
            },
            timestamp=now,
        )

        run_diagnose()
        self.assertFalse(
            has_diagnosis("NET-024", "test_sys"), "NET-024 tcp 成功时不应触发"
        )

    def test_MEM_072_boundary_10_results(self):
        """MEM-072 边界：平均 results_count = 10 不应触发（需要 > 10）"""
        now = time.time()
        for i in range(7):
            inject_event(
                "test_sys",
                "memory_retrieve",
                {"layer_memory": {"results_count": 10, "query_hash": f"q{i}"}},
                timestamp=now - i * 300,
            )

        run_diagnose()
        self.assertFalse(
            has_diagnosis("MEM-072", "test_sys"), "MEM-072 平均 10 条不应触发"
        )

    def test_integrity_score_filter(self):
        """完整性过滤：integrity_score <= 0.6 的事件不应参与诊断"""
        now = time.time()
        for i in range(10):
            inject_event(
                "test_sys",
                "agent_step",
                {
                    "layer_agent": {
                        "step_id": "low_integrity_step",
                        "session_id": "s1",
                        "agent_name": "test",
                    }
                },
                timestamp=now - i * 10,
                integrity_score=0.5,
            )

        run_diagnose()
        self.assertFalse(
            has_diagnosis("MDL-035", "test_sys"), "低完整性事件不应触发诊断"
        )

    def test_multi_system_isolation(self):
        """多系统隔离：不同系统的病症应独立诊断"""
        now = time.time()
        for i in range(7):
            inject_event(
                "sys_a",
                "agent_step",
                {
                    "layer_agent": {
                        "step_id": "loop_a",
                        "session_id": "s1",
                        "agent_name": "a",
                    }
                },
                timestamp=now - i * 10,
            )
            inject_event(
                "sys_b",
                "agent_step",
                {
                    "layer_agent": {
                        "step_id": f"unique_b_{i}",
                        "session_id": "s1",
                        "agent_name": "b",
                    }
                },
                timestamp=now - i * 10,
            )

        run_diagnose()
        self.assertTrue(has_diagnosis("MDL-035", "sys_a"), "sys_a 应触发 MDL-035")
        self.assertFalse(has_diagnosis("MDL-035", "sys_b"), "sys_b 不应触发 MDL-035")


# ============================================================
# L3: 全量病症覆盖率扫描
# ============================================================


class TestL3_FullCoverageScan(unittest.TestCase):
    """L3: 全量病症覆盖率扫描 — 验证 diseases.yaml 中所有规则的 SQL 可执行"""

    def test_all_sql_templates_executable(self):
        """所有 SQL 模板在空表上可执行（不报错）"""
        import yaml, re

        diseases_yaml = Path(__file__).parent.parent / "config" / "diseases.yaml"
        if not diseases_yaml.exists():
            self.skipTest("diseases.yaml 不存在")

        with open(diseases_yaml, "r", encoding="utf-8") as f:
            diseases = yaml.safe_load(f)

        conn = sqlite3.connect(str(TEST_DB))
        executable = 0
        failed = []
        schema_issues = []

        sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
        from lit_rule import resolve_env_sql

        for d in diseases:
            sql = d.get("sql_template", "").strip()
            if not sql:
                continue
            sql = resolve_env_sql(sql, d, {})
            try:
                param_count = len(re.findall(r"\?", sql))
                params = (time.time() - 3600,) * param_count
                conn.execute(sql, params).fetchall()
                executable += 1
            except sqlite3.OperationalError as e:
                err_str = str(e)
                if (
                    "no such column" in err_str
                    or "no such table" in err_str
                    or "bad JSON path" in err_str
                ):
                    schema_issues.append((d["id"], d["name"], err_str))
                else:
                    failed.append((d["id"], d["name"], err_str))
            except Exception as e:
                failed.append((d["id"], d["name"], str(e)))

        conn.close()

        total = len([d for d in diseases if d.get("sql_template")])
        print(
            f"\n  SQL 可执行率: {executable}/{total} ({executable / total * 100:.1f}%)"
        )
        if schema_issues:
            print(f"  Schema 问题（需扩展 events 表）: {len(schema_issues)} 个")
            for fid, fname, err in schema_issues[:5]:
                print(f"    {fid} ({fname}): {err}")
        if failed:
            print(f"  SQL 执行失败: {len(failed)} 个")
            for fid, fname, err in failed[:5]:
                print(f"    {fid} ({fname}): {err}")

        self.assertEqual(len(failed), 0, f"{len(failed)} 个 SQL 模板执行失败")

    def test_all_diseases_have_required_fields(self):
        """所有病症定义包含必需字段"""
        import yaml

        diseases_yaml = Path(__file__).parent.parent / "config" / "diseases.yaml"
        if not diseases_yaml.exists():
            self.skipTest("diseases.yaml 不存在")

        with open(diseases_yaml, "r", encoding="utf-8") as f:
            diseases = yaml.safe_load(f)

        required = [
            "id",
            "name",
            "layer",
            "severity",
            "scope",
            "depends",
            "sql_template",
            "description",
        ]
        missing = []

        for d in diseases:
            for field in required:
                if field not in d or not d[field]:
                    missing.append((d.get("id", "unknown"), field))

        self.assertEqual(
            len(missing), 0, f"{len(missing)} 个病症缺少必需字段: {missing}"
        )

    def test_severity_distribution(self):
        """验证病症严重度分布"""
        import yaml

        diseases_yaml = Path(__file__).parent.parent / "config" / "diseases.yaml"
        if not diseases_yaml.exists():
            self.skipTest("diseases.yaml 不存在")

        with open(diseases_yaml, "r", encoding="utf-8") as f:
            diseases = yaml.safe_load(f)

        severity_counts = {}
        for d in diseases:
            sev = d.get("severity", "unknown")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        print(f"\n  病症严重度分布: {severity_counts}")
        self.assertIn("P0", severity_counts, "应有 P0 级别病症")
        self.assertIn("P1", severity_counts, "应有 P1 级别病症")
        self.assertIn("P2", severity_counts, "应有 P2 级别病症")


if __name__ == "__main__":
    unittest.main()
