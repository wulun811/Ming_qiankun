# probe_lit.py —— v0.11.9m 乾坤镜 LIT Python SDK 适配层
# 职责：在 LIT Client/Server 事件流中注入乾坤镜探针调用
# 依赖：_python_base.py, _payload_builders.py（零第三方依赖）

from adapters._python_base import make_adapter_init, instrumented
from adapters._payload_builders import (
    build_llm_event,
    build_tool_event,
    build_memory_event,
    _extract_tokens,
    _extract_finish_reason,
)


def _monkey_patch(probe):
    try:
        from mcp.client.session import ClientSession

        ClientSession.call_tool = instrumented(
            probe,
            "tool_call",
            lambda s, r, lat, a, k: build_tool_event(
                tool_name=a[0] if a else None,
                execution_ms=lat,
                tool_args=k.get("arguments") or {},
                tool_result=r,
                target_host="mcp_server",
                success=r is not None and not isinstance(r, Exception),
            ),
            {"layer_agent": {"agent_name": "mcp_client"}},
        )(ClientSession.call_tool)

        ClientSession.read_resource = instrumented(
            probe,
            "memory_retrieve",
            lambda s, r, lat, a, k: build_memory_event(
                query=a[0] if a else None,
                results=r if isinstance(r, (list, tuple)) else ([r] if r else None),
                latency_ms=lat,
                memory_type="mcp_resource",
                memory_store=k.get("uri") or (str(a[0]) if a else None),
            ),
            {"layer_agent": {"agent_name": "mcp_client"}},
        )(ClientSession.read_resource)

        from mcp.server import Server

        _orig_handle = getattr(Server, "handle_request", None)
        if _orig_handle:
            Server.handle_request = instrumented(
                probe,
                "llm_invoke",
                lambda s, r, lat, a, k: build_llm_event(
                    agent_name="mcp_server",
                    latency_ms=lat,
                    response=r,
                    kwargs={
                        "input_tokens": _extract_tokens(r, k)[0],
                        "output_tokens": _extract_tokens(r, k)[1],
                        "finish_reason": _extract_finish_reason(r, k),
                    },
                    target_host="mcp_client",
                ),
                {"layer_agent": {"agent_name": "mcp_server"}},
            )(_orig_handle)

    except ImportError:
        pass


init_lit_probe = make_adapter_init(_monkey_patch, "lit")
