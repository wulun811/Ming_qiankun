# probe_langchain.py —— v0.11.9m 乾坤镜 LangChain Python 适配层
# 兼容 langchain-core >= 1.0
# 策略：monkey-patch invoke 入口 + BaseCallbackHandler 补流式/批量
# 依赖：_python_base.py, _payload_builders.py, probe_langchain_callback.py（零第三方依赖）

from adapters._python_base import make_adapter_init
from adapters._payload_builders import (
    build_llm_event,
    build_llm_output_event,
    build_tool_event,
    build_memory_event,
    build_step_start_event,
    build_step_finish_event,
)
import os
import time
import hashlib


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


def _extract_model_name(self):
    return getattr(self, "model_name", None) or getattr(self, "model", None)


def _extract_session_id(*args, **kwargs):
    """从 RunnableConfig.configurable 提取 session_id 或 thread_id"""
    config = args[0] if args else kwargs.get("config")
    if config and isinstance(config, dict):
        configurable = config.get("configurable", {})
        if isinstance(configurable, dict):
            return configurable.get("session_id") or configurable.get("thread_id")
    run_id = kwargs.get("run_id")
    if run_id:
        return str(run_id)
    return None


def _extract_usage_from_response_metadata(message):
    """从 AIMessage.response_metadata 提取 token usage"""
    if message is None:
        return None, None
    metadata = getattr(message, "response_metadata", {})
    if isinstance(metadata, dict):
        usage = metadata.get("token_usage") or metadata.get("usage")
        if usage:
            return (
                usage.get("prompt_tokens") or usage.get("input_tokens"),
                usage.get("completion_tokens") or usage.get("output_tokens"),
            )
    return None, None


def _extract_cache_hit(message):
    """从 AIMessage.usage_metadata 提取 cache_hit"""
    if message is None:
        return False
    usage = getattr(message, "usage_metadata", None)
    if usage and isinstance(usage, dict):
        details = usage.get("input_token_details", {})
        if isinstance(details, dict):
            cache_read = details.get("cache_read", 0)
            return cache_read > 0
    return False


def _extract_finish_reason(message):
    """从 AIMessage.response_metadata 提取 finish_reason"""
    if message is None:
        return None
    metadata = getattr(message, "response_metadata", {})
    if isinstance(metadata, dict):
        return metadata.get("finish_reason")
    return None


def _emit_error(probe, event_type, step_id, error):
    """统一错误事件发射"""
    probe.emit(
        "error",
        {
            "layer_agent": {
                "step_id": step_id,
                "agent_name": "langchain",
            },
            "error_type": type(error).__name__,
            "error_msg": str(error)[:500],
        },
    )


def _emit_agent_step(probe, step_name, session_id, result):
    """发射统一的 agent_step 事件（兼容 diseases.yaml 中 agent_step 规则）"""
    decision_summary = None
    if result is not None:
        content = getattr(result, "content", None)
        if content:
            decision_summary = truncate(str(content))
        else:
            decision_summary = truncate(str(result))

    probe.emit(
        "agent_step",
        {
            "layer_agent": {
                "step_id": step_name,
                "session_id": session_id,
                "agent_name": "langchain",
                "decision_summary": decision_summary,
            },
            "layer_network": {"target_host": "local"},
        },
    )


def _extract_retriever_metadata(result):
    """从 Document 列表提取 chunk_ids、relevance_scores"""
    chunk_ids = []
    relevance_scores = []
    if not isinstance(result, (list, tuple)):
        return chunk_ids, relevance_scores
    for doc in result:
        doc_id = getattr(doc, "id", None)
        if doc_id:
            chunk_ids.append(doc_id)
        meta = getattr(doc, "metadata", {})
        if isinstance(meta, dict):
            score = (
                meta.get("score")
                or meta.get("_relevance_score")
                or meta.get("similarity")
            )
            if score is not None:
                try:
                    relevance_scores.append(float(score))
                except (ValueError, TypeError):
                    pass
    return chunk_ids, relevance_scores


