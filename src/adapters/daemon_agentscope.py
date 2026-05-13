#!/usr/bin/env python3
"""AgentScope 探针守护进程 - 定期调用 LLM 保持探针活跃"""
import sys, os, time, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

XUNFEI_API_KEY = "39a1e74c3075f527752c9dcf9492d7fc:ZDM5YTg0ZmU1MjlmYjQyZGExOTkyOGU0"
XUNFEI_BASE_URL = "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2"
XUNFEI_MODEL = "astron-code-latest"

def main():
    from probe_uni import ProbeUni
    from pathlib import Path
    ProbeUni.HOT_DIR = Path(os.path.expanduser("~/.ming/hot"))
    probe = ProbeUni(system="agentscope")

    from agentscope.model import OpenAIChatModel
    model = OpenAIChatModel(
        model_name=XUNFEI_MODEL,
        api_key=XUNFEI_API_KEY,
        client_kwargs={"base_url": XUNFEI_BASE_URL},
        stream=False,
    )

    print("[agentscope-daemon] Started", file=sys.stderr)
    step = 0
    while True:
        try:
            step += 1
            t0 = time.time()
            async def call():
                return await model([{"role": "user", "content": "Say hi in one word"}])
            result = asyncio.run(call())
            latency_ms = (time.time() - t0) * 1000

            input_tokens = getattr(result.usage, "input_tokens", 0) or 0 if hasattr(result, "usage") and result.usage else 0
            output_tokens = getattr(result.usage, "output_tokens", 0) or 0 if hasattr(result, "usage") and result.usage else 0
            output_text = ""
            if hasattr(result, "content") and result.content:
                for c in result.content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        output_text = c.get("text", "")
                        break

            step_id = f"as_{step}"
            probe.emit("llm_invoke", {
                "layer_agent": {"step_id": step_id, "session_id": "daemon", "agent_name": "agentscope"},
                "layer_llm": {"model": XUNFEI_MODEL, "input_tokens": input_tokens, "output_tokens": output_tokens, "latency_ms": latency_ms, "finish_reason": "stop"},
                "layer_network": {"target_host": "maas-coding-api.cn-huabei-1.xf-yun.com", "status_code": 200},
            })
            if output_text:
                probe.emit("llm_output", {
                    "layer_agent": {"step_id": step_id, "session_id": "daemon", "agent_name": "agentscope"},
                    "layer_llm": {"output_text": output_text[:2000], "output_text_hash": str(hash(output_text))[:16]},
                })
            probe.touch()
            print(f"[agentscope-daemon] LLM call OK at {time.strftime('%H:%M:%S')}", file=sys.stderr)
        except Exception as e:
            print(f"[agentscope-daemon] Error: {e}", file=sys.stderr)
        time.sleep(60)

if __name__ == "__main__":
    main()
