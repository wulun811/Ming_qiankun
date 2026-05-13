# test_probe_agentscope_mock.py —— AgentScope 适配器 Mock 测试
# 测试 MingjingSpanProcessor 的 payload 提取逻辑
# 运行：pytest tests/test_probe_agentscope_mock.py -v

import unittest
from unittest.mock import Mock, patch, MagicMock, PropertyMock
import sys
import os

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

os.environ["MING_LANG"] = "zh"
from adapters.probe_agentscope import (
    MingjingSpanProcessor,
    check_agentscope_version,
)


class TestCheckAgentscopeVersion(unittest.TestCase):
    """测试版本检查函数"""

    @patch(
        "builtins.__import__", side_effect=ImportError("No module named 'agentscope'")
    )
    def test_version_not_installed(self, mock_import):
        """测试 AgentScope 未安装"""
        with self.assertRaises(RuntimeError) as ctx:
            check_agentscope_version()
        self.assertIn("未安装", str(ctx.exception))

    @patch("builtins.__import__")
    def test_version_compatible(self, mock_import):
        """测试兼容版本 1.0.18"""
        mock_agentscope = MagicMock()
        mock_agentscope.__version__ = "1.0.18"
        mock_import.return_value = mock_agentscope

        version = check_agentscope_version()
        self.assertEqual(version, "1.0.18")

    @patch("builtins.__import__")
    def test_version_too_old(self, mock_import):
        """测试旧版本 1.0.17"""
        mock_agentscope = MagicMock()
        mock_agentscope.__version__ = "1.0.17"
        mock_import.return_value = mock_agentscope

        with self.assertRaises(RuntimeError) as ctx:
            check_agentscope_version()
        self.assertIn("不兼容", str(ctx.exception))

    @patch("builtins.__import__")
    def test_version_too_new(self, mock_import):
        """测试新版本 1.1.0"""
        mock_agentscope = MagicMock()
        mock_agentscope.__version__ = "1.1.0"
        mock_import.return_value = mock_agentscope

        with self.assertRaises(RuntimeError) as ctx:
            check_agentscope_version()
        self.assertIn("不兼容", str(ctx.exception))


