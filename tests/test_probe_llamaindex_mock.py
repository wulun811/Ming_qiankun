# test_probe_llamaindex_mock.py —— 乾坤镜 LlamaIndex 适配器 Mock 测试

import sys
import types
import unittest
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

_captured_events = []


def _setup_llamaindex_modules():
    class FakeLLM:
        def __init__(self):
            self.model = "gpt-4"

        def complete(self, *args, **kwargs):
            return type("R", (), {"text": "response"})()

    class FakeBaseRetriever:
        def retrieve(self, *args, **kwargs):
            return [{"node": {"text": "doc1"}}]

    class FakeIngestionPipeline:
        def run(self, *args, **kwargs):
            return [{"id": "1"}]

    fake_core = types.ModuleType("llama_index")
    fake_core_core = types.ModuleType("llama_index.core")
    fake_core_llms = types.ModuleType("llama_index.core.llms")
    fake_core_llms.LLM = FakeLLM
    fake_core_base = types.ModuleType("llama_index.core.base")
    fake_core_base.base_retriever = types.ModuleType(
        "llama_index.core.base.base_retriever"
    )
    fake_core_base.base_retriever.BaseRetriever = FakeBaseRetriever
    fake_core_ingestion = types.ModuleType("llama_index.core.ingestion")
    fake_core_ingestion.IngestionPipeline = FakeIngestionPipeline

    sys.modules["llama_index"] = fake_core
    sys.modules["llama_index.core"] = fake_core_core
    sys.modules["llama_index.core.llms"] = fake_core_llms
    sys.modules["llama_index.core.base"] = fake_core_base
    sys.modules["llama_index.core.base.base_retriever"] = fake_core_base.base_retriever
    sys.modules["llama_index.core.ingestion"] = fake_core_ingestion


class TestLlamaIndexAdapter(unittest.TestCase):
    def setUp(self):
        global _captured_events
        _captured_events.clear()
        _setup_llamaindex_modules()
        from probe_uni import ProbeUni

        self._orig_emit = ProbeUni.emit

        def capture_emit(self, event_type, payload):
            _captured_events.append({"event_type": event_type, "payload": payload})

        ProbeUni.emit = capture_emit

    def tearDown(self):
        from probe_uni import ProbeUni

        ProbeUni.emit = self._orig_emit
        for mod in list(sys.modules.keys()):
            if mod.startswith("llama_index"):
                del sys.modules[mod]

    def test_adapter_imports_without_error(self):
        from adapters.probe_llamaindex import init_llamaindex_probe

        self.assertTrue(callable(init_llamaindex_probe))

    def test_monkey_patch_applies_to_complete(self):
        from adapters.probe_llamaindex import init_llamaindex_probe

        probe = init_llamaindex_probe()
        from llama_index.core.llms import LLM

        self.assertNotEqual(LLM.complete.__name__, "complete")

    def test_monkey_patch_applies_to_retrieve(self):
        from adapters.probe_llamaindex import init_llamaindex_probe

        probe = init_llamaindex_probe()
        from llama_index.core.base.base_retriever import BaseRetriever

        self.assertNotEqual(BaseRetriever.retrieve.__name__, "retrieve")

    def test_emit_on_complete(self):
        from adapters.probe_llamaindex import init_llamaindex_probe

        probe = init_llamaindex_probe()
        from llama_index.core.llms import LLM

        llm = LLM()
        llm.complete("test prompt")
        events = [e for e in _captured_events if e["event_type"] == "llm_invoke"]
        self.assertTrue(len(events) > 0)
        self.assertEqual(
            events[0]["payload"]["layer_agent"]["agent_name"], "llamaindex"
        )

    def test_emit_on_retrieve(self):
        from adapters.probe_llamaindex import init_llamaindex_probe

        probe = init_llamaindex_probe()
        from llama_index.core.base.base_retriever import BaseRetriever

        retriever = BaseRetriever()
        retriever.retrieve("test query")
        events = [e for e in _captured_events if e["event_type"] == "memory_retrieve"]
        self.assertTrue(len(events) > 0)
        self.assertIn("layer_memory", events[0]["payload"])


if __name__ == "__main__":
    unittest.main()
