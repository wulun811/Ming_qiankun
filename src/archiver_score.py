# archiver_score.py —— 0.11.9m 事件完整性评分
# 职责：纯函数，计算事件 integrity_score（0.0 ~ 1.0）
# 安全：无 DB 依赖，无外部调用

_REQUIRED_LAYERS = {
    "llm_invoke": ["layer_agent", "layer_llm", "layer_network"],
    "tool_call": ["layer_agent", "layer_tool", "layer_network"],
    "memory_retrieve": ["layer_agent", "layer_memory"],
}


def score_event(event: dict) -> float:
    """根据事件字段完整性计算 integrity_score"""
    score = 1.0
    payload = event.get("payload", {})
    event_type = event.get("event_type", "")

    missing = [l for l in _REQUIRED_LAYERS.get(event_type, []) if l not in payload]
    score -= 0.25 * len(missing)

    if payload.get("_incomplete"):
        score -= 0.15
    if payload.get("_source") == "watchdog_synthetic":
        score -= 0.1
    if payload.get("_clock_skew"):
        score -= 0.2
    if payload.get("_integrity_hint") == "partial":
        score -= 0.1

    return max(0.0, score)
