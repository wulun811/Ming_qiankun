# probe_langchain_callback.py —— v0.11.9m 乾坤镜 LangChain Callback Handler
# 职责：通过 LangChain 官方 BaseCallbackHandler 捕获 monkey-patch 覆盖不到的事件
#   - stream/batch/abatch（不走 invoke 入口）
#   - 对每一个 ChatModel 调用的 prompts/messages
#   - 逐 token 粒度输出（on_llm_new_token）
# 依赖：零第三方依赖，仅 langchain_core

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


try:
    from langchain_core.callbacks.base import BaseCallbackHandler
except ImportError:
    BaseCallbackHandler = object


class MingCallbackHandler(BaseCallbackHandler):
    """乾坤镜 Callback Handler —— 注入每个 RunnableConfig.callbacks

    捕获事件：
      - chat_model_start: 每次 ChatModel 调用（含 prompts/messages）
      - stream_token: 流式输出的每个 token
      - llm_invoke + llm_output: 仅在 stream/batch（非 invoke）时发射
    """

    def __init__(self, probe):
        super().__init__()
        self._probe = probe
        self._streaming_run_ids = set()

    def __call__(self):
        return self

    def on_chat_model_start(
        self,
        serialized,
        messages,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        name=None,
        **kwargs,
    ):
        if not _is_new_run(run_id):
            return
        serialized_name = None
        if isinstance(serialized, dict):
            serialized_name = serialized.get("name") or serialized.get("id", [None])[-1]

        model_name = (
            (metadata or {}).get("ls_model_name")
            or (metadata or {}).get("model")
            or serialized_name
        )

        prompt_texts = []
        for msg_list in messages if isinstance(messages, (list, tuple)) else [messages]:
            for msg in msg_list if isinstance(msg_list, (list, tuple)) else [msg_list]:
                content = getattr(msg, "content", str(msg))
                if content:
                    prompt_texts.append(str(content)[:500])

        prompt_hash = None
        combined = "".join(prompt_texts)
        if combined:
            import hashlib

            prompt_hash = hashlib.sha256(combined.encode()).hexdigest()[:16]

        self._probe.emit(
            "chat_model_start",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "agent_name": "langchain",
                    "session_id": (metadata or {}).get("conversation_id")
                    or (metadata or {}).get("session_id"),
                },
                "layer_llm": {
                    "model": model_name,
                    "prompt_count": len(prompt_texts),
                    "prompt_hash": prompt_hash,
                    "prompt_preview": prompt_texts[0][:200] if prompt_texts else None,
                    "tags": tags or [],
                },
            },
        )

    def on_llm_new_token(self, token, *, run_id, parent_run_id=None, **kwargs):
        with _seen_lock:
            self._streaming_run_ids.add(str(run_id))
        self._probe.emit(
            "stream_token",
            {
                "layer_llm": {
                    "token": token,
                    "run_id": str(run_id)[:8],
                },
            },
        )

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        run_id_str = str(run_id)
        is_stream = False
        with _seen_lock:
            is_stream = run_id_str in self._streaming_run_ids
        if not is_stream:
            return

        input_tokens = None
        output_tokens = None
        llm_output = getattr(response, "llm_output", None) if response else None
        if isinstance(llm_output, dict):
            usage = llm_output.get("token_usage") or llm_output.get("usage")
            if usage:
                input_tokens = usage.get("prompt_tokens") or usage.get("input_tokens")
                output_tokens = usage.get("completion_tokens") or usage.get(
                    "output_tokens"
                )

        output_text = None
        try:
            gens = getattr(response, "generations", None)
            if gens and len(gens) > 0 and len(gens[0]) > 0:
                gen = gens[0][0]
                text = getattr(gen, "text", None)
                if text:
                    output_text = text
                else:
                    msg = getattr(gen, "message", None)
                    if msg:
                        output_text = getattr(msg, "content", None)
        except (IndexError, AttributeError):
            pass

        finish_reason = None
        try:
            gens = getattr(response, "generations", None)
            if gens and len(gens) > 0 and len(gens[0]) > 0:
                gen_info = getattr(gens[0][0], "generation_info", {})
                if isinstance(gen_info, dict):
                    finish_reason = gen_info.get("finish_reason")
        except (IndexError, AttributeError):
            pass

        self._probe.emit(
            "llm_invoke",
            {
                "layer_agent": {"step_id": run_id_str[:8], "agent_name": "langchain"},
                "layer_llm": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "finish_reason": finish_reason,
                    "latency_ms": None,
                    "_source": "callback_stream",
                },
            },
        )

        if output_text:
            text_hash = None
            try:
                import hashlib

                text_hash = hashlib.sha256(str(output_text).encode()).hexdigest()[:16]
            except Exception:
                pass
            self._probe.emit(
                "llm_output",
                {
                    "layer_agent": {
                        "step_id": run_id_str[:8],
                        "agent_name": "langchain",
                    },
                    "layer_llm": {
                        "output_text": str(output_text)[:2000],
                        "output_text_hash": text_hash,
                    },
                    "layer_network": {"target_host": "local"},
                },
            )

        with _seen_lock:
            self._streaming_run_ids.discard(run_id_str)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._probe.emit(
            "error",
            {
                "layer_agent": {"step_id": str(run_id)[:8], "agent_name": "langchain"},
                "error_type": type(error).__name__
                if isinstance(error, Exception)
                else str(type(error)),
                "error_msg": str(error)[:500],
            },
        )

    def on_tool_start(
        self,
        serialized,
        input_str,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        name=None,
        **kwargs,
    ):
        if not _is_new_run(run_id):
            return
        tool_name = name or (
            serialized.get("name") if isinstance(serialized, dict) else None
        )
        self._probe.emit(
            "tool_call",
            {
                "layer_agent": {"step_id": str(run_id)[:8], "agent_name": "langchain"},
                "layer_tool": {
                    "tool_name": tool_name,
                    "tool_args": input_str,
                    "tool_status": "start",
                    "_source": "callback_stream",
                },
            },
        )

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        pass

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._probe.emit(
            "error",
            {
                "layer_agent": {"step_id": str(run_id)[:8], "agent_name": "langchain"},
                "error_type": type(error).__name__
                if isinstance(error, Exception)
                else str(type(error)),
                "error_msg": str(error)[:500],
            },
        )

    def on_retriever_start(
        self,
        serialized,
        query,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        name=None,
        **kwargs,
    ):
        if not _is_new_run(run_id):
            return
        self._probe.emit(
            "memory_retrieve",
            {
                "layer_agent": {"step_id": str(run_id)[:8], "agent_name": "langchain"},
                "layer_memory": {"query": str(query)[:200], "memory_type": "retriever"},
            },
        )

    def on_retriever_end(self, documents, *, run_id, parent_run_id=None, **kwargs):
        pass

    def on_retriever_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._probe.emit(
            "error",
            {
                "layer_agent": {"step_id": str(run_id)[:8], "agent_name": "langchain"},
                "error_type": type(error).__name__
                if isinstance(error, Exception)
                else str(type(error)),
                "error_msg": str(error)[:500],
            },
        )

    def on_chain_start(
        self,
        serialized,
        inputs,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        name=None,
        **kwargs,
    ):
        if not _is_new_run(run_id):
            return
        self._probe.emit(
            "agent_step_start",
            {
                "layer_agent": {
                    "step_id": name or str(run_id)[:8],
                    "agent_name": "langchain",
                    "step_status": "start",
                },
                "layer_network": {"target_host": "local"},
            },
        )

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        self._probe.emit(
            "agent_step_finish",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "agent_name": "langchain",
                    "step_status": "finish",
                },
                "layer_network": {"target_host": "local"},
            },
        )

    def on_chain_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._probe.emit(
            "error",
            {
                "layer_agent": {"step_id": str(run_id)[:8], "agent_name": "langchain"},
                "error_type": type(error).__name__
                if isinstance(error, Exception)
                else str(type(error)),
                "error_msg": str(error)[:500],
            },
        )
        self._probe.emit(
            "agent_step_finish",
            {
                "layer_agent": {
                    "step_id": str(run_id)[:8],
                    "agent_name": "langchain",
                    "step_status": "finish",
                },
                "layer_network": {"target_host": "local"},
            },
        )
