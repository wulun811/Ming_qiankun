# probe_semantic_kernel.py —— v0.11.12 乾坤镜 Semantic Kernel 适配层
# 职责：通过 Monkey-patch + Filter 零侵入拦截 Semantic Kernel LLM/Tool 调用
# 依赖：_python_base.py, _payload_builders.py（零第三方依赖）
# 兼容：semantic-kernel >=1.40.0,<1.42.0

from adapters._python_base import make_adapter_init, instrumented
from adapters._payload_builders import (
    build_llm_event,
    build_tool_event,
    _extract_tokens,
    _extract_finish_reason,
)
import os
import time
import logging
from functools import wraps

# Module-level probe storage for monkey-patch
_mingjing_probe = None

logger = logging.getLogger(__name__)


def _get_content_max_len():
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


def check_semantic_kernel_version():
    """检查 Semantic Kernel 版本兼容性，不兼容时抛出 RuntimeError"""
    try:
        import semantic_kernel
        version = semantic_kernel.__version__
        parts = version.split(".")
        if len(parts) < 3:
            raise RuntimeError(f"Semantic Kernel 版本格式异常: {version}")
        
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

        # 兼容范围：[1.40.0, 1.42.0)
        if major != 1 or minor < 40 or minor >= 42:
            raise RuntimeError(
                f"Semantic Kernel {version} 不兼容，请使用版本范围 [1.40.0, 1.42.0)\n"
                f"解决方法: pip install 'semantic-kernel>=1.40.0,<1.42.0'"
            )
        return version
    except ImportError:
        raise RuntimeError(
            "Semantic Kernel 未安装，请安装: pip install 'semantic-kernel>=1.40.0,<1.42.0'"
        )


class MingjingToolFilter:
    """乾坤镜 Semantic Kernel 探针 - Tool Filter

    通过 Filter 机制拦截工具调用，提取工具调用数据并写入热轨。
    """

    def __init__(self, hot_dir: str = "~/.ming/hot"):
        self.hot_dir = hot_dir
        self.probe = None  # 延迟初始化
        self._version_checked = False

    def _ensure_version(self):
        if not self._version_checked:
            check_semantic_kernel_version()
            self._version_checked = True

    def _ensure_probe(self):
        if self.probe is None:
            from probe_uni import ProbeUni
            ProbeUni.HOT_DIR = __import__('pathlib').Path(self.hot_dir).expanduser()
            self.probe = ProbeUni(system="semantic_kernel")

    async def tool_invocation_filter(self, context, next):
        """Tool Invocation Filter - 拦截工具调用前后"""
        try:
            self._ensure_version()
            self._ensure_probe()

            # 工具调用前
            tool_name = context.function.plugin_name + "." + context.function.name if context.function else "unknown"
            start_time = time.time()

            # 调用下一个 filter 或实际工具
            await next(context)

            # 工具调用后
            execution_ms = int((time.time() - start_time) * 1000)

            payload = {
                "event_type": "tool_call",
                "payload": {
                    "layer_tool": {
                        "tool_name": tool_name,
                        "tool_status": "success",
                        "execution_ms": execution_ms,
                        "tool_input_hash": hash(str(context.arguments)) if context.arguments else 0,
                    },
                    "layer_agent": {
                        "session_id": "",
                    },
                },
            }

            self.probe.emit(payload["event_type"], payload["payload"])

        except Exception as e:
            # 记录失败的工具调用
            if self.probe:
                payload = {
                    "event_type": "tool_call",
                    "payload": {
                        "layer_tool": {
                            "tool_name": tool_name if 'tool_name' in dir() else "unknown",
                            "tool_status": "fail",
                            "execution_ms": int((time.time() - start_time) * 1000) if 'start_time' in dir() else 0,
                            "tool_input_hash": 0,
                        },
                        "layer_agent": {
                            "session_id": "",
                        },
                    },
                }
                self.probe.emit(payload["event_type"], payload["payload"])
            raise


