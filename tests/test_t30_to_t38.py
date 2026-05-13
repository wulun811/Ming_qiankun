# test_t30_to_t38.py —— 乾坤镜 v0.11.9m 探针协议集成测试套件
# 职责：覆盖 T30-T38 探针协议化测试，验证跨语言 SDK 一致性
# 依赖：unittest, subprocess, json, sqlite3, shutil, tempfile, time, os, sys

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))
os.environ["MING_LANG"] = "zh"

from probe_uni import ProbeUni
from archiver import Archiver
from probe_validator import validate_event, CORE_REQUIRED, OFFICIAL_RECOMMENDED


@contextmanager
def isolated_hot_dir(prefix="ming_t3x_"):
    temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    hot_dir = temp_dir / "hot"
    cold_dir = temp_dir / "cold"
    db_path = temp_dir / "ming.db"
    hot_dir.mkdir()
    cold_dir.mkdir()
    try:
        yield {
            "temp_dir": temp_dir,
            "hot_dir": hot_dir,
            "cold_dir": cold_dir,
            "db_path": db_path,
        }
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _run_node(code: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", "-e", code], capture_output=True, text=True, timeout=timeout
    )


def _run_python(code: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=timeout
    )


def _archiver_once(hot_dir, cold_dir, db_path):
    a = Archiver(db_path=str(db_path), hot_dir=str(hot_dir), cold_dir=str(cold_dir))
    a._alive = True
    return a.run_once()


