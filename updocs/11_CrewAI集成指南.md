# CrewAI 集成指南

## 概述

乾坤镜为 CrewAI 提供**零配置自动适配**，通过 monkey-patch 拦截 Agent 任务执行、Crew 编排和 LLM 调用，完整捕获多 Agent 协作链路。

| 特性 | 说明 |
|------|------|
| **文件** | `probe_crewai.py` |
| **依赖** | 零第三方依赖（仅依赖 CrewAI 本身） |
| **适配方式** | 猴子补丁（monkey-patch） |
| **适用版本** | crewai >= 0.30 |

## 快速开始

### 安装

```python
from adapters.probe_crewai import init_crewai_probe

# 初始化后自动拦截所有 CrewAI 调用
init_crewai_probe(system='my_crew_app', mode='white')
```

### 使用示例

```python
from adapters.probe_crewai import init_crewai_probe
init_crewai_probe(system='my_crew_app', mode='white')

from crewai import Agent, Task, Crew

# 定义 Agent
researcher = Agent(
    role='Senior Research Analyst',
    goal='Discover innovative AI technologies',
    backstory='Expert in AI research',
    verbose=True
)

# 定义 Task
task = Task(
    description='Research latest AI trends',
    agent=researcher
)

# 创建并启动 Crew
crew = Crew(
    agents=[researcher],
    tasks=[task],
    verbose=True
)

# 所有 Agent 执行、LLM 调用都会被自动捕获
result = crew.kickoff()
```

## 捕获的事件

| 事件类型 | 触发时机 | 包含字段 |
|----------|----------|----------|
| `agent_step` | `Agent.execute_task()` 执行 | step_id, agent_name (Agent.role), task |
| `agent_step` | `Crew.kickoff()` 执行 | step_id, agent_name, crew_name |
| `agent_step_start` | Agent 开始执行任务 | step_id (Agent.role), agent_name, session_id |
| `agent_step_finish` | Agent 任务执行完成 | step_id, agent_name, step_status |
| `llm_invoke` | 每次 `LLM.call()` | model, latency_ms, input_tokens, output_tokens, finish_reason |
| `llm_output` | LLM 返回文本 | output_text, output_text_hash |

## Token 提取

适配器自动从 CrewAI LLM 返回中提取 token 用量：

1. **`response.usage`**：OpenAI ChatCompletion 对象的 usage 属性
2. **`response.get("usage")`**：字典格式的 response

如果无法提取 token 信息，对应字段为 `None`，不影响事件归档。

## 多 Agent 协作追踪

CrewAI 适配器特别适合追踪多 Agent 协作场景：

- **Agent 级别**：通过 `Agent.role` 识别每个 Agent 的身份
- **Crew 级别**：通过 `Crew.name` 识别编排容器
- **LLM 级别**：捕获每次底层 LLM 调用的延迟和 token 用量

所有事件通过 `session_id` 关联，可在归档后完整还原协作链路。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MING_CONTENT_MAX_LEN` | 2000 | 内容截断长度，设为 0 不截断 |

## 注意事项

1. `init_crewai_probe()` 必须在创建任何 CrewAI 实例**之前**调用
2. 如果 CrewAI 未安装，适配器会静默跳过（`ImportError` 捕获）
3. Agent 的 `agent_step` 事件包含任务描述的前 200 字符（`task` 字段）
4. `agent_step_start` 和 `agent_step_finish` 事件以 Agent.role 作为 step_id
