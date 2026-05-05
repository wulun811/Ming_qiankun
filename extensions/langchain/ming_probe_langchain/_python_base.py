# _python_base.py —— 0.11.9m 乾坤镜 Python 适配器基类（包内副本）

import time
import sys
from pathlib import Path

from ming_probe_langchain.probe_uni import ProbeUni


def instrumented(probe, event_type, build_payload, error_context=None):
    def decorator(orig_method):
        def wrapper(self, *args, **kwargs):
            start = time.perf_counter()
            try:
                result = orig_method(self, *args, **kwargs)
                latency = (time.perf_counter() - start) * 1000
                probe.emit(
                    event_type, build_payload(self, result, latency, args, kwargs)
                )
                return result
            except Exception as e:
                ctx = error_context or {
                    "layer_agent": {
                        "step_id": "unknown",
                        "session_id": "unknown",
                        "agent_name": "unknown",
                    }
                }
                probe.emit(
                    "error",
                    {**ctx, "error_type": type(e).__name__, "error_msg": str(e)},
                )
                raise

        return wrapper

    return decorator


def make_adapter_init(monkey_patch_fn, default_system):
    _probe = None

    def init(system=default_system, mode="white"):
        nonlocal _probe
        _probe = ProbeUni(system=system, mode=mode)
        monkey_patch_fn(_probe)
        return _probe

    return init