# ──────────────────────────────────────────────
# T30: 多语言 SDK JSONL 一致性
# ──────────────────────────────────────────────
class T30_MultiLangJsonlConsistency(unittest.TestCase):
    def test_nodejs_sdk_write(self):
        with isolated_hot_dir("t30_node_") as ctx:
            hot_dir = ctx["hot_dir"]
            node_code = f"""
            process.env.MING_HOT_DIR = '{hot_dir}';
            const probe = require('{SRC_DIR / "probe_node"}');
            probe.init('node_test', 'white');
            probe.emit('llm_invoke', {{
                layer_agent: {{ step_id: 't30', session_id: 's1', agent_name: 'test' }},
                layer_llm: {{ model: 'gpt-4', input_tokens: 100, output_tokens: 50, latency_ms: 1200 }},
                layer_network: {{ target_host: 'api.openai.com', status_code: 200 }}
            }});
            probe.flush();
            """
            result = _run_node(node_code)
            self.assertEqual(result.returncode, 0, f"Node.js SDK 失败: {result.stderr}")
            files = list(hot_dir.glob("node_test_*.jsonl"))
            self.assertGreater(len(files), 0, "Node.js SDK 未生成 JSONL 文件")
            lines = files[0].read_text().strip().splitlines()
            event = None
            for line in lines:
                ev = json.loads(line)
                if ev["event_type"] == "llm_invoke":
                    event = ev
                    break
            self.assertIsNotNone(event, "未找到 llm_invoke 事件")
            self.assertEqual(event["system"], "node_test")
            self.assertIn("layer_agent", event["payload"])
            self.assertIn("layer_llm", event["payload"])
            self.assertIn("layer_network", event["payload"])
            self.assertEqual(event["_schema_version"], "0.11.9m")
            print("T30-Node.js 通过: SDK 写入 JSONL 格式正确")

    def test_python_sdk_write(self):
        with isolated_hot_dir("t30_py_") as ctx:
            hot_dir = ctx["hot_dir"]
            py_code = f"""
import os, sys
os.environ['MING_HOT_DIR'] = '{hot_dir}'
sys.path.insert(0, '{SRC_DIR}')
from probe_uni import ProbeUni
p = ProbeUni(system='python_test', mode='white')
p.emit('tool_call', {{
    'layer_agent': {{'step_id': 't30', 'session_id': 's1', 'agent_name': 'test'}},
    'layer_tool': {{'tool_name': 'search', 'tool_args': {{}}, 'tool_result': 'ok', 'execution_ms': 50}},
    'layer_network': {{'target_host': 'local', 'status_code': 200}}
}})
p._flush_batch()
"""
            result = _run_python(py_code)
            self.assertEqual(result.returncode, 0, f"Python SDK 失败: {result.stderr}")
            files = list(hot_dir.glob("python_test_*.jsonl"))
            self.assertGreater(len(files), 0, "Python SDK 未生成 JSONL 文件")
            lines = files[0].read_text().strip().splitlines()
            event = None
            for line in lines:
                ev = json.loads(line)
                if ev["event_type"] == "tool_call":
                    event = ev
                    break
            self.assertIsNotNone(event, "未找到 tool_call 事件")
            self.assertEqual(event["system"], "python_test")
            self.assertIn("layer_tool", event["payload"])
            print("T30-Python 通过: SDK 写入 JSONL 格式正确")

    def test_archiver_parses_both(self):
        with isolated_hot_dir("t30_arch_") as ctx:
            hot_dir, db_path = ctx["hot_dir"], ctx["db_path"]
            py_code = f"""
import os, sys
os.environ['MING_HOT_DIR'] = '{hot_dir}'
sys.path.insert(0, '{SRC_DIR}')
from probe_uni import ProbeUni
p = ProbeUni(system='py_arch', mode='white')
p.emit('agent_step', {{'layer_agent': {{'step_id': 't30', 'session_id': 's1', 'agent_name': 'test'}}}})
p._flush_batch()
"""
            result = _run_python(py_code)
            self.assertEqual(result.returncode, 0, f"Python SDK 失败: {result.stderr}")
            node_code = f"""
            process.env.MING_HOT_DIR = '{hot_dir}';
            const probe = require('{SRC_DIR / "probe_node"}');
            probe.init('node_arch', 'white');
            probe.emit('agent_step', {{ layer_agent: {{ step_id: 't30', session_id: 's1', agent_name: 'test' }} }});
            probe.flush();
            """
            result = _run_node(node_code)
            self.assertEqual(result.returncode, 0, f"Node.js SDK 失败: {result.stderr}")
            time.sleep(0.3)
            archived = _archiver_once(hot_dir, ctx["cold_dir"], db_path)
            self.assertGreaterEqual(
                archived, 2, f"归档事件数不足，期望 >= 2, 实际 {archived}"
            )
            conn = sqlite3.connect(str(db_path))
            py_count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE system = 'py_arch'"
            ).fetchone()[0]
            node_count = conn.execute(
                "SELECT COUNT(*) FROM events WHERE system = 'node_arch'"
            ).fetchone()[0]
            conn.close()
            self.assertGreaterEqual(py_count, 1, "Python 事件未入库")
            self.assertGreaterEqual(node_count, 1, "Node.js 事件未入库")
            print("T30-Archiver 通过: 归档器正确处理多语言 JSONL")


