#!/usr/bin/env python3
"""Semantic Kernel 探针守护进程 - 定期调用 LLM 保持探针活跃"""
import sys, os, time, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

XUNFEI_API_KEY = "39a1e74c3075f527752c9dcf9492d7fc:ZDM5YTg0ZmU1MjlmYjQyZGExOTkyOGU0"
XUNFEI_BASE_URL = "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2"
XUNFEI_MODEL = "astron-code-latest"

def main():
    import semantic_kernel as sk
    from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion
    from openai import AsyncOpenAI
    from adapters.probe_semantic_kernel import init_mingjing_probe
    from probe_uni import ProbeUni
    from pathlib import Path
    ProbeUni.HOT_DIR = Path(os.path.expanduser("~/.ming/hot"))
    probe = ProbeUni(system="semantic_kernel")
    import adapters.probe_semantic_kernel as sk_adapter

    init_mingjing_probe()
    kernel = sk.Kernel()
    async_client = AsyncOpenAI(api_key=XUNFEI_API_KEY, base_url=XUNFEI_BASE_URL)
    kernel.add_service(OpenAIChatCompletion(service_id="xunfei", ai_model_id=XUNFEI_MODEL, async_client=async_client))
    tool_filter = sk_adapter._mingjing_probe
    if tool_filter:
        kernel.add_filter("function_invocation", tool_filter.tool_invocation_filter)

    print("[semantic-kernel-daemon] Started", file=sys.stderr)
    while True:
        try:
            async def call():
                return str(await kernel.invoke_prompt("Say hi in one word"))
            result = asyncio.run(call())
            probe.touch()
            print(f"[semantic-kernel-daemon] LLM call OK: {result[:50]}", file=sys.stderr)
        except Exception as e:
            print(f"[semantic-kernel-daemon] Error: {e}", file=sys.stderr)
        time.sleep(60)

if __name__ == "__main__":
    main()
