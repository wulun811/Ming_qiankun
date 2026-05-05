# probe_langchain.py —— v0.11.9m 乾坤镜 LangChain Python 适配层（包内副本）
# 策略：monkey-patch invoke 入口 + BaseCallbackHandler 补流式/批量

from ming_probe_langchain._python_base import make_adapter_init
from ming_probe_langchain._payload_builders import (
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
    config = args[0] if args else kwargs.get("config")
    if config and isinstance(config, dict):
        configurable = config.get("configurable", {})
        if isinstance(configurable, dict):
            return configurable.get("session_id") or configurable.get("thread_id")
    return None


def _make_agent_layer(session_id=None, step_id=None):
    return {
        "step_id": step_id or hashlib.md5(str(time.time()).encode()).hexdigest()[:8],
        "session_id": session_id or "unknown",
        "agent_name": "langchain_probe",
    }


def _make_network_layer(model_name=None, provider=None):
    return {
        "provider": provider or "langchain",
        "model": model_name or "unknown",
    }


def _handle_llm_invoke(probe, instance, args, kwargs, result, start_ms):
    session_id = _extract_session_id(*args, **kwargs)
    model_name = _extract_model_name(instance)
    latency = (time.perf_counter() - start_ms) * 1000
    input_content = None
    messages = kwargs.get("messages") or (args[0] if args else None)
    if messages:
        if isinstance(messages, (list, tuple)):
            texts = []
            for m in messages:
                try:
                    texts.append(str(getattr(m, "content", str(m)))[:500])
                except Exception:
                    texts.append("<unparseable>")
            input_content = " | ".join(texts)
        else:
            try:
                input_content = str(getattr(messages, "content", str(messages)))[:2000]
            except Exception:
                input_content = "<unparseable>"
    input_tokens = None
    output_tokens = None
    try:
        if hasattr(result, "usage_metadata") and result.usage_metadata:
            input_tokens = getattr(result.usage_metadata, "input_tokens", None)
            output_tokens = getattr(result.usage_metadata, "output_tokens", None)
    except Exception:
        pass
    probe.emit(
        "llm_invoke",
        {
            "layer_agent": _make_agent_layer(
                session_id=session_id, step_id="llm_invoke"
            ),
            "layer_llm": {
                "model": model_name or "unknown",
                "temperature": getattr(instance, "temperature", None),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            "layer_network": _make_network_layer(model_name=model_name),
            "content": truncate(input_content) if input_content else None,
        },
    )
    probe.emit(
        "llm_output",
        {
            "layer_agent": _make_agent_layer(
                session_id=session_id, step_id="llm_output"
            ),
            "layer_llm": {
                "model": model_name or "unknown",
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
            "layer_network": _make_network_layer(model_name=model_name),
            "content": truncate(str(result))[:500] if result is not None else None,
        },
    )


def _handle_tool_invoke(probe, instance, args, kwargs, result, start_ms):
    session_id = _extract_session_id(*args, **kwargs)
    tool_name = getattr(instance, "name", None) or type(instance).__name__
    latency = (time.perf_counter() - start_ms) * 1000
    tool_args = None
    if args and len(args) > 0:
        if isinstance(args[0], dict):
            tool_args = truncate(str(args[0]))
        else:
            tool_args = truncate(str(args))
    elif kwargs:
        tool_args = truncate(str(kwargs))
    result_str = truncate(str(result))[:500] if result is not None else None
    probe.emit(
        "tool_call",
        {
            "layer_agent": _make_agent_layer(session_id=session_id, step_id=tool_name),
            "layer_tool": {
                "tool_name": tool_name,
                "duration_ms": round(latency, 2),
                "args": tool_args,
                "result": result_str,
            },
            "layer_network": _make_network_layer(),
        },
    )


def _handle_retriever_invoke(probe, instance, args, kwargs, result, start_ms):
    session_id = _extract_session_id(*args, **kwargs)
    latency = (time.perf_counter() - start_ms) * 1000
    query = str(args[0]) if args else str(kwargs.get("input", ""))
    num_chunks = None
    if isinstance(result, (list, tuple)):
        num_chunks = len(result)
    elif hasattr(result, "documents"):
        try:
            num_chunks = len(result.documents)
        except Exception:
            pass
    probe.emit(
        "memory_retrieve",
        {
            "layer_agent": _make_agent_layer(session_id=session_id, step_id="retrieve"),
            "layer_memory": {
                "query": truncate(query),
                "num_chunks": num_chunks,
                "duration_ms": round(latency, 2),
            },
            "layer_network": _make_network_layer(),
        },
    )


def _handle_runnable_sequence(probe, instance, args, kwargs, result, start_ms):
    session_id = _extract_session_id(*args, **kwargs)
    latency = (time.perf_counter() - start_ms) * 1000
    input_text = truncate(str(args[0] if args else kwargs.get("input", "")))
    output_text = truncate(str(result))[:500]
    probe.emit(
        "agent_step",
        {
            "layer_agent": _make_agent_layer(session_id=session_id, step_id="step"),
            "layer_llm": _make_network_layer(),
            "layer_network": _make_network_layer(),
            "content": input_text,
        },
    )


def _handle_error(probe, instance_or_type, error, session_id=None):
    probe.emit(
        "error",
        {
            "layer_agent": _make_agent_layer(
                session_id=session_id or "unknown", step_id="error"
            ),
            "error_type": type(error).__name__,
            "error_msg": truncate(str(error)),
        },
    )


def _patch_invoke(cls, handler):
    original = cls.invoke

    def patched_invoke(self, *args, **kwargs):
        start = time.perf_counter()
        try:
            result = original(self, *args, **kwargs)
            handler(self, args, kwargs, result, start)
            return result
        except Exception as e:
            session_id = _extract_session_id(*args, **kwargs)
            _handle_error(None, self, e, session_id)
            raise

    patched_invoke.__name__ = original.__name__
    patched_invoke.__qualname__ = original.__qualname__
    return patched_invoke


_MING_PROBE_INSTANCE = None


def _make_error_handler(probe):
    def on_error(self_or_type, error, session_id=None):
        _handle_error(probe, self_or_type, error, session_id)

    return on_error


def _monkey_patch(probe):
    global _MING_PROBE_INSTANCE
    _MING_PROBE_INSTANCE = probe
    on_error = _make_error_handler(probe)
    try:
        import langchain_core.language_models.chat_models as _chat_mod
        import langchain_core.language_models.llms as _llm_mod
        import langchain_core.tools as _tool_mod
        import langchain_core.retrievers as _ret_mod

        _chat_mod.BaseChatModel.invoke = _patch_invoke(
            _chat_mod.BaseChatModel,
            lambda self, a, kw, r, s: _handle_llm_invoke(probe, self, a, kw, r, s),
        )
        _llm_mod.BaseLLM.invoke = _patch_invoke(
            _llm_mod.BaseLLM,
            lambda self, a, kw, r, s: _handle_llm_invoke(probe, self, a, kw, r, s),
        )
        _tool_mod.BaseTool.invoke = _patch_invoke(
            _tool_mod.BaseTool,
            lambda self, a, kw, r, s: _handle_tool_invoke(probe, self, a, kw, r, s),
        )
        _ret_mod.BaseRetriever.invoke = _patch_invoke(
            _ret_mod.BaseRetriever,
            lambda self, a, kw, r, s: _handle_retriever_invoke(
                probe, self, a, kw, r, s
            ),
        )
    except ImportError:
        pass

    try:
        from langchain_core.runnables import RunnableSequence

        RunnableSequence.invoke = _patch_invoke(
            RunnableSequence,
            lambda self, a, kw, r, s: _handle_runnable_sequence(
                probe, self, a, kw, r, s
            ),
        )
    except ImportError:
        pass

    try:
        from langchain_core.runnables.config import ensure_config as _orig_ensure_config
        from ming_probe_langchain.probe_langchain_callback import MingCallbackHandler

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
