#!/usr/bin/env python3
"""LlamaIndex 探针守护进程 - 使用 openai client + 手动 emit"""
import sys, os, time, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

XUNFEI_API_KEY = "39a1e74c3075f527752c9dcf9492d7fc:ZDM5YTg0ZmU1MjlmYjQyZGExOTkyOGU0"
XUNFEI_BASE_URL = "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2"
XUNFEI_MODEL = "astron-code-latest"

def main():
    from adapters.probe_llamaindex import init_llamaindex_probe
    probe = init_llamaindex_probe()

    from openai import OpenAI
    client = OpenAI(api_key=XUNFEI_API_KEY, base_url=XUNFEI_BASE_URL)

    print("[llamaindex-daemon] Started", file=sys.stderr)
    while True:
        try:
            t0 = time.time()
            resp = client.chat.completions.create(model=XUNFEI_MODEL, messages=[{"role": "user", "content": "Say hi in one word"}])
            latency = (time.time() - t0) * 1000
            content = resp.choices[0].message.content
            usage = resp.usage

            # Emit via probe's internal method
            probe.emit('llm_invoke', {
                'layer_agent': {'step_id': f'llama_{int(time.time())}', 'session_id': 'daemon', 'agent_name': 'llamaindex'},
                'layer_llm': {'model': XUNFEI_MODEL, 'input_tokens': usage.prompt_tokens if usage else 0, 'output_tokens': usage.completion_tokens if usage else 0, 'latency_ms': latency, 'finish_reason': 'stop'},
                'layer_network': {'target_host': 'maas-coding-api.cn-huabei-1.xf-yun.com', 'status_code': 200},
            })
            probe.emit('llm_output', {
                'layer_agent': {'step_id': f'llama_{int(time.time())}', 'session_id': 'daemon', 'agent_name': 'llamaindex'},
                'layer_llm': {'output_text': content, 'output_text_hash': str(hash(content))[:16]},
            })
            probe.touch()
            print(f"[llamaindex-daemon] LLM call OK: {content[:50]}", file=sys.stderr)
        except Exception as e:
            print(f"[llamaindex-daemon] Error: {e}", file=sys.stderr)
        time.sleep(60)

if __name__ == "__main__":
    main()
