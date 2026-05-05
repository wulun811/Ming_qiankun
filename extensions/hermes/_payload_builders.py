# _payload_builders.py —— v0.11.9m 乾坤镜适配器事件构建器
# 职责：提供标准化的事件 payload 构建函数
# 原则：无法提取的字段显式 None（非硬编码 0），让分诊器明确知道"该字段不可用"
# 归属：官方参考实现内部工具，非协议标准

import hashlib
import json


def _safe_get(obj, *attrs, default=None):
    """安全链式获取属性，任一环节失败返回 default"""
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
            current = getattr(current, attr, default)
    return current if current is not None else default


def _extract_tokens(response, kwargs):
    """从 response 或 kwargs 提取 token 计数，无法提取返回 None"""
    input_tokens = (
        _safe_get(response, "usage", "prompt_tokens")
        or _safe_get(response, "prompt_tokens")
        or _safe_get(response, "usage", "input_tokens")
        or kwargs.get("input_tokens")
    )
    output_tokens = (
        _safe_get(response, "usage", "completion_tokens")
        or _safe_get(response, "completion_tokens")
        or _safe_get(response, "usage", "output_tokens")
        or kwargs.get("output_tokens")
    )
    return (
        int(input_tokens) if input_tokens is not None else None,
        int(output_tokens) if output_tokens is not None else None,
    )


def _extract_finish_reason(response, kwargs):
    """提取 finish_reason，无法提取返回 None"""
    reason = (
        _safe_get(response, "choices", 0, "finish_reason")
        or _safe_get(response, "finish_reason")
        or kwargs.get("finish_reason")
    )
    return reason if reason else None


def _extract_status_code(response, kwargs):
    """提取 HTTP status_code，无法提取返回 None"""
    code = (
        _safe_get(response, "status_code")
        or _safe_get(response, "response", "status_code")
        or kwargs.get("status_code")
    )
    return int(code) if code is not None else None


def build_llm_event(
    step_id=None,
    session_id=None,
    agent_name=None,
    model=None,
    latency_ms=None,
    response=None,
    kwargs=None,
    target_host=None,
):
    kwargs = kwargs or {}
    input_tokens, output_tokens = _extract_tokens(response, kwargs)
    finish_reason = _extract_finish_reason(response, kwargs)
    status_code = _extract_status_code(response, kwargs)

    return {
        "layer_agent": {
            "step_id": step_id,
            "session_id": session_id,
            "agent_name": agent_name,
        },
        "layer_llm": {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "latency_ms": latency_ms,
            "finish_reason": finish_reason,
            "temperature": kwargs.get("temperature"),
            "cache_hit": _safe_get(response, "cache_hit", default=None),
        },
        "layer_network": {
            "target_host": target_host,
            "status_code": status_code,
            "tcp_connected_ms": kwargs.get("tcp_connected_ms"),
            "tls_handshake_ms": kwargs.get("tls_handshake_ms"),
        },
    }


def build_tool_event(
    step_id=None,
    session_id=None,
    agent_name=None,
    tool_name=None,
    execution_ms=None,
    tool_args=None,
    tool_result=None,
    target_host=None,
    success=True,
):
    return {
        "layer_agent": {
            "step_id": step_id,
            "session_id": session_id,
            "agent_name": agent_name,
        },
        "layer_tool": {
            "tool_name": tool_name,
            "tool_args": tool_args or {},
            "tool_result": str(tool_result)[:500] if tool_result else None,
            "execution_ms": execution_ms,
            "tool_status": "success" if success else "fail",
            "tool_input_hash": (
                hashlib.sha256(
                    json.dumps(tool_args, sort_keys=True, default=str).encode()
                ).hexdigest()[:16]
                if tool_args
                else None
            ),
        },
        "layer_network": {
            "target_host": target_host,
            "status_code": 200 if success else None,
        },
    }


def build_memory_event(
    step_id=None,
    session_id=None,
    agent_name=None,
    memory_type=None,
    latency_ms=None,
    query=None,
    results=None,
    memory_store=None,
    relevance_scores=None,
    chunk_ids=None,
):
    results_count = len(results) if isinstance(results, (list, tuple)) else None
    avg_relevance = (
        sum(relevance_scores) / len(relevance_scores)
        if relevance_scores and len(relevance_scores) > 0
        else None
    )
    return {
        "layer_agent": {
            "step_id": step_id,
            "session_id": session_id,
            "agent_name": agent_name,
        },
        "layer_memory": {
            "memory_type": memory_type,
            "query": str(query)[:200] if query else None,
            "results_count": results_count,
            "latency_ms": latency_ms,
            "memory_store": memory_store,
            "relevance_score": avg_relevance,
            "chunk_id": chunk_ids[0] if chunk_ids and len(chunk_ids) == 1 else None,
        },
    }


def build_step_event(step_id=None, session_id=None, agent_name=None, **extras):
    payload = {
        "layer_agent": {
            "step_id": step_id,
            "session_id": session_id,
            "agent_name": agent_name,
        }
    }
    payload["layer_agent"].update({k: v for k, v in extras.items() if v is not None})
    return payload


def build_error_event(
    step_id=None, session_id=None, agent_name=None, error_type=None, error_msg=None
):
    return {
        "layer_agent": {
            "step_id": step_id,
            "session_id": session_id,
            "agent_name": agent_name,
        },
        "error_type": error_type,
        "error_msg": error_msg,
    }


def build_llm_output_event(
    step_id=None,
    session_id=None,
    agent_name=None,
    output_text=None,
    target_host=None,
):
    """构建 LLM 输出文本事件（含文本哈希）"""
    text_hash = None
    if output_text:
        text_hash = hashlib.sha256(str(output_text).encode()).hexdigest()[:16]

    return {
        "layer_agent": {
            "step_id": step_id,
            "session_id": session_id,
            "agent_name": agent_name,
        },
        "layer_llm": {
            "output_text": str(output_text)[:2000] if output_text else None,
            "output_text_hash": text_hash,
        },
        "layer_network": {
            "target_host": target_host,
        },
    }


def build_step_start_event(
    step_id=None,
    session_id=None,
    agent_name=None,
    target_host=None,
):
    """构建 Agent 步骤开始事件"""
    return {
        "layer_agent": {
            "step_id": step_id,
            "session_id": session_id,
            "agent_name": agent_name,
            "step_status": "start",
        },
        "layer_network": {
            "target_host": target_host,
        },
    }


def build_step_finish_event(
    step_id=None,
    session_id=None,
    agent_name=None,
    target_host=None,
):
    """构建 Agent 步骤结束事件"""
    return {
        "layer_agent": {
            "step_id": step_id,
            "session_id": session_id,
            "agent_name": agent_name,
            "step_status": "finish",
        },
        "layer_network": {
            "target_host": target_host,
        },
    }
