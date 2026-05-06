# mingjing-probe — Hermes Agent plugin
# Entry point: register(ctx)
# Installs lifecycle hooks that mirror LLM/tool/session events to ~/.ming/hot/*.jsonl
# Includes synthetic agent_step generation and OS resource sampling

import time
import os
import threading
from pathlib import Path

from . import _extractors as ext
from .probe_uni import ProbeUni
from ._payload_builders import (
    build_tool_event,
    build_step_start_event,
    build_step_finish_event,
    build_step_event,
    build_llm_output_event,
)

_MING_HERMES_MIN_VERSION = "0.1.0"

_probe: ProbeUni | None = None
_api_starts: dict[str, float] = {}
_tool_starts: dict[str, float] = {}
_step_counter: int = 0
_step_start_time: float | None = None
_step_task_id: str | None = None
_polling_started: bool = False


def _check_hermes_version():
    try:
        import hermes

        ver = getattr(hermes, "__version__", "unknown")
        print(
            f"[mingjing-probe] Hermes Agent {ver} detected. "
            f"Hook interface is stable across upgrades — no action needed.",
            file=sys.stderr,
        )
    except ImportError:
        pass
    except Exception:
        pass


def register(ctx) -> None:
    global _probe, _polling_started
    _check_hermes_version()
    system_name = os.environ.get("MING_SYSTEM_NAME", "hermes-agent")
    mode = os.environ.get("MING_MODE", "white")
    _probe = ProbeUni(system=system_name, mode=mode)

    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_hook("pre_api_request", _on_pre_api_request)
    ctx.register_hook("post_api_request", _on_post_api_request)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("subagent_stop", _on_subagent_stop)

    skill_path = Path(__file__).parent / "skills" / "mingjing" / "SKILL.md"
    if skill_path.exists():
        ctx.register_skill(
            "mingjing", skill_path, "乾坤镜诊断系统：按需诊病、分诊、健康报告"
        )

    if not _polling_started:
        _start_os_polling()
        _start_healthbeat()
        _polling_started = True


def _safe_probe():
    """Return the probe instance, creating a fallback if not initialized."""
    global _probe
    if _probe is None:
        system_name = os.environ.get("MING_SYSTEM_NAME", "hermes-agent")
        _probe = ProbeUni(system=system_name, mode="white")
    return _probe


# ── Session hooks ──


def _on_session_start(**kwargs):
    try:
        p = _safe_probe()
        info = ext.extract_session(kwargs)
        info["platform"] = kwargs.get("platform")
        info["model"] = kwargs.get("model")
        p.emit("session_start", {"session": info})
        p.emit("__register__", {"layer_agent": info})
        p.emit("__health__", {"layer_agent": info, "integrity_score": 1.0})
    except Exception as e:
        _safe_emit_error("on_session_start", kwargs, e)


def _on_session_end(**kwargs):
    try:
        p = _safe_probe()
        info = ext.extract_session(kwargs)
        info["completed"] = kwargs.get("completed")
        info["interrupted"] = kwargs.get("interrupted")
        p.emit("session_end", {"session": info})
    except Exception as e:
        _safe_emit_error("on_session_end", kwargs, e)


# ── LLM / API hooks ──


def _on_pre_api_request(**kwargs):
    try:
        sid = kwargs.get("session_id", "unknown")
        task_id = kwargs.get("task_id", sid)
        _api_starts[task_id] = time.perf_counter()

        p = _safe_probe()
        session = ext.extract_session(kwargs)
        model_info = ext.extract_model_info(kwargs)
        req_info = ext.extract_api_request(kwargs)
        req_info["user_message_preview"] = _truncate(
            ext.extract_user_message(kwargs), 200
        )

        payload = {
            "layer_agent": session,
            "layer_llm": model_info,
            "layer_network": {
                "target_host": model_info.get("base_url"),
                "status_code": None,
            },
            "request_meta": req_info,
        }
        p.emit("llm_invoke_start", payload)
    except Exception as e:
        _safe_emit_error("pre_api_request", kwargs, e)


