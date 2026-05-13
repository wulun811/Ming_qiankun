# test_probe_semantic_kernel_mock.py —— Semantic Kernel 适配器 Mock 测试
# 测试 MingjingToolFilter 和 patch_llm_client 的逻辑
# 运行：pytest tests/test_probe_semantic_kernel_mock.py -v

import os

os.environ["MING_LANG"] = "zh"
import unittest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
import sys
import os

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from adapters.probe_semantic_kernel import (
    MingjingToolFilter,
    check_semantic_kernel_version,
    patch_llm_client,
)


class TestCheckSemanticKernelVersion(unittest.TestCase):
    """测试版本检查函数"""

    @patch(
        "builtins.__import__",
        side_effect=ImportError("No module named 'semantic_kernel'"),
    )
    def test_version_not_installed(self, mock_import):
        """测试 Semantic Kernel 未安装"""
        with self.assertRaises(RuntimeError) as ctx:
            check_semantic_kernel_version()
        self.assertIn("未安装", str(ctx.exception))

    @patch("builtins.__import__")
    def test_version_compatible(self, mock_import):
        """测试兼容版本 1.41.3"""
        mock_sk = MagicMock()
        mock_sk.__version__ = "1.41.3"
        mock_import.return_value = mock_sk

        version = check_semantic_kernel_version()
        self.assertEqual(version, "1.41.3")

    @patch("builtins.__import__")
    def test_version_too_old(self, mock_import):
        """测试旧版本 1.39.0"""
        mock_sk = MagicMock()
        mock_sk.__version__ = "1.39.0"
        mock_import.return_value = mock_sk

        with self.assertRaises(RuntimeError) as ctx:
            check_semantic_kernel_version()
        self.assertIn("不兼容", str(ctx.exception))

    @patch("builtins.__import__")
    def test_version_too_new(self, mock_import):
        """测试新版本 1.42.0"""
        mock_sk = MagicMock()
        mock_sk.__version__ = "1.42.0"
        mock_import.return_value = mock_sk

        with self.assertRaises(RuntimeError) as ctx:
            check_semantic_kernel_version()
        self.assertIn("不兼容", str(ctx.exception))


class TestMingjingToolFilter(unittest.TestCase):
    """测试 Tool Filter"""

    def setUp(self):
        self.filter = MingjingToolFilter(hot_dir="/tmp/test_ming")
        # Mock probe
        self.filter.probe = Mock()
        self.filter._version_checked = True  # 跳过版本检查

    def test_tool_invocation_success(self):
        """测试工具调用成功"""
        # 创建 mock context
        context = Mock()
        context.function = Mock()
        context.function.plugin_name = "test_plugin"
        context.function.name = "test_function"
        context.arguments = {"arg1": "value1"}

        # 创建 mock next
        async def mock_next(ctx):
            pass

        # 运行测试
        import asyncio

        asyncio.run(self.filter.tool_invocation_filter(context, mock_next))

        # 验证 probe.emit 被调用
        self.filter.probe.emit.assert_called_once()
        event_type, payload = self.filter.probe.emit.call_args[0]
        self.assertEqual(event_type, "tool_call")
        self.assertEqual(
            payload["layer_tool"]["tool_name"], "test_plugin.test_function"
        )
        self.assertEqual(payload["layer_tool"]["tool_status"], "success")

    def test_tool_invocation_error(self):
        """测试工具调用失败"""
        # 创建 mock context
        context = Mock()
        context.function = Mock()
        context.function.plugin_name = "test_plugin"
        context.function.name = "test_function"
        context.arguments = {"arg1": "value1"}

        # 创建 mock next (抛出异常)
        async def mock_next(ctx):
            raise ValueError("Test error")

        # 运行测试
        import asyncio

        with self.assertRaises(ValueError):
            asyncio.run(self.filter.tool_invocation_filter(context, mock_next))

        # 验证 probe.emit 被调用 (失败记录)
        self.filter.probe.emit.assert_called_once()
        event_type, payload = self.filter.probe.emit.call_args[0]
        self.assertEqual(event_type, "tool_call")
        self.assertEqual(payload["layer_tool"]["tool_status"], "fail")


class TestInitMingjingProbe(unittest.TestCase):
    """测试初始化函数"""

    @patch("adapters.probe_semantic_kernel.check_semantic_kernel_version")
    @patch("adapters.probe_semantic_kernel.patch_llm_client")
    def test_init_success(self, mock_patch, mock_check):
        """测试初始化成功"""
        mock_check.return_value = "1.41.3"

        from adapters.probe_semantic_kernel import init_mingjing_probe

        tool_filter = init_mingjing_probe(hot_dir="/tmp/test_ming")

        # 验证 patch_llm_client 被调用
        mock_patch.assert_called_once_with(hot_dir="/tmp/test_ming")

        # 验证返回 ToolFilter
        self.assertIsInstance(tool_filter, MingjingToolFilter)


if __name__ == "__main__":
    unittest.main()
