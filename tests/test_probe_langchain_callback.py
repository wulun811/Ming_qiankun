# test_probe_langchain_callback.py —— 乾坤镜 LangChain Callback Handler 测试
# 需要 langchain-core >= 1.0 真实安装

import sys
import unittest
import uuid
from pathlib import Path

_src = Path(__file__).parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

try:
    import langchain_core
except ImportError:
    langchain_core = None

_captured_events = []


@unittest.skipIf(langchain_core is None, "需要 langchain-core")
class TestMingCallbackHandler(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from probe_uni import ProbeUni

        global _captured_events
        _captured_events.clear()
        cls._orig_emit = ProbeUni.emit

        def capture_emit(self, event_type, payload):
            _captured_events.append({"event_type": event_type, "payload": payload})

        ProbeUni.emit = capture_emit

    @classmethod
    def tearDownClass(cls):
        from probe_uni import ProbeUni

        ProbeUni.emit = cls._orig_emit

    def setUp(self):
        _captured_events.clear()

    def test_chat_model_start_emits_event(self):
        from adapters.probe_langchain_callback import MingCallbackHandler
        from probe_uni import ProbeUni

        probe = ProbeUni(system="langchain-test")
        handler = MingCallbackHandler(probe)
        run_id = uuid.uuid4()

        handler.on_chat_model_start(
            {"name": "ChatOpenAI", "id": ["langchain", "llms", "openai", "ChatOpenAI"]},
            [[type("FakeMsg", (), {"content": "Hello!", "type": "human"})()]],
            run_id=run_id,
            metadata={"ls_model_name": "gpt-4"},
        )

        events = [e for e in _captured_events if e["event_type"] == "chat_model_start"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["layer_llm"]["model"], "gpt-4")
        self.assertEqual(events[0]["payload"]["layer_llm"]["prompt_count"], 1)
        self.assertIsNotNone(events[0]["payload"]["layer_llm"]["prompt_hash"])

    def test_stream_token_emits_event(self):
        from adapters.probe_langchain_callback import MingCallbackHandler
        from probe_uni import ProbeUni

        probe = ProbeUni(system="langchain-test")
        handler = MingCallbackHandler(probe)
        run_id = uuid.uuid4()

        handler.on_llm_new_token("Hello", run_id=run_id)
        handler.on_llm_new_token(" World", run_id=run_id)

        events = [e for e in _captured_events if e["event_type"] == "stream_token"]
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["payload"]["layer_llm"]["token"], "Hello")
        self.assertEqual(events[1]["payload"]["layer_llm"]["token"], " World")

    def test_llm_end_stream_emits_invoke_and_output(self):
        from adapters.probe_langchain_callback import MingCallbackHandler
        from probe_uni import ProbeUni
        from langchain_core.outputs import LLMResult, Generation

        probe = ProbeUni(system="langchain-test")
        handler = MingCallbackHandler(probe)
        run_id = uuid.uuid4()

        handler.on_llm_new_token("Hello", run_id=run_id)
        handler.on_llm_end(
            LLMResult(
                generations=[
                    [
                        Generation(
                            text="Hello World!",
                            generation_info={"finish_reason": "stop"},
                        )
                    ]
                ],
                llm_output={
                    "token_usage": {"prompt_tokens": 5, "completion_tokens": 3}
                },
            ),
            run_id=run_id,
        )

        invoke_events = [e for e in _captured_events if e["event_type"] == "llm_invoke"]
        self.assertEqual(len(invoke_events), 1)
        llm_p = invoke_events[0]["payload"]["layer_llm"]
        self.assertEqual(llm_p["_source"], "callback_stream")
        self.assertEqual(llm_p["input_tokens"], 5)
        self.assertEqual(llm_p["output_tokens"], 3)
        self.assertEqual(llm_p["finish_reason"], "stop")

        output_events = [e for e in _captured_events if e["event_type"] == "llm_output"]
        self.assertEqual(len(output_events), 1)
        self.assertIn(
            "Hello World!", output_events[0]["payload"]["layer_llm"]["output_text"]
        )

    def test_llm_end_non_stream_does_not_emit_invoke(self):
        from adapters.probe_langchain_callback import MingCallbackHandler
        from probe_uni import ProbeUni
        from langchain_core.outputs import LLMResult, Generation

        probe = ProbeUni(system="langchain-test")
        handler = MingCallbackHandler(probe)
        run_id = uuid.uuid4()

        handler.on_llm_end(
            LLMResult(
                generations=[[Generation(text="Hello!")]],
                llm_output={
                    "token_usage": {"prompt_tokens": 5, "completion_tokens": 3}
                },
            ),
            run_id=run_id,
        )

        invoke_events = [e for e in _captured_events if e["event_type"] == "llm_invoke"]
        self.assertEqual(len(invoke_events), 0)

    def test_llm_error_emits_error(self):
        from adapters.probe_langchain_callback import MingCallbackHandler
        from probe_uni import ProbeUni

        probe = ProbeUni(system="langchain-test")
        handler = MingCallbackHandler(probe)

        handler.on_llm_error(ValueError("API timeout"), run_id=uuid.uuid4())

        events = [e for e in _captured_events if e["event_type"] == "error"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["error_type"], "ValueError")
        self.assertIn("API timeout", events[0]["payload"]["error_msg"])

    def test_tool_start_emits_tool_call(self):
        from adapters.probe_langchain_callback import MingCallbackHandler
        from probe_uni import ProbeUni

        probe = ProbeUni(system="langchain-test")
        handler = MingCallbackHandler(probe)

        handler.on_tool_start(
            {"name": "search_tool"},
            "query: hello",
            run_id=uuid.uuid4(),
            name="search_tool",
        )

        events = [e for e in _captured_events if e["event_type"] == "tool_call"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["layer_tool"]["tool_name"], "search_tool")
        self.assertEqual(events[0]["payload"]["layer_tool"]["tool_status"], "start")
        self.assertEqual(
            events[0]["payload"]["layer_tool"]["_source"], "callback_stream"
        )

    def test_retriever_start_emits_memory_retrieve(self):
        from adapters.probe_langchain_callback import MingCallbackHandler
        from probe_uni import ProbeUni

        probe = ProbeUni(system="langchain-test")
        handler = MingCallbackHandler(probe)

        handler.on_retriever_start(
            {"name": "my_retriever"}, "test query", run_id=uuid.uuid4()
        )

        events = [e for e in _captured_events if e["event_type"] == "memory_retrieve"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["layer_memory"]["query"], "test query")

    def test_chain_start_end_emits_agent_step(self):
        from adapters.probe_langchain_callback import MingCallbackHandler
        from probe_uni import ProbeUni

        probe = ProbeUni(system="langchain-test")
        handler = MingCallbackHandler(probe)
        run_id = uuid.uuid4()

        handler.on_chain_start({}, {"question": "hi"}, run_id=run_id, name="my_chain")
        handler.on_chain_end({"answer": "hello"}, run_id=run_id)

        start_events = [
            e for e in _captured_events if e["event_type"] == "agent_step_start"
        ]
        finish_events = [
            e for e in _captured_events if e["event_type"] == "agent_step_finish"
        ]
        self.assertEqual(len(start_events), 1)
        self.assertEqual(len(finish_events), 1)
        self.assertEqual(
            start_events[0]["payload"]["layer_agent"]["step_id"], "my_chain"
        )

    def test_integration_with_real_model_stream(self):
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import HumanMessage, AIMessage
        from langchain_core.outputs import ChatResult, ChatGeneration
        from adapters.probe_langchain_callback import MingCallbackHandler
        from probe_uni import ProbeUni

        probe = ProbeUni(system="langchain-test")
        handler = MingCallbackHandler(probe)

        class FakeStreamModel(BaseChatModel):
            @property
            def _llm_type(self):
                return "fake"

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                if run_manager:
                    for token in ["Hi", " ", "there"]:
                        run_manager.on_llm_new_token(token)
                return ChatResult(
                    generations=[ChatGeneration(message=AIMessage(content="Hi there"))],
                    llm_output={
                        "token_usage": {"prompt_tokens": 2, "completion_tokens": 2}
                    },
                )

        llm = FakeStreamModel()
        llm.invoke([HumanMessage(content="Hello")], config={"callbacks": [handler]})

        model_start = [
            e for e in _captured_events if e["event_type"] == "chat_model_start"
        ]
        stream_tokens = [
            e for e in _captured_events if e["event_type"] == "stream_token"
        ]
        llm_invoke = [e for e in _captured_events if e["event_type"] == "llm_invoke"]
        llm_output = [e for e in _captured_events if e["event_type"] == "llm_output"]

        self.assertEqual(len(model_start), 1)
        self.assertGreaterEqual(len(stream_tokens), 1)
        self.assertGreaterEqual(len(llm_invoke), 1)
        self.assertEqual(len(llm_output), 1)


if __name__ == "__main__":
    unittest.main()
