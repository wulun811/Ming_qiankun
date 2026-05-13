#!/usr/bin/env python3
"""OpenHands 探针守护进程 - 定期调用 LLM 保持探针活跃"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ["OPENHANDS_SUPPRESS_BANNER"] = "1"

XUNFEI_API_KEY = "39a1e74c3075f527752c9dcf9492d7fc:ZDM5YTg0ZmU1MjlmYjQyZGExOTkyOGU0"
XUNFEI_BASE_URL = "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2"
XUNFEI_MODEL = "astron-code-latest"

def main():
    from probe_uni import ProbeUni
    from pathlib import Path
    ProbeUni.HOT_DIR = Path(os.path.expanduser("~/.ming/hot"))
    probe = ProbeUni(system="openhands")

    from openai import OpenAI
    client = OpenAI(api_key=XUNFEI_API_KEY, base_url=XUNFEI_BASE_URL)

    print("[openhands-daemon] Started", file=sys.stderr)
    step = 0
    while True:
        try:
            step += 1
            t0 = time.time()
            resp = client.chat.completions.create(model=XUNFEI_MODEL, messages=[{"role": "user", "content": "Say hi in one word"}])
            latency_ms = (time.time() - t0) * 1000
            content = resp.choices[0].message.content or ""
            usage = resp.usage

            step_id = f"oh_{step}"
            probe.emit("llm_invoke", {
                "layer_agent": {"step_id": step_id, "session_id": "daemon", "agent_name": "openhands"},
                "layer_llm": {"model": XUNFEI_MODEL, "input_tokens": usage.prompt_tokens if usage else 0, "output_tokens": usage.completion_tokens if usage else 0, "latency_ms": latency_ms, "finish_reason": "stop"},
                "layer_network": {"target_host": "maas-coding-api.cn-huabei-1.xf-yun.com", "status_code": 200},
            })
            if content:
                probe.emit("llm_output", {
                    "layer_agent": {"step_id": step_id, "session_id": "daemon", "agent_name": "openhands"},
                    "layer_llm": {"output_text": content[:2000], "output_text_hash": str(hash(content))[:16]},
                })
            probe.touch()
            print(f"[openhands-daemon] LLM call OK: {content[:50]}", file=sys.stderr)
        except Exception as e:
            print(f"[openhands-daemon] Error: {e}", file=sys.stderr)
        time.sleep(60)

if __name__ == "__main__":
    main()
