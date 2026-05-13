# diagnosis_llm_hint.py — LLM 辅助诊断推测
# 异步获取 LLM 推测，失败/超时返回 None
# 铁律：不替代确定性规则、不阻塞 P0 推送、失败即退化
import json
import os
import threading
from typing import Optional

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

# 熔断器
_circuit_state = {
    "error_count": 0,
    "circuit_open": False,
    "total_calls": 0,
    "total_errors": 0,
}
CIRCUIT_THRESHOLD = 5
CIRCUIT_RESET_SEC = 60


def _build_prompt(rule_id: str, detail: str, events_summary: list[dict]) -> str:
    summary = "\n".join(
        f"{e.get('timestamp', '?')} {e.get('type', '?')} {e.get('detail', '')[:200]}"
        for e in (events_summary or [])[-20:]
    ) or "(无事件)"
    return (
        "你是诊断助手。根据以下诊断事件和当前故障，给出简短的可能原因推测（1-2句话）。\n\n"
        f"当前故障: 规则={rule_id}, 详情={detail}\n\n"
        f"最近事件:\n{summary}\n\n"
        "只输出推测，不要重复故障信息。"
    )


def fetch_llm_hint_sync(
    rule_id: str,
    detail: str,
    events_summary: list[dict],
    endpoint: str,
    api_key: str = "",
    timeout: float = 2.0,
    max_tokens: int = 100,
) -> Optional[str]:
    if not HAS_HTTPX or not endpoint:
        return None
    if _circuit_state["circuit_open"]:
        return None

    _circuit_state["total_calls"] += 1
    try:
        prompt = _build_prompt(rule_id, detail, events_summary)
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                endpoint,
                json={
                    "model": "deepseek-chat",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.3,
                },
                headers=headers,
            )
            if resp.status_code != 200:
                _record_error()
                return None
            data = resp.json()
            hint = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
            if hint:
                _circuit_state["error_count"] = 0
            return hint or None
    except Exception:
        _record_error()
        return None


def _record_error():
    _circuit_state["error_count"] += 1
    _circuit_state["total_errors"] += 1
    if _circuit_state["error_count"] >= CIRCUIT_THRESHOLD and not _circuit_state["circuit_open"]:
        _circuit_state["circuit_open"] = True
        import time
        threading.Timer(CIRCUIT_RESET_SEC, _reset_circuit).start()


def _reset_circuit():
    _circuit_state["circuit_open"] = False
    _circuit_state["error_count"] = 0


def get_hint_stats() -> dict:
    return dict(_circuit_state)
