# probe_crewai.py —— v0.11.9m 乾坤镜 CrewAI 适配层
# 职责：在 CrewAI Agent 角色动作和 Crew 编排中注入乾坤镜探针调用
# 依赖：_python_base.py, _payload_builders.py（零第三方依赖）

from adapters._python_base import make_adapter_init, instrumented
from adapters._payload_builders import (
    build_llm_event,
    build_llm_output_event,
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


def _extract_crewai_tokens(response):
    """CrewAI LLM 返回可能是字符串或 ChatCompletion 对象"""
    if response is None:
        return None, None
    # 如果是 OpenAI ChatCompletion 对象
    usage = getattr(response, "usage", None)
    if usage:
        return (
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
        )
    # 如果是 dict
    if isinstance(response, dict):
        usage = response.get("usage")
        if usage:
            return (usage.get("prompt_tokens"), usage.get("completion_tokens"))
    return None, None


def _monkey_patch(probe):
    try:
        from crewai.agent import Agent

        Agent.execute_task = instrumented(
            probe,
            "agent_step",
            lambda s, r, lat, a, k: build_step_event(
                step_id="execute",
                agent_name=getattr(s, "role", None) or "crewai_agent",
                task=str(a[0])[:200] if a else None,
            ),
            {"layer_agent": {"agent_name": "crewai"}},
        )(Agent.execute_task)

        from crewai.crew import Crew

        Crew.kickoff = instrumented(
            probe,
            "agent_step",
            lambda s, r, lat, a, k: build_step_event(
                step_id="kickoff",
                agent_name="crew",
                crew_name=getattr(s, "name", None),
            ),
            {"layer_agent": {"agent_name": "crewai"}},
        )(Crew.kickoff)

        from crewai.llm import LLM

        original_call = LLM.call

        def patched_llm_call(self, *args, **kwargs):
            # 发射 llm_invoke 事件（原有逻辑）
            import time

            start = time.time()
            result = original_call(self, *args, **kwargs)
            latency_ms = (time.time() - start) * 1000

            probe.emit(
                "llm_invoke",
                build_llm_event(
                    step_id="llm",
                    agent_name="crewai",
                    model=getattr(self, "model", None),
                    latency_ms=latency_ms,
                    response=result,
                    kwargs={
                        "input_tokens": _extract_crewai_tokens(result)[0],
                        "output_tokens": _extract_crewai_tokens(result)[1],
                        "finish_reason": _extract_finish_reason(result, kwargs),
                    },
                ),
            )

            # 发射 llm_output 事件
            output_text = None
            if result:
                if isinstance(result, dict):
                    output_text = result.get("content") or result.get("text")
                elif hasattr(result, "content"):
                    output_text = result.content
                elif hasattr(result, "text"):
                    output_text = result.text

            if output_text:
                probe.emit(
                    "llm_output",
                    build_llm_output_event(
                        step_id="llm",
                        agent_name="crewai",
                        output_text=truncate(output_text),
                        target_host="local",
                    ),
                )
            return result

        LLM.call = patched_llm_call

        # 新增：Agent 级别的 step_start/step_finish 事件
        original_execute_task = Agent.execute_task

        def patched_execute_task(self, *args, **kwargs):
            probe.emit(
                "agent_step_start",
                build_step_start_event(
                    step_id=getattr(self, "role", "crewai_agent"),
                    agent_name="crewai",
                    target_host="local",
                ),
            )
            try:
                result = original_execute_task(self, *args, **kwargs)
                probe.emit(
                    "agent_step_finish",
                    build_step_finish_event(
                        step_id=getattr(self, "role", "crewai_agent"),
                        agent_name="crewai",
                        target_host="local",
                    ),
                )
                return result
            except Exception:
                probe.emit(
                    "agent_step_finish",
                    build_step_finish_event(
                        step_id=getattr(self, "role", "crewai_agent"),
                        agent_name="crewai",
                        target_host="local",
                    ),
                )
                raise

        Agent.execute_task = patched_execute_task

    except ImportError:
        pass


init_crewai_probe = make_adapter_init(_monkey_patch, "crewai")
