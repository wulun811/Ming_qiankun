# probe_langchain_callback.py —— v0.11.9m 乾坤镜 LangChain Callback Handler（包内副本）

import time
import threading

_seen_run_ids = set()
_seen_lock = threading.Lock()


def _is_new_run(run_id):
    run_id_str = str(run_id)
    with _seen_lock:
        if run_id_str in _seen_run_ids:
            return False
        _seen_run_ids.add(run_id_str)
        if len(_seen_run_ids) > 1000:
            _seen_run_ids.clear()
        return True


from langchain_core.callbacks.base import BaseCallbackHandler


class MingCallbackHandler(BaseCallbackHandler):
    def __init__(self, probe):
        super().__init__()
        self._probe = probe
        self._streaming_run_ids = set()

    def __call__(self):
        return self

    def on_chat_model_start(
        self, serialized, messages, *, run_id, parent_run_id=None, **kwargs
    ):
        if not _is_new_run(run_id):
            return
        session_id = None
        try:
            tags = kwargs.get("tags") or []
            metadata = kwargs.get("metadata") or {}
            session_id = metadata.get("session_id") or metadata.get("conversation_id")
        except Exception:
            pass
        texts = []
        for msg_list in messages:
            for m in msg_list if isinstance(msg_list, (list, tuple)) else [msg_list]:
                try:
                    texts.append(str(getattr(m, "content", str(m)))[:500])
                except Exception:
                    texts.append("<unparseable>")
        self._probe.emit(
            "chat_model_start",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "session_id": str(session_id) if session_id else "unknown",
                    "agent_name": "langchain_callback",
                },
                "content": " | ".join(texts) if texts else None,
            },
        )

    def on_llm_new_token(self, token, *, run_id, parent_run_id=None, **kwargs):
        self._streaming_run_ids.add(run_id)
        self._probe.emit(
            "stream_token",
            {
                "token": str(token)[:200],
                "run_id": str(run_id),
            },
        )

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        if run_id not in _seen_run_ids:
            return
        is_stream = run_id in self._streaming_run_ids
        if not is_stream:
            return
        self._streaming_run_ids.discard(run_id)
        output_text = None
        input_tokens = None
        output_tokens = None
        try:
            generations = getattr(response, "generations", None)
            if generations and len(generations) > 0 and len(generations[0]) > 0:
                output_text = str(getattr(generations[0][0], "text", ""))[:500]
            llm_output = getattr(response, "llm_output", None) or {}
            token_usage = llm_output.get("token_usage", {}) or {}
            input_tokens = token_usage.get("prompt_tokens") or token_usage.get(
                "input_tokens"
            )
            output_tokens = token_usage.get("completion_tokens") or token_usage.get(
                "output_tokens"
            )
        except Exception:
            pass
        self._probe.emit(
            "llm_invoke",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "session_id": "unknown",
                    "agent_name": "langchain_callback",
                },
                "layer_llm": {
                    "model": "unknown",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
                "layer_network": {"provider": "langchain", "model": "unknown"},
                "is_stream": True,
            },
        )
        self._probe.emit(
            "llm_output",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "session_id": "unknown",
                    "agent_name": "langchain_callback",
                },
                "layer_llm": {
                    "model": "unknown",
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
                "layer_network": {"provider": "langchain", "model": "unknown"},
                "content": output_text,
            },
        )

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        if run_id not in _seen_run_ids:
            return
        self._probe.emit(
            "error",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "session_id": "unknown",
                    "agent_name": "langchain_callback",
                },
                "error_type": type(error).__name__,
                "error_msg": str(error)[:500],
            },
        )

    def on_tool_start(
        self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs
    ):
        if not _is_new_run(run_id):
            return
        tool_name = (
            serialized.get("name") if isinstance(serialized, dict) else str(serialized)
        )
        self._probe.emit(
            "tool_call",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "session_id": "unknown",
                    "agent_name": "langchain_callback",
                },
                "layer_tool": {
                    "tool_name": tool_name,
                    "args": str(input_str)[:500],
                },
                "layer_network": {"provider": "langchain", "model": ""},
            },
        )

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._probe.emit(
            "error",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "session_id": "unknown",
                    "agent_name": "langchain_callback",
                },
                "error_type": type(error).__name__,
                "error_msg": str(error)[:500],
            },
        )

    def on_retriever_start(
        self, serialized, query, *, run_id, parent_run_id=None, **kwargs
    ):
        if not _is_new_run(run_id):
            return
        self._probe.emit(
            "memory_retrieve",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "session_id": "unknown",
                    "agent_name": "langchain_callback",
                },
                "layer_memory": {
                    "query": str(query)[:500],
                },
                "layer_network": {"provider": "langchain", "model": ""},
            },
        )

    def on_chain_start(
        self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs
    ):
        if not _is_new_run(run_id):
            return
        input_text = str(inputs)[:500]
        self._probe.emit(
            "agent_step",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "session_id": "unknown",
                    "agent_name": "langchain_callback",
                },
                "content": input_text,
            },
        )

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        pass