class TestMingjingSpanProcessor(unittest.TestCase):
    """测试 SpanProcessor payload 提取"""

    def setUp(self):
        self.processor = MingjingSpanProcessor(hot_dir="/tmp/test_ming")
        # Mock probe
        self.processor.probe = Mock()
        self.processor._version_checked = True  # 跳过版本检查

    def _make_span(
        self, name, attributes, is_ok=True, is_error=False, start_time=0, end_time=0
    ):
        """创建 Mock span"""
        span = Mock()
        span.name = name
        span.attributes = attributes
        span.status = Mock(is_ok=is_ok, is_error=is_error)
        span.start_time = start_time
        span.end_time = end_time
        return span

    def test_llm_span_success(self):
        """测试 LLM span → llm_invoke 事件"""
        span = self._make_span(
            name="chat gpt-4",
            attributes={
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 50,
                "gen_ai.request.model": "gpt-4",
                "gen_ai.response.finish_reasons": ["stop"],
                "gen_ai.conversation.id": "session-123",
            },
            start_time=0,
            end_time=2_000_000_000,  # 2 seconds (ns)
        )

        payload = self.processor._extract_llm_payload(span)

        self.assertEqual(payload["event_type"], "llm_invoke")
        self.assertEqual(payload["payload"]["layer_llm"]["input_tokens"], 100)
        self.assertEqual(payload["payload"]["layer_llm"]["output_tokens"], 50)
        self.assertEqual(payload["payload"]["layer_llm"]["model"], "gpt-4")
        self.assertEqual(payload["payload"]["layer_llm"]["finish_reason"], "stop")
        self.assertEqual(payload["payload"]["layer_llm"]["latency_ms"], 2000)
        self.assertEqual(payload["payload"]["layer_network"]["status_code"], 200)
        self.assertEqual(payload["payload"]["layer_agent"]["session_id"], "session-123")

    def test_llm_span_error(self):
        """测试 LLM span error → error 事件"""
        span = self._make_span(
            name="chat gpt-4",
            attributes={
                "gen_ai.usage.input_tokens": 0,
                "gen_ai.request.model": "gpt-4",
            },
            is_ok=False,
            is_error=True,
        )

        payload = self.processor._extract_llm_payload(span)

        self.assertEqual(payload["event_type"], "error")
        self.assertEqual(payload["payload"]["error_type"], "llm_api_error")

    def test_tool_span_success(self):
        """测试工具 span → tool_call 事件"""
        span = self._make_span(
            name="execute_tool python_exec",
            attributes={
                "gen_ai.tool.name": "python_exec",
                "gen_ai.tool.call.arguments": '{"code": "print(1)"}',
                "gen_ai.conversation.id": "session-123",
            },
            start_time=0,
            end_time=100_000_000,  # 100ms
        )

        payload = self.processor._extract_tool_payload(span)

        self.assertEqual(payload["event_type"], "tool_call")
        self.assertEqual(payload["payload"]["layer_tool"]["tool_name"], "python_exec")
        self.assertEqual(payload["payload"]["layer_tool"]["tool_status"], "success")
        self.assertEqual(payload["payload"]["layer_tool"]["execution_ms"], 100)

    def test_agent_span_success(self):
        """测试 Agent span → agent_invoke 事件"""
        span = self._make_span(
            name="invoke_agent my_agent",
            attributes={
                "gen_ai.agent.id": "agent-001",
                "gen_ai.agent.name": "my_agent",
                "gen_ai.conversation.id": "session-123",
            },
            start_time=0,
            end_time=500_000_000,  # 500ms
        )

        payload = self.processor._extract_agent_payload(span)

        self.assertEqual(payload["event_type"], "agent_invoke")
        self.assertEqual(payload["payload"]["layer_agent"]["agent_id"], "agent-001")
        self.assertEqual(payload["payload"]["layer_agent"]["agent_name"], "my_agent")
        self.assertEqual(payload["payload"]["layer_agent"]["latency_ms"], 500)

    def test_on_end_llm_span(self):
        """测试 on_end 完整流程 - LLM span"""
        span = self._make_span(
            name="chat qwen-plus",
            attributes={
                "gen_ai.usage.input_tokens": 200,
                "gen_ai.usage.output_tokens": 100,
                "gen_ai.request.model": "qwen-plus",
                "gen_ai.response.finish_reasons": ["stop"],
                "gen_ai.conversation.id": "session-456",
            },
            start_time=0,
            end_time=3_000_000_000,
        )

        self.processor.on_end(span)

        # 验证 probe.emit 被调用
        self.processor.probe.emit.assert_called_once()
        event_type, payload = self.processor.probe.emit.call_args[0]
        self.assertEqual(event_type, "llm_invoke")
        self.assertEqual(payload["layer_llm"]["model"], "qwen-plus")

    def test_on_end_tool_span(self):
        """测试 on_end 完整流程 - Tool span"""
        span = self._make_span(
            name="execute_tool search",
            attributes={
                "gen_ai.tool.name": "search",
                "gen_ai.tool.call.arguments": '{"query": "test"}',
                "gen_ai.conversation.id": "session-789",
            },
            start_time=0,
            end_time=50_000_000,
        )

        self.processor.on_end(span)

        self.processor.probe.emit.assert_called_once()
        event_type, payload = self.processor.probe.emit.call_args[0]
        self.assertEqual(event_type, "tool_call")
        self.assertEqual(payload["layer_tool"]["tool_name"], "search")

    def test_on_end_unknown_span(self):
        """测试未知 span 类型 → 跳过"""
        span = self._make_span(
            name="http.request",
            attributes={},
        )

        self.processor.on_end(span)

        # 验证 probe.emit 未被调用
        self.processor.probe.emit.assert_not_called()

    def test_on_end_none_attributes(self):
        """测试 on_end 空 attributes 处理"""
        span = Mock()
        span.name = "chat gpt-4"
        span.attributes = None  # 空 attributes
        span.status = Mock(is_ok=True, is_error=False)
        span.start_time = 0
        span.end_time = 1_000_000_000

        # 不应该抛异常
        self.processor.on_end(span)

        # 空 attributes 应该生成默认 payload
        self.processor.probe.emit.assert_called_once()