def _on_post_api_request(**kwargs):
    try:
        global _step_start_time, _step_task_id
        task_id = kwargs.get("task_id")
        sess_id = kwargs.get("session_id")
        start = _api_starts.pop(task_id or sess_id or "unknown", None)

        local_ms = (
            _to_ms(time.perf_counter() - start, hint_unit="seconds") if start else None
        )

        p = _safe_probe()
        usage = kwargs.get("usage") or {}
        network_meta = _deep_extract_network(kwargs)
        status_code = network_meta.get("status_code")

        payload = {
            "layer_agent": ext.extract_session(kwargs),
            "layer_llm": {
                "model": kwargs.get("model"),
                "provider": kwargs.get("provider"),
                "finish_reason": kwargs.get("finish_reason"),
                "input_tokens": _safe_int(usage, "prompt_tokens"),
                "output_tokens": _safe_int(usage, "completion_tokens"),
                "latency_ms": local_ms
                or _to_ms(kwargs.get("api_duration"), hint_unit="seconds"),
                "assistant_content_chars": kwargs.get("assistant_content_chars"),
            },
            "layer_network": {
                "target_host": kwargs.get("base_url"),
                "status_code": status_code,
                "retry_count": network_meta.get("retry_count"),
                "error_code": network_meta.get("error_code"),
            },
            "api_call_count": kwargs.get("api_call_count"),
        }
        p.emit("api_response", payload)
    except Exception as e:
        _safe_emit_error("post_api_request", kwargs, e)


def _on_pre_llm_call(**kwargs):
    try:
        global _step_counter, _step_start_time, _step_task_id
        p = _safe_probe()
        session = ext.extract_session(kwargs)
        model_info = ext.extract_model_info(kwargs)
        payload = {
            "layer_agent": session,
            "layer_llm": {
                "model": model_info.get("model"),
                "provider": model_info.get("provider"),
                "platform": model_info.get("platform"),
            },
            "user_message_preview": _truncate(ext.extract_user_message(kwargs), 200),
        }
        p.emit("llm_call_start", payload)

        _step_counter += 1
        _step_start_time = time.perf_counter()
        _step_task_id = kwargs.get("task_id") or kwargs.get("session_id")
        step_id = kwargs.get("task_id") or f"step-{_step_counter}"

        step_start = build_step_start_event(
            step_id=step_id,
            session_id=kwargs.get("session_id"),
            agent_name="hermes-agent",
            target_host=model_info.get("base_url"),
        )
        p.emit("agent_step_start", step_start)
    except Exception as e:
        _safe_emit_error("pre_llm_call", kwargs, e)


def _on_post_llm_call(**kwargs):
    try:
        global _step_start_time, _step_task_id
        session = ext.extract_session(kwargs)
        model_info = ext.extract_model_info(kwargs)
        usage = kwargs.get("usage") or {}

        assistant_response = (
            kwargs.get("assistant_response") or kwargs.get("assistant_message") or ""
        )
        if assistant_response:
            assistant_response = str(assistant_response)

        network_meta = _deep_extract_network(kwargs)
        status_code = network_meta.get("status_code")

        payload = {
            "layer_agent": {
                "step_id": kwargs.get("task_id"),
                "session_id": kwargs.get("session_id"),
                "agent_name": "hermes-agent",
            },
            "layer_llm": {
                "model": model_info.get("model"),
                "input_tokens": _safe_int(usage, "prompt_tokens"),
                "output_tokens": _safe_int(usage, "completion_tokens"),
                "latency_ms": _to_ms(kwargs.get("api_duration"), hint_unit="seconds"),
                "finish_reason": kwargs.get("finish_reason"),
                "temperature": kwargs.get("temperature"),
                "assistant_content_chars": kwargs.get("assistant_content_chars"),
                "assistant_tool_call_count": kwargs.get("assistant_tool_call_count"),
            },
            "layer_network": {
                "target_host": model_info.get("base_url"),
                "status_code": status_code,
                "retry_count": network_meta.get("retry_count"),
                "error_code": network_meta.get("error_code"),
            },
            "output_preview": _truncate(assistant_response, 200),
        }
        _safe_probe().emit("llm_invoke", payload)

        task_id = kwargs.get("task_id")
        session_id = kwargs.get("session_id")
        step_id = task_id or _step_task_id or f"step-{_step_counter}"
        step_elapsed_ms = None
        if _step_start_time and task_id and _step_task_id and task_id == _step_task_id:
            step_elapsed_ms = (time.perf_counter() - _step_start_time) * 1000

        p = _safe_probe()
        step_event = build_step_event(
            step_id=step_id,
            session_id=session_id,
            agent_name="hermes-agent",
            step_duration_ms=step_elapsed_ms,
            step_status="complete",
        )
        p.emit("agent_step", step_event)

        step_finish = build_step_finish_event(
            step_id=step_id,
            session_id=session_id,
            agent_name="hermes-agent",
            target_host=model_info.get("base_url"),
        )
        p.emit("agent_step_finish", step_finish)

        if assistant_response and len(assistant_response) > 10:
            llm_out = build_llm_output_event(
                step_id=step_id,
                session_id=session_id,
                agent_name="hermes-agent",
                output_text=assistant_response,
                target_host=model_info.get("base_url"),
            )
            p.emit("llm_output", llm_out)
    except Exception as e:
        _safe_emit_error("post_llm_call", kwargs, e)


