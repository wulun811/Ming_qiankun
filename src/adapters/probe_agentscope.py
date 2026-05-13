# probe_agentscope.py —— v0.11.12 乾坤镜 AgentScope 适配层
# 职责：通过 OpenTelemetry SpanProcessor 零侵入拦截 AgentScope LLM/Tool 调用
# 依赖：_python_base.py, _payload_builders.py（零第三方依赖）
# 兼容：AgentScope >=1.0.18,<1.1.0

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


def check_agentscope_version():
    """检查 AgentScope 版本兼容性，不兼容时抛出 RuntimeError"""
    try:
        import agentscope
        version = agentscope.__version__
        parts = version.split(".")
        if len(parts) < 3:
            raise RuntimeError(f"AgentScope 版本格式异常: {version}")
        
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

        # 兼容范围：[1.0.18, 1.1.0)
        if major != 1 or minor >= 1:
            raise RuntimeError(
                f"AgentScope {version} 不兼容，请使用版本范围 [1.0.18, 1.1.0)\n"
                f"解决方法: pip install 'agentscope>=1.0.18,<1.1.0'"
            )
        if minor == 0 and patch < 18:
            raise RuntimeError(
                f"AgentScope {version} 不兼容，请使用版本范围 [1.0.18, 1.1.0)\n"
                f"解决方法: pip install 'agentscope>=1.0.18,<1.1.0'"
            )
        return version
    except ImportError:
        raise RuntimeError(
            "AgentScope 未安装，请安装: pip install 'agentscope>=1.0.18,<1.1.0'"
        )


