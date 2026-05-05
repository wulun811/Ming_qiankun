# _payload_builders.py —— v0.11.9m 乾坤镜适配器事件构建器（包内副本）

import json
import hashlib


def _safe_get(obj, *attrs, default=None):
    current = obj
    for attr in attrs:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(attr, default)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[attr]
            except (IndexError, TypeError):
                return default
        else:
            try:
                current = getattr(current, attr, default)
            except Exception:
                return default
    return current


def _extract_model(self):
    return (
        getattr(self, "model", None) or getattr(self, "model_name", None) or "unknown"
    )


def _extract_provider(self):
    return getattr(self, "provider", None) or type(self).__module__.split(".")[0]


def _extract_temperature(self):
    return getattr(self, "temperature", None)


def _safe_str(o, max_len=2000):
    try:
        s = str(o)
    except Exception:
        s = repr(o)
    if len(s) > max_len:
        return s[:max_len] + f"...[truncated {len(s) - max_len} chars]"
    return s


def _default_agent(session_id=None, step_id=None, agent_name=None):
    return {
        "step_id": _safe_str(step_id or "unknown"),
        "session_id": _safe_str(session_id or "unknown"),
        "agent_name": _safe_str(agent_name or "unknown"),
    }


def _default_network():
    return {"provider": "unknown", "model": "unknown"}


def build_llm_event(self, result, latency_ms, args, kwargs):
    model = _extract_model(self)
    provider = _extract_provider(self)
    temperature = _extract_temperature(self)
    session_id = _get_session_id(args, kwargs)
    step_id = hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
    messages_str = _extract_messages(args, kwargs)
    input_tokens = (
        _safe_get(result, "usage_metadata", "input_tokens") if result else None
    )
    output_tokens = (
        _safe_get(result, "usage_metadata", "output_tokens") if result else None
    )
    return {
        "layer_agent": _default_agent(session_id=session_id, step_id=step_id),
        "layer_llm": {
            "model": _safe_str(model),
            "temperature": temperature,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "layer_network": _default_network() | {"provider": provider},
        "content": messages_str,
    }


def build_llm_output_event(self, result, latency_ms, args, kwargs):
    session_id = _get_session_id(args, kwargs)
    model = _extract_model(self)
    provider = _extract_provider(self)
    output_text = _safe_str(result) if result else None
    input_tokens = (
        _safe_get(result, "usage_metadata", "input_tokens") if result else None
    )
    output_tokens = (
        _safe_get(result, "usage_metadata", "output_tokens") if result else None
    )
    return {
        "layer_agent": _default_agent(session_id=session_id, step_id="output"),
        "layer_llm": {
            "model": _safe_str(model),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "layer_network": _default_network() | {"provider": provider},
        "content": output_text,
    }


def build_tool_event(self, result, latency_ms, args, kwargs):
    session_id = _get_session_id(args, kwargs)
    tool_name = _extract_tool_name(self)
    return {
        "layer_agent": _default_agent(session_id=session_id, step_id=tool_name),
        "layer_tool": {
            "tool_name": _safe_str(tool_name),
            "duration_ms": round(latency_ms, 2),
            "args": _extract_tool_args(args, kwargs),
            "result": _safe_str(result)[:500] if result is not None else None,
        },
        "layer_network": _default_network(),
    }


def build_memory_event(self, result, latency_ms, args, kwargs):
    session_id = _get_session_id(args, kwargs)
    query = _extract_query(args, kwargs)
    return {
        "layer_agent": _default_agent(session_id=session_id, step_id="retrieve"),
        "layer_memory": {
            "query": _safe_str(query),
            "num_chunks": _count_chunks(result),
            "duration_ms": round(latency_ms, 2),
        },
        "layer_network": _default_network(),
    }


def build_step_start_event(self, result, latency_ms, args, kwargs):
    session_id = _get_session_id(args, kwargs)
    input_text = _safe_str(args[0] if args else kwargs.get("input", ""))
    return {
        "layer_agent": _default_agent(session_id=session_id, step_id="step_start"),
        "layer_llm": _default_network(),
        "layer_network": _default_network(),
        "content": input_text,
    }


def build_step_finish_event(self, result, latency_ms, args, kwargs):
    session_id = _get_session_id(args, kwargs)
    output_text = _safe_str(result)[:500] if result is not None else None
    return {
        "layer_agent": _default_agent(session_id=session_id, step_id="step_finish"),
        "layer_llm": _default_network(),
        "layer_network": _default_network(),
        "content": output_text,
    }


# ===== internal helpers =====

import time as _time


def _get_session_id(args, kwargs):
    config = args[0] if args else kwargs.get("config")
    if config and isinstance(config, dict):
        configurable = config.get("configurable", {})
        if isinstance(configurable, dict):
            return configurable.get("session_id") or configurable.get("thread_id")
    return None


def _extract_messages(args, kwargs):
    messages = kwargs.get("messages") or (args[0] if args else None)
    if messages:
        texts = []
        for m in messages if isinstance(messages, (list, tuple)) else [messages]:
            try:
                texts.append(
                    _safe_str(m.content)[:500]
                    if hasattr(m, "content")
                    else _safe_str(m)[:500]
                )
            except Exception:
                texts.append("<unparseable>")
        return " | ".join(texts)
    return None


def _extract_tool_name(self):
    return (
        getattr(self, "name", None) or getattr(self, "__class__", type(self)).__name__
    )


def _extract_tool_args(args, kwargs):
    if args and len(args) > 0:
        if isinstance(args[0], dict):
            return _safe_str(json.dumps(args[0], ensure_ascii=False))[:500]
    if kwargs:
        return _safe_str(json.dumps(kwargs, ensure_ascii=False))[:500]
    return _safe_str(str(args))[:500]


def _extract_query(args, kwargs):
    return args[0] if args else kwargs.get("query", "")


def _count_chunks(result):
    if result is None:
        return None
    if isinstance(result, (list, tuple)):
        return len(result)
    if hasattr(result, "documents"):
        try:
            return len(result.documents)
        except Exception:
            pass
    return 1


def build_error_event(error_type, error_msg, context=None):
    return {
        "layer_agent": _default_agent(
            session_id=_safe_str(context.get("session_id")) if context else "unknown"
        ),
        "error_type": error_type,
        "error_msg": _safe_str(error_msg),
    }