# ── Tool hooks ──


def _on_pre_tool_call(**kwargs):
    try:
        tcid = kwargs.get("tool_call_id", "unknown")
        _tool_starts[tcid] = time.perf_counter()

        p = _safe_probe()
        info = ext.extract_tool_call(kwargs)
        session = ext.extract_session(kwargs)
        payload = {
            "layer_agent": session,
            "layer_tool": {
                "tool_name": info.get("tool_name"),
                "tool_args_preview": _truncate(str(info.get("tool_args", "")), 500),
                "tool_call_id": tcid,
                "tool_status": "start",
            },
        }
        p.emit("tool_call_start", payload)
    except Exception as e:
        _safe_emit_error("pre_tool_call", kwargs, e)


def _on_post_tool_call(**kwargs):
    try:
        tcid = kwargs.get("tool_call_id", "unknown")
        start = _tool_starts.pop(tcid, None)
        duration_ms = (
            _to_ms(time.perf_counter() - start, hint_unit="seconds")
            if start
            else kwargs.get("duration_ms")
        )

        p = _safe_probe()
        info = ext.extract_tool_call(kwargs)
        result_info = ext.extract_tool_result(kwargs)
        session = ext.extract_session(kwargs)

        success = not _is_error_result(result_info.get("tool_result"))
        payload = build_tool_event(
            step_id=kwargs.get("task_id"),
            session_id=kwargs.get("session_id"),
            agent_name="hermes-agent",
            tool_name=info.get("tool_name"),
            execution_ms=_to_ms(
                result_info.get("duration_ms"), hint_unit="milliseconds"
            )
            or duration_ms,
            tool_args=info.get("tool_args"),
            tool_result=result_info.get("tool_result"),
            target_host=None,
            success=success,
        )
        payload["layer_tool"]["tool_call_id"] = tcid
        p.emit("tool_call", payload)
    except Exception as e:
        _safe_emit_error("post_tool_call", kwargs, e)


# ── Sub-agent hook ──


def _on_subagent_stop(**kwargs):
    try:
        p = _safe_probe()
        session = ext.extract_session(kwargs)
        payload = {
            "layer_agent": session,
            "subagent": {
                "status": "stopped",
            },
        }
        p.emit("subagent_stop", payload)
    except Exception as e:
        _safe_emit_error("subagent_stop", kwargs, e)


# ── Deep extraction & OS polling ──


def _deep_extract_network(kwargs: dict) -> dict:
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
    elif isinstance(err, str):
        meta["error_code"] = err[:64]
    retry = kwargs.get("retry_count")
    if retry is not None:
        try:
            meta["retry_count"] = int(retry)
        except (ValueError, TypeError):
            pass
    return meta


