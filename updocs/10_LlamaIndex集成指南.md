# LlamaIndex 集成指南

## 概述

乾坤镜为 LlamaIndex 提供**零配置自动适配**，通过 monkey-patch 拦截 LLM 调用、向量检索和文档摄入流水线，无需修改业务代码。

| 特性 | 说明 |
|------|------|
| **文件** | `probe_llamaindex.py` |
| **依赖** | 零第三方依赖（仅依赖 LlamaIndex 本身） |
| **适配方式** | 猴子补丁（monkey-patch） |
| **适用版本** | llama-index-core >= 0.10 |

## 快速开始

### 安装

```python
from adapters.probe_llamaindex import init_llamaindex_probe

# 初始化后自动拦截所有 LlamaIndex 调用
init_llamaindex_probe(system='my_rag_app', mode='white')
```

### 使用示例

```python
from adapters.probe_llamaindex import init_llamaindex_probe
init_llamaindex_probe(system='my_rag_app', mode='white')

from llama_index.llms.openai import OpenAI
from llama_index.core import VectorStoreIndex, SimpleDirectoryReader

# 使用讯飞星辰 MaaS 平台（OpenAI 兼容接口）
llm = OpenAI(
    model="astron-code-latest",
    api_base="https://maas-coding-api.cn-huabei-1.xf-yun.com/v2",
    api_key="your-api-key",
)

# LLM 调用会被自动捕获
response = llm.complete("你好，请介绍一下自己")

# 文档摄入和检索也会被自动捕获
documents = SimpleDirectoryReader("data").load_data()
index = VectorStoreIndex.from_documents(documents)
query_engine = index.as_query_engine()
result = query_engine.query("乾坤镜是什么？")
```

## 捕获的事件

| 事件类型 | 触发时机 | 包含字段 |
|----------|----------|----------|
| `llm_invoke` | 每次 `LLM.complete()` / `LLM.chat()` | model, latency_ms, input_tokens, output_tokens, finish_reason |
| `llm_output` | LLM 返回文本 | output_text, output_text_hash |
| `memory_retrieve` | 每次 `BaseRetriever.retrieve()` | query, results_count, latency_ms, memory_type, memory_store |
| `agent_step_start` | `IngestionPipeline.run()` 开始 | step_id, agent_name, session_id |
| `agent_step_finish` | `IngestionPipeline.run()` 结束 | step_id, agent_name, step_status |

## Token 提取

适配器自动从 LlamaIndex 的 `CompletionResponse` 中提取 token 用量：

1. **`response.raw.usage`**：原始 OpenAI response 对象
2. **`response.additional_kwargs.usage`**：附加字段中的 usage 信息

如果无法提取 token 信息，对应字段为 `None`，不影响事件归档。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MING_CONTENT_MAX_LEN` | 2000 | 内容截断长度，设为 0 不截断 |

## 注意事项

1. `init_llamaindex_probe()` 必须在创建任何 LlamaIndex 实例**之前**调用
2. 如果 LlamaIndex 未安装，适配器会静默跳过（`ImportError` 捕获）
3. 文档摄入（`IngestionPipeline`）的 step 事件以 `ingestion` 为 step_id
