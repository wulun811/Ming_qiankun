#!/usr/bin/env python3
"""CrewAI 探针守护进程"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ["OPENAI_API_KEY"] = "39a1e74c3075f527752c9dcf9492d7fc:ZDM5YTg0ZmU1MjlmYjQyZGExOTkyOGU0"
os.environ["OPENAI_API_BASE"] = "https://maas-coding-api.cn-huabei-1.xf-yun.com/v2"

def main():
    from adapters.probe_crewai import init_crewai_probe
    from probe_uni import ProbeUni
    from pathlib import Path
    ProbeUni.HOT_DIR = Path(os.path.expanduser("~/.ming/hot"))
    probe = ProbeUni(system="crewai")
    init_crewai_probe()

    from crewai import Agent, Task, Crew
    from crewai.llm import LLM
    llm = LLM(model="openai/astron-code-latest", base_url="https://maas-coding-api.cn-huabei-1.xf-yun.com/v2", api_key="39a1e74c3075f527752c9dcf9492d7fc:ZDM5YTg0ZmU1MjlmYjQyZGExOTkyOGU0")
    agent = Agent(role="helper", goal="respond briefly", backstory="a helpful assistant", llm=llm)
    task = Task(description="Say hi in one word", expected_output="a single word", agent=agent)
    crew = Crew(agents=[agent], tasks=[task])

    print("[crewai-daemon] Started", file=sys.stderr)
    while True:
        try:
            result = crew.kickoff()
            probe.touch()
            print(f"[crewai-daemon] Crew call OK: {str(result)[:50]}", file=sys.stderr)
        except Exception as e:
            print(f"[crewai-daemon] Error: {e}", file=sys.stderr)
        time.sleep(60)

if __name__ == "__main__":
    main()