def _extract_embedding_model(retriever):
    """从 retriever 提取 embedding model（启发式）"""
    try:
        ls_params = retriever._get_ls_params()
        if isinstance(ls_params, dict):
            model = ls_params.get("ls_embedding_model")
            if model:
                return str(model)
    except Exception:
        pass
    vs = getattr(retriever, "vectorstore", None) or getattr(
        retriever, "retriever", None
    )
    if vs:
        emb = getattr(vs, "embeddings", None) or getattr(vs, "embedding", None)
        if emb:
            return getattr(emb, "model", None) or getattr(emb, "model_name", None)
    return None


def _monkey_patch(probe):
    # ===== Chat Models: BaseChatModel.invoke =====
    try:
        from langchain_core.language_models.chat_models import BaseChatModel

        original_invoke = BaseChatModel.invoke

        def patched_chat_invoke(self, input, *args, **kwargs):
            start = time.perf_counter()
            try:
                result = original_invoke(self, input, *args, **kwargs)
                latency_ms = (time.perf_counter() - start) * 1000

                session_id = _extract_session_id(*args, **kwargs)
                model_name = _extract_model_name(self)
                input_tokens, output_tokens = _extract_usage_from_response_metadata(
                    result
                )
                finish_reason = _extract_finish_reason(result)
                cache_hit = _extract_cache_hit(result)
                output_text = getattr(result, "content", None) if result else None

                probe.emit(
                    "llm_invoke",
                    build_llm_event(
                        step_id="invoke",
                        session_id=session_id,
                        agent_name="langchain",
                        model=model_name,
                        latency_ms=latency_ms,
                        response=result,
                        kwargs={
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "finish_reason": finish_reason,
                            "cache_hit": cache_hit,
                        },
                    ),
                )

                if output_text:
                    probe.emit(
                        "llm_output",
                        build_llm_output_event(
                            step_id="invoke",
                            session_id=session_id,
                            agent_name="langchain",
                            output_text=truncate(output_text),
                            target_host="local",
                        ),
                    )
                return result
            except Exception as e:
                _emit_error(probe, "llm_invoke", "invoke", e)
                raise

        BaseChatModel.invoke = patched_chat_invoke
    except ImportError:
        pass

    # ===== Completion Models: BaseLLM.invoke =====
    try:
        from langchain_core.language_models.llms import BaseLLM

        original_llm_invoke = BaseLLM.invoke

        def patched_llm_invoke(self, input, *args, **kwargs):
            start = time.perf_counter()
            try:
                result = original_llm_invoke(self, input, *args, **kwargs)
                latency_ms = (time.perf_counter() - start) * 1000

                session_id = _extract_session_id(*args, **kwargs)
                model_name = _extract_model_name(self)
                output_text = str(result) if result else None

                probe.emit(
                    "llm_invoke",
                    build_llm_event(
                        step_id="invoke",
                        session_id=session_id,
                        agent_name="langchain",
                        model=model_name,
                        latency_ms=latency_ms,
                        response=result,
                        kwargs={},
                    ),
                )

                if output_text:
                    probe.emit(
                        "llm_output",
                        build_llm_output_event(
                            step_id="invoke",
                            session_id=session_id,
                            agent_name="langchain",
                            output_text=truncate(output_text),
                            target_host="local",
                        ),
                    )
                return result
            except Exception as e:
                _emit_error(probe, "llm_invoke", "invoke", e)
                raise

        BaseLLM.invoke = patched_llm_invoke
    except ImportError:
        pass

    # ===== Tools: BaseTool.invoke =====
    try:
        from langchain_core.tools import BaseTool

        original_tool_invoke = BaseTool.invoke

        def patched_tool_invoke(self, input, *args, **kwargs):
            start = time.perf_counter()
            try:
                result = original_tool_invoke(self, input, *args, **kwargs)
                latency_ms = (time.perf_counter() - start) * 1000

                session_id = _extract_session_id(*args, **kwargs)

                probe.emit(
                    "tool_call",
                    build_tool_event(
                        session_id=session_id,
                        agent_name="langchain",
                        tool_name=getattr(self, "name", None),
                        execution_ms=latency_ms,
                        tool_args=input,
                        tool_result=result,
                        target_host="local",
                        success=result is not None,
                    ),
                )
                return result
            except Exception as e:
                _emit_error(probe, "tool_call", getattr(self, "name", "tool"), e)
                raise

        BaseTool.invoke = patched_tool_invoke
    except ImportError:
        pass

    # ===== Retrievers: BaseRetriever.invoke =====
    try:
        from langchain_core.retrievers import BaseRetriever

        original_retriever_invoke = BaseRetriever.invoke

        def patched_retriever_invoke(self, input, *args, **kwargs):
            start = time.perf_counter()
            try:
                result = original_retriever_invoke(self, input, *args, **kwargs)
                latency_ms = (time.perf_counter() - start) * 1000

                session_id = _extract_session_id(*args, **kwargs)
                chunk_ids, relevance_scores = _extract_retriever_metadata(result)
                embedding_model = _extract_embedding_model(self)

                probe.emit(
                    "memory_retrieve",
                    build_memory_event(
                        session_id=session_id,
                        agent_name="langchain",
                        query=input,
                        results=result if isinstance(result, (list, tuple)) else None,
                        latency_ms=latency_ms,
                        memory_type="retriever",
                        memory_store=getattr(self, "vectorstore", None)
                        and getattr(getattr(self, "vectorstore", None), "_index", None)
                        and "vectorstore",
                        relevance_scores=relevance_scores or None,
                        chunk_ids=chunk_ids or None,
                        embedding_model=embedding_model,
                    ),
                )
                return result
            except Exception as e:
                _emit_error(probe, "memory_retrieve", "retriever", e)
                raise

        BaseRetriever.invoke = patched_retriever_invoke
    except ImportError:
        pass

    # ===== Runnable/LCEL: Runnable.invoke + RunnableSequence.invoke =====
    try:
        from langchain_core.runnables import Runnable, RunnableSequence

        def _wrap_runnable_invoke(original_invoke):
            def wrapper(self, input, *args, **kwargs):
                step_name = getattr(self, "name", None)
                if step_name is None:
                    cls = getattr(self, "__class__", None)
                    step_name = cls.__name__ if cls else "runnable"

                session_id = _extract_session_id(*args, **kwargs)

                probe.emit(
                    "agent_step_start",
                    build_step_start_event(
                        step_id=step_name,
                        session_id=session_id,
                        agent_name="langchain",
                        target_host="local",
                    ),
                )
                try:
                    result = original_invoke(self, input, *args, **kwargs)

                    _emit_agent_step(probe, step_name, session_id, result)

                    probe.emit(
                        "agent_step_finish",
                        build_step_finish_event(
                            step_id=step_name,
                            session_id=session_id,
                            agent_name="langchain",
                            target_host="local",
                        ),
                    )
                    return result
                except Exception as e:
                    _emit_error(probe, "agent_step", step_name, e)
                    probe.emit(
                        "agent_step_finish",
                        build_step_finish_event(
                            step_id=step_name,
                            session_id=session_id,
                            agent_name="langchain",
                            target_host="local",
                        ),
                    )
                    raise

            return wrapper

        Runnable.invoke = _wrap_runnable_invoke(Runnable.invoke)
        RunnableSequence.invoke = _wrap_runnable_invoke(RunnableSequence.invoke)
    except ImportError:
        pass

    # ===== BaseCallbackHandler 补流式/批量 =====
    try:
        from langchain_core.runnables.config import ensure_config as _orig_ensure_config
        from adapters.probe_langchain_callback import MingCallbackHandler

        _cb_handler = MingCallbackHandler(probe)

        def _patched_ensure_config(config=None):
            result = _orig_ensure_config(config)
            existing = result.get("callbacks")
            if existing is None:
                result["callbacks"] = [_cb_handler]
            elif isinstance(existing, list):
                if _cb_handler not in existing:
                    result["callbacks"] = [_cb_handler] + existing
            return result

        import langchain_core.runnables.config as _lc_config

        _lc_config.ensure_config = _patched_ensure_config
    except ImportError:
        pass


init_langchain_probe = make_adapter_init(_monkey_patch, "langchain")
