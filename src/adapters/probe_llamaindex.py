# probe_llamaindex.py —— v0.11.9m 乾坤镜 LlamaIndex 适配层
# 职责：在 LlamaIndex 文档流水线和检索中注入乾坤镜探针调用
# 依赖：_python_base.py, _payload_builders.py（零第三方依赖）

from adapters._python_base import make_adapter_init, instrumented
from adapters._payload_builders import (
    build_llm_event,
    build_llm_output_event,
    build_tool_event,
    build_memory_event,
    build_step_event,
    build_step_start_event,
    build_step_finish_event,
    _extract_tokens,
    _extract_finish_reason,
)
import os


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


def _extract_li_tokens(response):
    """LlamaIndex CompletionResponse 有 raw.usage 或 additional_kwargs"""
    if response is None:
        return None, None
    # response.raw 是原始 OpenAI response
    raw = getattr(response, "raw", None)
    if raw:
        usage = getattr(raw, "usage", None)
        if usage:
            return (
                getattr(usage, "prompt_tokens", None),
                getattr(usage, "completion_tokens", None),
            )
    # response.additional_kwargs
    addl = getattr(response, "additional_kwargs", {})
    if isinstance(addl, dict):
        usage = addl.get("usage")
        if usage:
            return (usage.get("prompt_tokens"), usage.get("completion_tokens"))
    return None, None


def _extract_li_finish_reason(response):
    if response is None:
        return None
    raw = getattr(response, "raw", None)
    if raw:
        try:
            choices = getattr(raw, "choices", None)
            if choices and len(choices) > 0:
                return getattr(choices[0], "finish_reason", None)
        except (IndexError, AttributeError):
            pass
    return getattr(response, "stop_reason", None)


def _monkey_patch(probe):
    try:
        from llama_index.core.llms import LLM

        original_complete = LLM.complete

        def patched_complete(self, *args, **kwargs):
            # 发射 llm_invoke 事件（原有逻辑）
            import time

            start = time.time()
            result = original_complete(self, *args, **kwargs)
            latency_ms = (time.time() - start) * 1000

            probe.emit(
                "llm_invoke",
                build_llm_event(
                    step_id="complete",
                    agent_name="llamaindex",
                    model=getattr(self, "model", None)
                    or getattr(self, "model_name", None),
                    latency_ms=latency_ms,
                    response=result,
                    kwargs={
                        "input_tokens": _extract_li_tokens(result)[0],
                        "output_tokens": _extract_li_tokens(result)[1],
                        "finish_reason": _extract_li_finish_reason(result),
                    },
                ),
            )

            # 发射 llm_output 事件
            output_text = None
            if result:
                output_text = getattr(result, "text", None) or getattr(
                    result, "content", None
                )

            if output_text:
                probe.emit(
                    "llm_output",
                    build_llm_output_event(
                        step_id="complete",
                        agent_name="llamaindex",
                        output_text=truncate(output_text),
                        target_host="local",
                    ),
                )
            return result

        LLM.complete = patched_complete

        from llama_index.core.base.base_retriever import BaseRetriever

        BaseRetriever.retrieve = instrumented(
            probe,
            "memory_retrieve",
            lambda s, r, lat, a, k: build_memory_event(
                query=a[0] if a else None,
                results=r if isinstance(r, (list, tuple)) else None,
                latency_ms=lat,
                memory_type="vector_index",
                memory_store=getattr(s, "storage_context", None) and "vector_index",
            ),
            {"layer_agent": {"agent_name": "llamaindex"}},
        )(BaseRetriever.retrieve)

        from llama_index.core.ingestion import IngestionPipeline

        original_run = IngestionPipeline.run

        def patched_ingestion_run(self, *args, **kwargs):
            probe.emit(
                "agent_step_start",
                build_step_start_event(
                    step_id="ingestion",
                    agent_name="llamaindex",
                    target_host="local",
                ),
            )
            try:
                result = original_run(self, *args, **kwargs)
                probe.emit(
                    "agent_step_finish",
                    build_step_finish_event(
                        step_id="ingestion",
                        agent_name="llamaindex",
                        target_host="local",
                    ),
                )
                return result
            except Exception:
                probe.emit(
                    "agent_step_finish",
                    build_step_finish_event(
                        step_id="ingestion",
                        agent_name="llamaindex",
                        target_host="local",
                    ),
                )
                raise

        IngestionPipeline.run = patched_ingestion_run

    except ImportError:
        pass


init_llamaindex_probe = make_adapter_init(_monkey_patch, "llamaindex")
