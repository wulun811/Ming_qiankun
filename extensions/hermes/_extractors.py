# _extractors.py — Hermes hook kwargs → Mingjing event fields
# Purpose: safely extract relevant fields from Hermes hook callbacks
#          and map them to the Mingjing event payload schema.
# Each extractor returns a dict or partial dict ready for payload builders.
# All access uses .get() so new/missing Hermes fields never crash.


def extract_session(kwargs: dict) -> dict:
    """Extract session identifiers from any hook kwargs."""
    return {
        "session_id": kwargs.get("session_id"),
        "task_id": kwargs.get("task_id"),
    }


def extract_model_info(kwargs: dict) -> dict:
    """Extract model/provider/platform from LLM or API hooks."""
    return {
        "model": kwargs.get("model"),
        "provider": kwargs.get("provider"),
        "platform": kwargs.get("platform"),
        "base_url": kwargs.get("base_url"),
        "api_mode": kwargs.get("api_mode"),
    }


def extract_api_request(kwargs: dict) -> dict:
    """Extract request metadata from pre_api_request."""
    return {
        "api_call_count": kwargs.get("api_call_count"),
        "message_count": kwargs.get("message_count"),
        "tool_count": kwargs.get("tool_count"),
        "approx_input_tokens": kwargs.get("approx_input_tokens"),
        "request_char_count": kwargs.get("request_char_count"),
        "max_tokens": kwargs.get("max_tokens"),
    }


def extract_api_response(kwargs: dict) -> dict:
    """Extract response metadata from post_api_request / post_llm_call."""
    usage = kwargs.get("usage") or {}
    return {
        "api_duration": kwargs.get("api_duration"),
        "finish_reason": kwargs.get("finish_reason"),
        "assistant_content_chars": kwargs.get("assistant_content_chars"),
        "assistant_tool_call_count": kwargs.get("assistant_tool_call_count"),
        "input_tokens": _safe_int(usage, "prompt_tokens")
        or _safe_int(usage, "input_tokens"),
        "output_tokens": _safe_int(usage, "completion_tokens")
        or _safe_int(usage, "output_tokens"),
    }


def extract_tool_call(kwargs: dict) -> dict:
    """Extract tool execution details from pre/post_tool_call."""
    return {
        "tool_name": kwargs.get("tool_name"),
        "tool_args": kwargs.get("args"),
        "tool_call_id": kwargs.get("tool_call_id"),
    }


def extract_tool_result(kwargs: dict) -> dict:
    """Extract tool result from post_tool_call."""
    return {
        "tool_result": kwargs.get("result"),
        "duration_ms": kwargs.get("duration_ms"),
    }


def extract_user_message(kwargs: dict) -> str | None:
    """Extract the user-facing message from pre_llm_call / pre_api_request."""
    msg = kwargs.get("user_message")
    if msg:
        return str(msg)
    messages = kwargs.get("messages")
    if isinstance(messages, (list, tuple)) and messages:
        last = messages[-1]
        if isinstance(last, dict):
            return str(last.get("content", ""))
        return str(last)
    return None


def extract_network_meta(kwargs: dict) -> dict:
    """Extract network-level metadata from post_llm_call / post_api_request."""
    meta = {}
    for key in ("status_code", "http_status", "http_status_code"):
        v = kwargs.get(key)
        if v is not None:
            try:
                meta["status_code"] = int(v)
                break
            except (ValueError, TypeError):
                pass
    err = kwargs.get("error") or kwargs.get("api_error")
    if isinstance(err, dict):
        meta["error_code"] = err.get("code") or err.get("status_code")
        meta["error_type"] = err.get("type") or err.get("error_type")
    elif isinstance(err, str):
        meta["error_code"] = err[:64]
    for int_key in ("retry_count", "attempt", "api_call_count"):
        v = kwargs.get(int_key)
        if v is not None:
            try:
                meta[int_key] = int(v)
            except (ValueError, TypeError):
                pass
    return meta


def _safe_int(obj, *keys, default=None):
    """Safely traverse dict/list for an integer value."""
    current = obj
    for k in keys:
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(k)
        elif isinstance(current, (list, tuple)):
            try:
                current = current[k]
            except (IndexError, TypeError):
                return default
        else:
            return default
    if current is None:
        return default
    try:
        return int(current)
    except (ValueError, TypeError):
        return default
