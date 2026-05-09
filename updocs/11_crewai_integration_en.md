# CrewAI Integration Guide

## Overview

Mingjing provides **zero-configuration automatic adaptation** for CrewAI, intercepting Agent task execution, Crew orchestration, and LLM calls via monkey-patching to fully capture multi-agent collaboration chains.

| Feature | Description |
|---------|-------------|
| **File** | `probe_crewai.py` |
| **Dependencies** | Zero third-party dependencies (only depends on CrewAI itself) |
| **Adaptation Method** | Monkey-patch |
| **Supported Versions** | crewai >= 0.30 |

## Quick Start

### Installation

```python
from adapters.probe_crewai import init_crewai_probe

# After initialization, all CrewAI calls are automatically intercepted
init_crewai_probe(system='my_crew_app', mode='white')
```

### Usage Example

```python
from adapters.probe_crewai import init_crewai_probe
init_crewai_probe(system='my_crew_app', mode='white')

from crewai import Agent, Task, Crew

# Define Agent
researcher = Agent(
    role='Senior Research Analyst',
    goal='Discover innovative AI technologies',
    backstory='Expert in AI research',
    verbose=True
)

# Define Task
task = Task(
    description='Research latest AI trends',
    agent=researcher
)

# Create and start Crew
crew = Crew(
    agents=[researcher],
    tasks=[task],
    verbose=True
)

# All Agent executions and LLM calls are automatically captured
result = crew.kickoff()
```

## Captured Events

| Event Type | Trigger | Included Fields |
|------------|---------|-----------------|
| `agent_step` | `Agent.execute_task()` executes | step_id, agent_name (Agent.role), task |
| `agent_step` | `Crew.kickoff()` executes | step_id, agent_name, crew_name |
| `agent_step_start` | Agent starts executing task | step_id (Agent.role), agent_name, session_id |
| `agent_step_finish` | Agent task execution completes | step_id, agent_name, step_status |
| `llm_invoke` | Each `LLM.call()` | model, latency_ms, input_tokens, output_tokens, finish_reason |
| `llm_output` | LLM returns text | output_text, output_text_hash |

## Token Extraction

The adapter automatically extracts token usage from CrewAI LLM responses:

1. **`response.usage`**: OpenAI ChatCompletion object's usage attribute
2. **`response.get("usage")`**: Dictionary-format response

If token information cannot be extracted, the corresponding fields will be `None`, which does not affect event archiving.

## Multi-Agent Collaboration Tracking

The CrewAI adapter is particularly suited for tracking multi-agent collaboration scenarios:

- **Agent Level**: Identifies each agent by `Agent.role`
- **Crew Level**: Identifies orchestration container by `Crew.name`
- **LLM Level**: Captures latency and token usage for each underlying LLM call

All events are correlated via `session_id`, allowing complete reconstruction of the collaboration chain after archiving.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MING_CONTENT_MAX_LEN` | 2000 | Content truncation length, set to 0 to disable truncation |

## Notes

1. `init_crewai_probe()` must be called **before** creating any CrewAI instances
2. If CrewAI is not installed, the adapter will silently skip (`ImportError` caught)
3. Agent's `agent_step` events include the first 200 characters of the task description (`task` field)
4. `agent_step_start` and `agent_step_finish` events use Agent.role as the step_id
