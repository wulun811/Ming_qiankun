# LlamaIndex Integration Guide

## Overview

Mingjing provides **zero-configuration automatic adaptation** for LlamaIndex, intercepting LLM calls, vector retrievals, and document ingestion pipelines via monkey-patching without requiring any business code changes.

| Feature | Description |
|---------|-------------|
| **File** | `probe_llamaindex.py` |
| **Dependencies** | Zero third-party dependencies (only depends on LlamaIndex itself) |
| **Adaptation Method** | Monkey-patch |
| **Supported Versions** | llama-index-core >= 0.10 |

## Quick Start

### Installation

```python
from adapters.probe_llamaindex import init_llamaindex_probe

# After initialization, all LlamaIndex calls are automatically intercepted
init_llamaindex_probe(system='my_rag_app', mode='white')
```

### Usage Example

```python
from adapters.probe_llamaindex import init_llamaindex_probe
init_llamaindex_probe(system='my_rag_app', mode='white')

from llama_index.core.llms import ChatMessage
from llama_index.llms.openai import OpenAI
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader

# LLM calls are automatically captured
llm = OpenAI(model="gpt-4")
response = llm.complete("Hello, please introduce yourself")

# Document ingestion and retrieval are also automatically captured
documents = SimpleDirectoryReader("data").load_data()
index = VectorStoreIndex.from_documents(documents)
query_engine = index.as_query_engine()
result = query_engine.query("What is Mingjing?")
```

## Captured Events

| Event Type | Trigger | Included Fields |
|------------|---------|-----------------|
| `llm_invoke` | Each `LLM.complete()` / `LLM.chat()` | model, latency_ms, input_tokens, output_tokens, finish_reason |
| `llm_output` | LLM returns text | output_text, output_text_hash |
| `memory_retrieve` | Each `BaseRetriever.retrieve()` | query, results_count, latency_ms, memory_type, memory_store |
| `agent_step_start` | `IngestionPipeline.run()` starts | step_id, agent_name, session_id |
| `agent_step_finish` | `IngestionPipeline.run()` ends | step_id, agent_name, step_status |

## Token Extraction

The adapter automatically extracts token usage from LlamaIndex's `CompletionResponse`:

1. **`response.raw.usage`**: Raw OpenAI response object
2. **`response.additional_kwargs.usage`**: Usage info in additional kwargs

If token information cannot be extracted, the corresponding fields will be `None`, which does not affect event archiving.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MING_CONTENT_MAX_LEN` | 2000 | Content truncation length, set to 0 to disable truncation |

## Notes

1. `init_llamaindex_probe()` must be called **before** creating any LlamaIndex instances
2. If LlamaIndex is not installed, the adapter will silently skip (`ImportError` caught)
3. Document ingestion (`IngestionPipeline`) step events use `ingestion` as the step_id