class MingjingSpanProcessor:
    """乾坤镜 AgentScope 探针 - OpenTelemetry SpanProcessor

    通过拦截 AgentScope 的 OpenTelemetry spans，提取 LLM/Tool 调用数据并写入热轨。
    零侵入设计：无需 monkey-patch AgentScope SDK。
    """

    def __init__(self, hot_dir: str = "~/.ming/hot"):
        self.hot_dir = hot_dir
        self.probe = None  # 延迟初始化
        self._version_checked = False

    def _ensure_version(self):
        if not self._version_checked:
            check_agentscope_version()
            self._version_checked = True

    def _ensure_probe(self):
        if self.probe is None:
            from probe_uni import ProbeUni
            ProbeUni.HOT_DIR = __import__('pathlib').Path(self.hot_dir).expanduser()
            self.probe = ProbeUni(system="agentscope")

    def on_start(self, span, parent_context) -> None:
        # 无需处理（乾坤镜是事后归档）
        pass

    def _on_ending(self, span) -> None:
        # 无需处理（乾坤镜是事后归档）
        pass

    def on_end(self, span) -> None:
        """核心：从 span.attributes 提取乾坤镜 payload 并写入热轨"""
        try:
            self._ensure_version()
            self._ensure_probe()

            span_name = span.name if span.name else ""

            # LLM span: "chat {model}"
            if span_name.startswith("chat "):
                payload = self._extract_llm_payload(span)
                if payload:
                    self.probe.emit(payload["event_type"], payload["payload"])
            # Tool span: "execute_tool {tool_name}"
            elif span_name.startswith("execute_tool "):
                payload = self._extract_tool_payload(span)
                if payload:
                    self.probe.emit(payload["event_type"], payload["payload"])
            # Agent span: "invoke_agent {agent_name}"
            elif span_name.startswith("invoke_agent "):
                payload = self._extract_agent_payload(span)
                if payload:
                    self.probe.emit(payload["event_type"], payload["payload"])
        except Exception as e:
            logger.warning(f"AgentScope probe error: {e}")

    def _extract_llm_payload(self, span) -> dict:
        """从 OpenTelemetry span attributes 提取 llm_invoke payload"""
        attrs = span.attributes if span.attributes else {}

        # 提取 tokens
        input_tokens = attrs.get("gen_ai.usage.input_tokens", 0) or 0
        output_tokens = attrs.get("gen_ai.usage.output_tokens", 0) or 0

        # 提取 finish_reason
        finish_reasons = attrs.get("gen_ai.response.finish_reasons", [])
        finish_reason = finish_reasons[0] if finish_reasons else "stop"

        # 计算延迟 (ns -> ms)
        latency_ms = 0
        start_time = span.start_time
        end_time = span.end_time
        if start_time is not None and end_time is not None:
            try:
                latency_ms = int((int(end_time) - int(start_time)) / 1_000_000)
            except (TypeError, ValueError):
                pass

        # 提取 model
        model = attrs.get("gen_ai.request.model", "unknown")

        # 提取 conversation id
        session_id = attrs.get("gen_ai.conversation.id", "")

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
                    "status_code": 200 if span.status and span.status.is_ok else 500,
                },
                "layer_agent": {
                    "session_id": session_id,
                },
            },
        }

        # 如果 span 有异常，记录 error
        if span.status and not span.status.is_ok:
            payload["event_type"] = "error"
            payload["payload"]["error_type"] = "llm_api_error"

        return payload

    def _extract_tool_payload(self, span) -> dict:
        """从 OpenTelemetry span attributes 提取 tool_call payload"""
        attrs = span.attributes if span.attributes else {}

        tool_name = attrs.get("gen_ai.tool.name", "unknown")

        # 计算延迟
        execution_ms = 0
        start_time = span.start_time
        end_time = span.end_time
        if start_time is not None and end_time is not None:
            try:
                execution_ms = int((int(end_time) - int(start_time)) / 1_000_000)
            except (TypeError, ValueError):
                pass

        # 提取 conversation id
        session_id = attrs.get("gen_ai.conversation.id", "")

        # 提取 tool call arguments (用于 hash)
        tool_args = attrs.get("gen_ai.tool.call.arguments", "")
        tool_input_hash = hash(str(tool_args)) if tool_args else 0

        payload = {
            "event_type": "tool_call",
            "payload": {
                "layer_tool": {
                    "tool_name": tool_name,
                    "tool_status": "success" if span.status and span.status.is_ok else "fail",
                    "execution_ms": execution_ms,
                    "tool_input_hash": tool_input_hash,
                },
                "layer_agent": {
                    "session_id": session_id,
                },
            },
        }

        return payload

    def _extract_agent_payload(self, span) -> dict:
        """从 OpenTelemetry span attributes 提取 agent_invoke payload"""
        attrs = span.attributes if span.attributes else {}

        agent_id = attrs.get("gen_ai.agent.id", "")
        agent_name = attrs.get("gen_ai.agent.name", "unknown")

        # 计算延迟
        latency_ms = 0
        start_time = span.start_time
        end_time = span.end_time
        if start_time is not None and end_time is not None:
            try:
                latency_ms = int((int(end_time) - int(start_time)) / 1_000_000)
            except (TypeError, ValueError):
                pass

        session_id = attrs.get("gen_ai.conversation.id", "")

        payload = {
            "event_type": "agent_invoke",
            "payload": {
                "layer_agent": {
                    "agent_id": agent_id,
                    "agent_name": agent_name,
                    "session_id": session_id,
                    "latency_ms": latency_ms,
                },
            },
        }

        return payload

    def shutdown(self) -> None:
        # 无需处理（探针无状态）
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        # 无需处理（探针实时写入）
        return True


def init_mingjing_tracer(hot_dir: str = "~/.ming/hot") -> None:
    """初始化乾坤镜 AgentScope 探针

    使用方式：
        import agentscope
        agentscope.init(...)  # AgentScope 原生初始化

        from adapters.probe_agentscope import init_mingjing_tracer
        init_mingjing_tracer()  # 添加乾坤镜 SpanProcessor

    环境变量：
        MING_HOT_DIR: 热轨目录（默认 ~/.ming/hot）
        MING_CONTENT_MAX_LEN: 内容截断长度（默认 2000）
    """
    # 检查版本兼容性
    version = check_agentscope_version()
    logger.info(f"AgentScope {version} detected, initializing Mingjing probe")

    # 获取或创建 TracerProvider
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry import trace

    tracer_provider = trace.get_tracer_provider()

    # 添加乾坤镜 SpanProcessor
    if hasattr(tracer_provider, "add_span_processor"):
        processor = MingjingSpanProcessor(hot_dir=hot_dir)
        tracer_provider.add_span_processor(processor)
        logger.info(f"Mingjing SpanProcessor added to existing TracerProvider")
    else:
        # 如果没有 TracerProvider，创建一个
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(MingjingSpanProcessor(hot_dir=hot_dir))
        trace.set_tracer_provider(tracer_provider)
        logger.info(f"Mingjing TracerProvider created with SpanProcessor")


# 导出
__all__ = [
    "MingjingSpanProcessor",
    "init_mingjing_tracer",
    "check_agentscope_version",
]