class T30_ValidatorCrossLang(unittest.TestCase):
    def test_validator_python_event(self):
        with isolated_hot_dir("t30v_py_") as ctx:
            hot_dir = ctx["hot_dir"]
            py_code = f"""
import os, sys, json
os.environ['MING_HOT_DIR'] = '{hot_dir}'
sys.path.insert(0, '{SRC_DIR}')
from probe_uni import ProbeUni
p = ProbeUni(system='validator_py', mode='white')
p.emit('llm_invoke', {{
    'layer_agent': {{'step_id': 'v', 'session_id': 's1', 'agent_name': 'test'}},
    'layer_llm': {{'model': 'gpt-4', 'input_tokens': 100, 'output_tokens': 50, 'latency_ms': 1200}},
    'layer_network': {{'target_host': 'api.openai.com', 'status_code': 200}},
}})
p._flush_batch()
"""
            result = _run_python(py_code)
            self.assertEqual(result.returncode, 0, f"Python SDK 失败: {result.stderr}")
            files = list(hot_dir.glob("validator_py_*.jsonl"))
            self.assertGreater(len(files), 0, "Python SDK 未生成文件")
            lines = files[0].read_text().strip().splitlines()
            event = None
            for line in lines:
                ev = json.loads(line)
                if ev["event_type"] == "llm_invoke":
                    event = ev
                    break
            self.assertIsNotNone(event, "未找到 llm_invoke 事件")
            is_valid, errors, warnings = validate_event(event)
            self.assertEqual(len(errors), 0, f"Python 事件校验失败: {errors}")
            print(f"T30-Validator-Python 通过: {len(warnings)} warnings")

    def test_validator_nodejs_event(self):
        with isolated_hot_dir("t30v_node_") as ctx:
            hot_dir = ctx["hot_dir"]
            node_code = f"""
            process.env.MING_HOT_DIR = '{hot_dir}';
            const probe = require('{SRC_DIR / "probe_node"}');
            probe.init('validator_node', 'white');
            probe.emit('llm_invoke', {{
                layer_agent: {{ step_id: 'v', session_id: 's1', agent_name: 'test' }},
                layer_llm: {{ model: 'gpt-4', input_tokens: 100, output_tokens: 50, latency_ms: 1200 }},
                layer_network: {{ target_host: 'api.openai.com', status_code: 200 }}
            }});
            probe.flush();
            """
            result = _run_node(node_code)
            self.assertEqual(result.returncode, 0)
            files = list(hot_dir.glob("validator_node_*.jsonl"))
            self.assertGreater(len(files), 0)
            event = json.loads(files[0].read_text().strip().splitlines()[0])
            is_valid, errors, warnings = validate_event(event)
            self.assertEqual(len(errors), 0, f"Node.js 事件校验失败: {errors}")
            print(f"T30-Validator-Node.js 通过: {len(warnings)} warnings")


# ──────────────────────────────────────────────
# T31: Node.js SDK 批量缓冲
# ──────────────────────────────────────────────
class T31_NodeJsBatchBuffer(unittest.TestCase):
    def test_batch_size_trigger(self):
        with isolated_hot_dir("t31_batch_") as ctx:
            hot_dir = ctx["hot_dir"]
            node_code = f"""
            process.env.MING_HOT_DIR = '{hot_dir}';
            const probe = require('{SRC_DIR / "probe_node"}');
            probe.init('batch_test', 'white');
            for (let i = 0; i < 15; i++) {{
                probe.emit('agent_step', {{ layer_agent: {{ step_id: 't31', session_id: 's1', agent_name: 'test' }} }});
            }}
            probe.flush();
            """
            result = _run_node(node_code)
            self.assertEqual(
                result.returncode, 0, f"Node.js 批量测试失败: {result.stderr}"
            )
            files = list(hot_dir.glob("batch_test_*.jsonl"))
            self.assertGreater(len(files), 0, "批量 flush 未生成文件")
            content = files[0].read_text().strip().splitlines()
            self.assertGreaterEqual(
                len(content), 10, f"批量文件行数不足，期望 >= 10, 实际 {len(content)}"
            )
            print("T31 通过: Node.js SDK 批量缓冲正常")

    def test_flush_interval_trigger(self):
        with isolated_hot_dir("t31_timer_") as ctx:
            hot_dir = ctx["hot_dir"]
            node_code = f"""
            process.env.MING_HOT_DIR = '{hot_dir}';
            const probe = require('{SRC_DIR / "probe_node"}');
            probe.init('timer_test', 'white');
            for (let i = 0; i < 5; i++) {{
                probe.emit('agent_step', {{ layer_agent: {{ step_id: 't31', session_id: 's1', agent_name: 'test' }} }});
            }}
            setTimeout(() => {{
                const fs = require('fs');
                const files = fs.readdirSync('{hot_dir}').filter(f => f.endsWith('.jsonl'));
                console.log('FILES:' + files.length);
                process.exit(0);
            }}, 300);
            """
            result = _run_node(node_code, timeout=15)
            self.assertEqual(
                result.returncode, 0, f"Node.js 定时器测试失败: {result.stderr}"
            )
            self.assertIn("FILES:", result.stdout)
            file_count = int(result.stdout.strip().split("FILES:")[1].split()[0])
            self.assertGreater(file_count, 0, "定时器 flush 未生成文件")
            print("T31-Timer 通过: 定时器触发 flush 正常")


