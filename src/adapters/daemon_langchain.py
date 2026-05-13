#!/usr/bin/env python3
"""LangChain 探针守护进程 - 定期调用 LLM 保持探针活跃"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

XUNFEI_API_KEY = "39a1e74c3075f527752c9dcf9492d7fc:ZDM5YTg0ZmU1MjlmYjQyZGExOTkyOGU0"
XUNFEI_BASE_URL = "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2"
XUNFEI_MODEL = "astron-code-latest"

def main():
    from adapters.probe_langchain import init_langchain_probe
    from probe_uni import ProbeUni
    from pathlib import Path
    ProbeUni.HOT_DIR = Path(os.path.expanduser("~/.ming/hot"))
    probe = ProbeUni(system="langchain")
    init_langchain_probe()  # noqa: F841

    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage

    llm = ChatOpenAI(model=XUNFEI_MODEL, api_key=XUNFEI_API_KEY, base_url=XUNFEI_BASE_URL)

    print("[langchain-daemon] Started", file=sys.stderr)
    while True:
        try:
            resp = llm.invoke([HumanMessage(content="Say hi in one word")])
            probe.touch()
            print(f"[langchain-daemon] LLM call OK: {resp.content[:50]}", file=sys.stderr)
        except Exception as e:
            print(f"[langchain-daemon] Error: {e}", file=sys.stderr)
        time.sleep(60)

if __name__ == "__main__":
    main()
