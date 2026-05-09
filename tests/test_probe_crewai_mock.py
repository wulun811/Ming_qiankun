# test_probe_crewai_mock.py —— 乾坤镜 CrewAI 适配器 Mock 测试

import sys
import types
import unittest
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

_captured_events = []


def _setup_crewai_modules():
    class FakeAgent:
        def __init__(self):
            self.role = "researcher"

        def execute_task(self, *args, **kwargs):
            return "task_result"

    class FakeCrew:
        def __init__(self):
            self.name = "test_crew"

        def kickoff(self, *args, **kwargs):
            return "crew_result"

    class FakeLLM:
        def __init__(self):
            self.model = "gpt-4"

        def call(self, *args, **kwargs):
            return {"text": "response"}

    fake_agent = types.ModuleType("crewai")
    fake_agent.agent = types.ModuleType("crewai.agent")
    fake_agent.agent.Agent = FakeAgent
    fake_crew = types.ModuleType("crewai.crew")
    fake_crew.crew = types.ModuleType("crewai.crew")
    fake_crew.crew.Crew = FakeCrew
    fake_llm = types.ModuleType("crewai.llm")
    fake_llm.LLM = FakeLLM

    sys.modules["crewai"] = types.ModuleType("crewai")
    sys.modules["crewai.agent"] = fake_agent.agent
    sys.modules["crewai.crew"] = fake_crew.crew
    sys.modules["crewai.llm"] = fake_llm


class TestCrewAIAdapter(unittest.TestCase):
    def setUp(self):
        global _captured_events
        _captured_events.clear()
        _setup_crewai_modules()
        from probe_uni import ProbeUni

        self._orig_emit = ProbeUni.emit

        def capture_emit(self, event_type, payload):
            _captured_events.append({"event_type": event_type, "payload": payload})

        ProbeUni.emit = capture_emit

    def tearDown(self):
        from probe_uni import ProbeUni

        ProbeUni.emit = self._orig_emit
        for mod in list(sys.modules.keys()):
            if mod.startswith("crewai"):
                del sys.modules[mod]

    def test_adapter_imports_without_error(self):
        from adapters.probe_crewai import init_crewai_probe

        self.assertTrue(callable(init_crewai_probe))

    def test_monkey_patch_applies_to_execute_task(self):
        from adapters.probe_crewai import init_crewai_probe

        probe = init_crewai_probe()
        from crewai.agent import Agent

        self.assertNotEqual(Agent.execute_task.__name__, "execute_task")

    def test_monkey_patch_applies_to_kickoff(self):
        from adapters.probe_crewai import init_crewai_probe

        probe = init_crewai_probe()
        from crewai.crew import Crew

        self.assertNotEqual(Crew.kickoff.__name__, "kickoff")

    def test_emit_on_execute_task(self):
        from adapters.probe_crewai import init_crewai_probe

        probe = init_crewai_probe()
        from crewai.agent import Agent

        agent = Agent()
        agent.execute_task("research")
        events = [e for e in _captured_events if e["event_type"] == "agent_step"]
        self.assertTrue(len(events) > 0)
        self.assertEqual(
            events[0]["payload"]["layer_agent"]["agent_name"], "researcher"
        )

    def test_emit_on_kickoff(self):
        from adapters.probe_crewai import init_crewai_probe

        probe = init_crewai_probe()
        from crewai.crew import Crew

        crew = Crew()
        crew.kickoff()
        kickoff_events = [
            e for e in _captured_events if "kickoff" in str(e.get("payload", {}))
        ]
        self.assertTrue(len(kickoff_events) > 0)


if __name__ == "__main__":
    unittest.main()
