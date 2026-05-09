# LangChain 集成指南

## 概述

乾坤镜提供两种 LangChain Python 适配方式：

| 方式 | 文件 | 特点 | 适用场景 |
|------|------|------|----------|
| **自动版（推荐）** | `probe_langchain.py` | 一行代码，monkey-patch 所有 invoke 入口 | 大多数场景，零配置 |
| **手动版（Callback）** | `probe_langchain_callback.py` | 需手动注入 callback，但能捕获流式 token | 需要精细控制或流式输出 |

## 自动版（推荐）

### 安装

```python
from adapters.probe_langchain import init_langchain_probe

# 初始化后自动拦截所有 LangChain 调用
init_langchain_probe(system='my_app', mode='white')
```

### 使用示例

```python
from adapters.probe_langchain import init_langchain_probe
init_langchain_probe(system='my_app', mode='white')

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

llm = ChatOpenAI(model="gpt-4")
prompt = ChatPromptTemplate.from_messages([("human", "你好")])
chain = prompt | llm
result = chain.invoke({})  # 自动捕获事件
```

### 捕获的事件

| 事件类型 | 触发时机 | 包含字段 |
|----------|----------|----------|
| `llm_invoke` | 每次 ChatModel/LLM.invoke | model, latency_ms, input_tokens, output_tokens, cache_hit, finish_reason |
| `llm_output` | LLM 返回文本 | output_text, output_text_hash |
| `tool_call` | 每次 Tool.invoke | tool_name, tool_args, tool_result, execution_ms |
| `memory_retrieve` | 每次 Retriever.invoke | query, results_count, latency_ms, chunk_id, relevance_score, embedding_model |
| `agent_step_start` | Runnable/Chain 开始执行 | step_id, session_id, agent_name |
| `agent_step` | Runnable 执行完成 | decision_summary |
| `agent_step_finish` | Runnable/Chain 执行结束 | step_status |
| `error` | 任何调用抛出异常 | error_type, error_msg |

## 手动版（Callback）

### 使用示例

```python
from adapters.probe_langchain_callback import MingCallbackHandler
from probe_uni import ProbeUni

probe = ProbeUni(system='my_app', mode='white')
handler = MingCallbackHandler(probe)

# 在每次调用时手动注入 callback
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4")
result = llm.invoke("你好", config={"callbacks": [handler]})
```

### 手动版额外捕获的事件

- `stream_token`：流式输出的每个 token（自动版不捕获流式 token）
- `chat_model_start`：ChatModel 开始调用时的 messages 内容

## LangChain.js 集成

### 安装

复制以下文件到项目：
- `probe_langchain.js`
- `_js_base.js`
- `probe_node.js`

### 使用示例

```javascript
const { initLangChainJsProbe } = require('./probe_langchain');

// 必须在创建 LangChain 实例之前调用
initLangChainJsProbe({ system: 'my_app', mode: 'white' });

// 正常使用 LangChain.js
const { OpenAI } = require("@langchain/openai");
const llm = new OpenAI({ model: "gpt-4" });
const result = await llm.invoke("你好");
```

### 注意事项

1. `initLangChainJsProbe()` 必须在创建任何 LangChain 实例**之前**调用
2. 需要 LangChain.js 实例挂载在 `globalThis.langchain` 上
3. 捕获的事件：`llm_invoke`、`llm_output`、`tool_call`、`agent_step_start`、`agent_step_finish`

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MING_CONTENT_MAX_LEN` | 2000 | 内容截断长度，设为 0 不截断 |

## 版本兼容性

| 版本 | Python 自动版 | Python Callback 版 | JS 版 |
|------|---------------|-------------------|-------|
| langchain-core >= 0.1 | ✅ | ✅ | - |
| langchain-core >= 1.0 | ✅ | ✅ | - |
| @langchain/core >= 0.1 | - | - | ✅ |