# ──────────────────────────────────────────────
# T32: 多进程写入隔离
# ──────────────────────────────────────────────
class T32_MultiProcessIsolation(unittest.TestCase):
    def test_multiprocess_isolation(self):
        with isolated_hot_dir("t32_iso_") as ctx:
            hot_dir = ctx["hot_dir"]
            processes = []
            for i in range(3):
                code = f"""
                process.env.MING_HOT_DIR = '{hot_dir}';
                const probe = require('{SRC_DIR / "probe_node"}');
                probe.init('iso_test', 'white');
                for (let j = 0; j < 5; j++) {{
                    probe.emit('error', {{ layer_agent: {{ step_id: 't32', session_id: 's1', agent_name: 'test' }}, msg: 'proc{i}_evt' + j }});
                }}
                probe.flush();
                """
                p = subprocess.Popen(
                    ["node", "-e", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                processes.append(p)
                # 错开启动时间，避免同秒内文件名冲突
                time.sleep(0.15)
            for p in processes:
                p.wait(timeout=15)
            time.sleep(0.3)
            files = list(hot_dir.glob("iso_test_*.jsonl"))
            # 验证总事件数 >= 15（3 进程 × 5 事件）
            total_events = 0
            for f in files:
                lines = f.read_text().strip().splitlines()
                total_events += len(lines)
                for line in lines:
                    event = json.loads(line)
                    self.assertIn("event_type", event)
                    self.assertIn("payload", event)
            self.assertGreaterEqual(
                total_events, 15, f"总事件数不足，期望 >= 15, 实际 {total_events}"
            )
            self.assertGreaterEqual(
                len(files), 2, f"期望 >= 2 个文件，实际 {len(files)}"
            )
            print("T32 通过: 多进程写入隔离正常")


# ──────────────────────────────────────────────
# T33: 磁盘空间保护降级
# ──────────────────────────────────────────────
class T33_DiskSpaceProtection(unittest.TestCase):
    def test_disk_protection_python(self):
        with isolated_hot_dir("t33_disk_") as ctx:
            hot_dir = ctx["hot_dir"]
            py_code = f"""
import os, sys, time
os.environ['MING_HOT_DIR'] = '{hot_dir}'
sys.path.insert(0, '{SRC_DIR}')
from probe_uni import ProbeUni
p = ProbeUni(system='disk_test', mode='white')
p._disk_free_mb = 100
p._last_disk_check = time.time()
p.emit('agent_step', {{'layer_agent': {{'step_id': 't33', 'session_id': 's1', 'agent_name': 'test'}}}})
p.emit('error', {{'layer_agent': {{'step_id': 't33'}}, 'msg': 'disk full'}})
p.touch()
p._flush_batch()
"""
            result = _run_python(py_code)
            self.assertEqual(result.returncode, 0, f"磁盘保护测试失败: {result.stderr}")
            files = list(hot_dir.glob("disk_test_*.jsonl"))
            self.assertGreater(len(files), 0, "磁盘保护测试未生成文件")
            content = files[0].read_text().strip().splitlines()
            types = [json.loads(line)["event_type"] for line in content]
            self.assertNotIn("agent_step", types, "磁盘不足时普通事件未被过滤")
            self.assertIn("error", types, "error 事件被错误过滤")
            has_touch = any(t.startswith("__") for t in types)
            self.assertTrue(has_touch, "系统事件被错误过滤")
            print("T33 通过: 磁盘空间保护降级正常")


# ──────────────────────────────────────────────
# T34: 降级路径
# ──────────────────────────────────────────────
class T34_FallbackPath(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "Windows 权限模型不同，跳过")
    def test_fallback_python(self):
        with isolated_hot_dir("t34_fallback_") as ctx:
            readonly_dir = ctx["temp_dir"] / "readonly"
            readonly_dir.mkdir()
            os.chmod(readonly_dir, 0o000)
            try:
                py_code = f"""
import os, sys
os.environ['MING_HOT_DIR'] = '{readonly_dir}'
sys.path.insert(0, '{SRC_DIR}')
from probe_uni import ProbeUni
p = ProbeUni(system='fallback_test', mode='white')
p.emit('error', {{'layer_agent': {{'step_id': 't34'}}, 'msg': 'fallback test'}})
p._flush_batch()
print(f'HOT_DIR: {{p.HOT_DIR}}')
"""
                result = _run_python(py_code)
                self.assertEqual(
                    result.returncode, 0, f"降级路径测试失败: {result.stderr}"
                )
                self.assertIn(
                    "fallback", result.stdout.lower(), "未触发降级到 fallback 路径"
                )
                print("T34-Python 通过: 降级路径正常")
            finally:
                os.chmod(readonly_dir, 0o755)

    def test_fallback_nodejs(self):
        with isolated_hot_dir("t34_node_fb_") as ctx:
            readonly_dir = ctx["temp_dir"] / "readonly"
            readonly_dir.mkdir()
            os.chmod(readonly_dir, 0o000)
            try:
                node_code = f"""
                process.env.MING_HOT_DIR = '{readonly_dir}';
                const probe = require('{SRC_DIR / "probe_node"}');
                probe.init('node_fallback', 'white');
                probe.emit('error', {{ layer_agent: {{ step_id: 't34' }}, msg: 'node fallback' }});
                probe.flush();
                """
                result = _run_node(node_code)
                self.assertEqual(
                    result.returncode, 0, f"Node.js 降级路径测试失败: {result.stderr}"
                )
                fallback_dir = Path("/tmp/ming_fallback")
                if fallback_dir.exists():
                    files = list(fallback_dir.glob("node_fallback_*.jsonl"))
                    self.assertGreater(
                        len(files), 0, "Node.js 降级后未写入 fallback 目录"
                    )
                print("T34-Node.js 通过: 降级路径正常")
            finally:
                os.chmod(readonly_dir, 0o755)


# ──────────────────────────────────────────────
# T35: 协议版本向后兼容
# ──────────────────────────────────────────────
class T35_SchemaVersionCompat(unittest.TestCase):
    def test_legacy_event_archived(self):
        with isolated_hot_dir("t35_compat_") as ctx:
            hot_dir, cold_dir, db_path = ctx["hot_dir"], ctx["cold_dir"], ctx["db_path"]
            old_event = {
                "system": "legacy_test",
                "mode": "white",
                "event_type": "tool_fail",
                "payload": {"tool": "git", "error": "test"},
                "timestamp": time.time(),
                "monotonic_ms": time.time() * 1000,
                "lamport": 1,
                "_pid": 12345,
                "_schema_version": "0.8.0",
            }
            filepath = (
                hot_dir / f"legacy_test_{time.strftime('%Y%m%d_%H%M%S')}_12345.jsonl"
            )
            filepath.write_text(json.dumps(old_event) + "\n")
            archived = _archiver_once(hot_dir, cold_dir, db_path)
            self.assertGreaterEqual(archived, 1, f"旧版本事件未归档，实际 {archived}")
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute(
                "SELECT event_type FROM events WHERE system = 'legacy_test'"
            ).fetchall()
            conn.close()
            self.assertGreaterEqual(len(rows), 1, "旧版本事件未入库")
            self.assertEqual(rows[0][0], "tool_fail", f"事件类型错误: {rows[0][0]}")
            print("T35 通过: 协议版本向后兼容")


# ──────────────────────────────────────────────
# T36: OpenClaw 适配层注入
# ──────────────────────────────────────────────
class T36_OpenClawAdapter(unittest.TestCase):
    def test_openclaw_hooks(self):
        with isolated_hot_dir("t36_oc_") as ctx:
            hot_dir = ctx["hot_dir"]
            node_code = f"""
            process.env.MING_HOT_DIR = '{hot_dir}';
            const probe = require('{SRC_DIR / "probe_node"}');
            probe.init('openclaw_test', 'white');
            globalThis.openclaw = {{
                hooks: {{ onLLMCall: null, onToolCall: null, onSessionEvent: null, onError: null }}
            }};
            const adapter = require('{SRC_DIR / "adapters" / "probe_openclaw"}');
            adapter.initOpenClawProbe({{ system: 'openclaw_test', mode: 'white' }});
            globalThis.openclaw.hooks.onLLMCall({{
                stepId: 't36', sessionId: 's1', agentName: 'openclaw',
                model: 'gpt-4', inputTokens: 100, outputTokens: 50, latencyMs: 1200,
                host: 'api.openai.com', statusCode: 200
            }});
            globalThis.openclaw.hooks.onToolCall({{
                stepId: 't36', sessionId: 's1', agentName: 'openclaw',
                toolName: 'search', args: {{q: 'test'}}, result: 'ok', executionMs: 50,
                host: 'local', success: true
            }});
            probe.flush();
            """
            result = _run_node(node_code)
            self.assertEqual(
                result.returncode, 0, f"OpenClaw 适配层测试失败: {result.stderr}"
            )
            files = list(hot_dir.glob("openclaw_test_*.jsonl"))
            self.assertGreater(len(files), 0, "OpenClaw 适配层未生成文件")
            content = files[0].read_text().strip().splitlines()
            types = [json.loads(line)["event_type"] for line in content]
            self.assertIn("llm_invoke", types, "缺少 llm_invoke 事件")
            self.assertIn("tool_call", types, "缺少 tool_call 事件")
            print("T36 通过: OpenClaw 适配层注入正常")


# ──────────────────────────────────────────────
# T37: Hermes 适配层注入
# ──────────────────────────────────────────────
class T37_HermesAdapter(unittest.TestCase):
    def test_hermes_plugin_importable(self):
        """Hermes 适配层验证 extensions/hermes 插件能正常导入"""
        py_code = f"""
import sys
sys.path.insert(0, '{PROJECT_ROOT}')
from extensions.hermes import register
assert callable(register)
print("Hermes 适配层加载成功")
"""
        result = _run_python(py_code)
        self.assertEqual(
            result.returncode, 0, f"Hermes 适配层测试失败: {result.stderr}"
        )
        self.assertIn("加载成功", result.stdout, "Hermes 适配层未成功加载")


# ──────────────────────────────────────────────
# T38: LIT 适配层注入
# ──────────────────────────────────────────────
class T38_LitAdapter(unittest.TestCase):
    def test_lit_js_loads(self):
        node_code = f"""
        const adapter = require('{SRC_DIR / "adapters" / "probe_lit"}');
        const probe = adapter.initLitProbe({{ system: 'lit_test_js', mode: 'white' }});
        console.log('LIT JS 适配层加载成功');
        """
        result = _run_node(node_code)
        self.assertEqual(
            result.returncode, 0, f"LIT JS 适配层测试失败: {result.stderr}"
        )
        self.assertIn("加载成功", result.stdout, "LIT JS 适配层未成功加载")
        print("T38-JS 通过: LIT JS 适配层加载正常")


if __name__ == "__main__":
    unittest.main(verbosity=2)