def patch_llm_client(hot_dir: str = "~/.ming/hot"):
    """Monkey-patch Semantic Kernel OpenAI chat completion client

    目标方法：semantic_kernel.connectors.ai.open_ai.services.open_ai_chat_completion_base._inner_get_chat_message_contents
    """
    global _mingjing_probe
    check_semantic_kernel_version()

    from semantic_kernel.connectors.ai.open_ai.services.open_ai_chat_completion_base import OpenAIChatCompletionBase

    original_method = OpenAIChatCompletionBase._inner_get_chat_message_contents

    @wraps(original_method)
    async def patched_method(self, chat_history, settings, *args, **kwargs):
        global _mingjing_probe
        start_time = time.time()
        status_code = 200

        try:
            # 调用原始方法
            result = await original_method(self, chat_history, settings, *args, **kwargs)

            # 提取 usage + finish_reason
            if result and len(result) > 0:
                chat_content = result[0]
                metadata = chat_content.metadata or {}

                # 提取 tokens
                usage = metadata.get("usage")
                input_tokens = 0
                output_tokens = 0
                if usage:
                    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    output_tokens = getattr(usage, "completion_tokens", 0) or 0

                # 提取 finish_reason
                finish_reason = "stop"
                if hasattr(chat_content, "finish_reason") and chat_content.finish_reason:
                    finish_reason = str(chat_content.finish_reason)

                # 提取 model
                model = getattr(self, "ai_model_id", "unknown")

                # 计算延迟
                latency_ms = int((time.time() - start_time) * 1000)

                # 构建 payload
                payload = {
                    "event_type": "llm_invoke",
                    "payload": {
                        "layer_llm": {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "model": model,
                            "finish_reason": finish_reason,
                            "latency_ms": latency_ms,
                        },
                        "layer_network": {
                            "status_code": status_code,
                        },
                        "layer_agent": {
                            "session_id": "",
                        },
                    },
                }

                # 写入热轨
                if _mingjing_probe is None:
                    from probe_uni import ProbeUni
                    ProbeUni.HOT_DIR = __import__('pathlib').Path(hot_dir).expanduser()
                    _mingjing_probe = ProbeUni(system="semantic_kernel")

                _mingjing_probe.emit(payload["event_type"], payload["payload"])

            return result

        except Exception as e:
            status_code = 500
            latency_ms = int((time.time() - start_time) * 1000)

            # 记录错误
            payload = {
                "event_type": "error",
                "payload": {
                    "layer_llm": {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "model": getattr(self, "ai_model_id", "unknown"),
                        "finish_reason": "error",
                        "latency_ms": latency_ms,
                    },
                    "layer_network": {
                        "status_code": status_code,
                    },
                    "layer_agent": {
                        "session_id": "",
                    },
                    "error_type": type(e).__name__,
                },
            }

            if _mingjing_probe is None:
                from probe_uni import ProbeUni
                ProbeUni.HOT_DIR = __import__('pathlib').Path(hot_dir).expanduser()
                _mingjing_probe = ProbeUni(system="semantic_kernel")

            _mingjing_probe.emit(payload["event_type"], payload["payload"])

            raise

    # 应用 monkey-patch
    OpenAIChatCompletionBase._inner_get_chat_message_contents = patched_method
    logger.info("Semantic Kernel OpenAI chat completion monkey-patched")


def init_mingjing_probe(hot_dir: str = "~/.ming/hot") -> MingjingToolFilter:
    """初始化乾坤镜 Semantic Kernel 探针

    使用方式：
        import semantic_kernel
        kernel = semantic_kernel.Kernel()

        from adapters.probe_semantic_kernel import init_mingjing_probe
        tool_filter = init_mingjing_probe()
        kernel.add_filter("function_invocation", tool_filter.tool_invocation_filter)

    环境变量：
        MING_HOT_DIR: 热轨目录（默认 ~/.ming/hot）
        MING_CONTENT_MAX_LEN: 内容截断长度（默认 2000）
    """
    # 检查版本兼容性
    version = check_semantic_kernel_version()
    logger.info(f"Semantic Kernel {version} detected, initializing Mingjing probe")

    # Monkey-patch LLM client
    patch_llm_client(hot_dir=hot_dir)

    # 创建 Tool Filter
    tool_filter = MingjingToolFilter(hot_dir=hot_dir)

    logger.info(f"Mingjing probe initialized (LLM patch + Tool filter)")
    return tool_filter


# 导出
__all__ = [
    "MingjingToolFilter",
    "patch_llm_client",
    "init_mingjing_probe",
    "check_semantic_kernel_version",
]