def _start_os_polling():
    try:
        os.listdir("/proc/self/fd")
    except (FileNotFoundError, PermissionError, OSError):
        return

    def _poll():
        interval = float(os.environ.get("MING_POLL_INTERVAL", "10"))
        while True:
            time.sleep(interval)
            try:
                p = _safe_probe()
                snapshot = _sample_platform()
                if snapshot:
                    p.emit("platform_snapshot", snapshot)
            except Exception:
                pass

    threading.Thread(target=_poll, daemon=True, name="ming-os-poll").start()


def _start_healthbeat():
    def _beat():
        while True:
            time.sleep(30)
            try:
                p = _safe_probe()
                p.emit(
                    "__health__",
                    {
                        "integrity_score": 1.0,
                        "disk_free_mb": -1,
                        "emit_count": 0,
                        "drop_count": 0,
                    },
                )
            except Exception:
                pass

    threading.Thread(target=_beat, daemon=True, name="ming-healthbeat").start()


def _sample_platform() -> dict | None:
    try:
        result = {"pid": os.getpid()}
        with open("/proc/self/status") as f:
            for line in f:
                line = line.strip()
                if line.startswith("VmRSS:"):
                    result["vm_rss_kb"] = int(line.split()[1])
                elif line.startswith("VmSize:"):
                    result["vm_size_kb"] = int(line.split()[1])
                elif line.startswith("Threads:"):
                    result["thread_count"] = int(line.split()[1])
                elif line.startswith("FDSize:"):
                    result["fd_count"] = int(line.split()[1])
                elif line.startswith("voluntary_ctxt_switches:"):
                    result["voluntary_ctxt_switches"] = int(line.split()[1])
                elif line.startswith("nonvoluntary_ctxt_switches:"):
                    result["nonvoluntary_ctxt_switches"] = int(line.split()[1])
        try:
            with open("/proc/self/comm", "r") as f:
                result["proc_name"] = f.read().strip()
        except (FileNotFoundError, PermissionError, OSError):
            result["proc_name"] = ""
        try:
            result["proc_exe"] = os.readlink("/proc/self/exe")
        except (FileNotFoundError, PermissionError, OSError):
            result["proc_exe"] = ""
        try:
            with open("/proc/loadavg", "r") as f:
                parts = f.read().strip().split()
                result["load_avg_1m"] = float(parts[0])
                result["load_avg_5m"] = float(parts[1])
                result["load_avg_15m"] = float(parts[2])
        except (FileNotFoundError, PermissionError, OSError, ValueError):
            pass
        result["cpu_count"] = os.cpu_count() or 0
        try:
            result["open_fds"] = len(os.listdir("/proc/self/fd"))
        except (FileNotFoundError, PermissionError, OSError):
            result["open_fds"] = -1
        try:
            st = os.statvfs("/")
            result["disk_free_mb"] = (st.f_frsize * st.f_bavail) // (1024 * 1024)
        except (FileNotFoundError, PermissionError, OSError):
            pass
        return result
    except (FileNotFoundError, PermissionError, OSError):
        return None


# ── Helpers ──


def _safe_emit_error(hook_name: str, kwargs: dict, exc: Exception):
    try:
        sid = kwargs.get("session_id", "unknown")
        _safe_probe().emit(
            "error",
            {
                "layer_agent": {"session_id": sid},
                "error_type": type(exc).__name__,
                "error_msg": f"hook {hook_name}: {exc}",
                "hook": hook_name,
            },
        )
    except Exception:
        pass


def _truncate(s, max_len: int) -> str | None:
    if s is None:
        return None
    s = str(s)
    return s if len(s) <= max_len else s[:max_len] + "\u2026"


def _to_ms(val, hint_unit: str = "seconds") -> float | None:
    if val is None:
        return None
    try:
        v = float(val)
        return v * 1000 if hint_unit == "seconds" else v
    except (ValueError, TypeError):
        return None


def _safe_int(obj, *keys, default=None):
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


def _is_error_result(result) -> bool:
    if result is None:
        return True
    if isinstance(result, dict):
        return result.get("status") in ("error", "fail") or "error" in result
    s = str(result).lower()
    return "traceback" in s or "exception" in s
