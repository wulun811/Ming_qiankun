# LangChain Integration Guide

## Overview

Mingjing provides two ways to integrate with LangChain Python:

| Method | File | Features | Use Case |
|--------|------|----------|----------|
| **Auto (Recommended)** | `probe_langchain.py` | One line, monkey-patches all invoke entry points | Most scenarios, zero config |
| **Manual (Callback)** | `probe_langchain_callback.py` | Requires manual callback injection, captures streaming tokens | Fine-grained control or streaming output |

## Auto Method (Recommended)

### Installation

```python
from adapters.probe_langchain import init_langchain_probe

# After initialization, all LangChain calls are automatically intercepted
init_langchain_probe(system='my_app', mode='white')
```

### Usage Example

```python
from adapters.probe_langchain import init_langchain_probe
init_langchain_probe(system='my_app', mode='white')

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

llm = ChatOpenAI(model="gpt-4")
prompt = ChatPromptTemplate.from_messages([("human", "Hello")])
chain = prompt | llm
result = chain.invoke({})  # Events are automatically captured
```

### Captured Events

| Event Type | Trigger | Fields |
|------------|---------|--------|
| `llm_invoke` | Each ChatModel/LLM.invoke | model, latency_ms, input_tokens, output_tokens, cache_hit, finish_reason |
| `llm_output` | LLM returns text | output_text, output_text_hash |
| `tool_call` | Each Tool.invoke | tool_name, tool_args, tool_result, execution_ms |
| `memory_retrieve` | Each Retriever.invoke | query, results_count, latency_ms, chunk_id, relevance_score, embedding_model |
| `agent_step_start` | Runnable/Chain starts | step_id, session_id, agent_name |
| `agent_step` | Runnable completes | decision_summary |
| `agent_step_finish` | Runnable/Chain finishes | step_status |
| `error` | Any call throws exception | error_type, error_msg |

## Manual Method (Callback)

### Usage Example

```python
from adapters.probe_langchain_callback import MingCallbackHandler
from probe_uni import ProbeUni

probe = ProbeUni(system='my_app', mode='white')
handler = MingCallbackHandler(probe)

# Manually inject callback on each call
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4")
result = llm.invoke("Hello", config={"callbacks": [handler]})
```

### Additional Events Captured by Manual Method

- `stream_token`: Each token in streaming output (auto method does not capture streaming tokens)
- `chat_model_start`: Messages content when ChatModel starts

## LangChain.js Integration

### Installation

Copy these files to your project:
- `probe_langchain.js`
- `_js_base.js`
- `probe_node.js`

### Usage Example

```javascript
const { initLangChainJsProbe } = require('./probe_langchain');

// Must be called BEFORE creating any LangChain instances
initLangChainJsProbe({ system: 'my_app', mode: 'white' });

// Use LangChain.js normally
const { OpenAI } = require("@langchain/openai");
const llm = new OpenAI({ model: "gpt-4" });
const result = await llm.invoke("Hello");
```

### Notes

1. `initLangChainJsProbe()` must be called **before** creating any LangChain instances
2. Requires LangChain.js to be mounted on `globalThis.langchain`
3. Captured events: `llm_invoke`, `llm_output`, `tool_call`, `agent_step_start`, `agent_step_finish`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MING_CONTENT_MAX_LEN` | 2000 | Content truncation length, set to 0 for no truncation |

## Version Compatibility

| Version | Python Auto | Python Callback | JS |
|---------|-------------|-----------------|-----|
| langchain-core >= 0.1 | ✅ | ✅ | - |
| langchain-core >= 1.0 | ✅ | ✅ | - |
| @langchain/core >= 0.1 | - | - | ✅ |
