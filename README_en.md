# Mingjing — AI Agent Diagnostic Refraction Array

> **Zero-intrusion observability** for LLM calls, tool execution, memory retrieval, and Agent orchestration.
>
> [🌏 中文](./README.md) | [📖 Full Docs](updocs/)

![Mingjing LOGO](updocs/image/logo.png)

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-BUSL--1.1-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.11.9m-green.svg)](https://pypi.org/project/mingjing/)

**Mingjing is LIT 1.4's lightweight refraction array, not a standalone diagnostic platform.** It provides zero-intrusion observability for LangChain, LlamaIndex, CrewAI, OpenHands, AutoGPT, and other Agent frameworks via hot-rail JSONL files + SQLite/MySQL persistence layer.

---

## 5-Minute Quick Start

### 1. Install

```bash
pip install mingjing
```

### 2. Run (zero dependencies)

```bash
mkdir -p ~/.ming && cd ~/.ming
ming start
```

### 3. Emit a test event

```bash
python -c "
from probe_uni import ProbeUni
p = ProbeUni(system='demo', mode='white')
p.emit('llm_invoke', {
    'layer_agent': {'step_id': 'test', 'session_id': 's1', 'agent_name': 'demo'},
    'layer_llm': {'model': 'gpt-4', 'input_tokens': 100, 'output_tokens': 50, 'latency_ms': 230},
    'layer_network': {'target_host': 'api.openai.com', 'status_code': 200}
})
print('Event emitted!')
"
```

### 4. View diagnostics

```bash
ming dx list
```

### 5. Open Web Dashboard

```bash
ming web start
# Browser: http://localhost:18088
```

**Auto-update**: Web dashboard auto-exports data.json every 30s + frontend auto-refresh.

---

## Architecture

```
┌─────────────┐     JSONL hot files      ┌──────────────┐
│  Probes     │ ───────────────────────► │  Archiver    │
│  (stateless) │                          │  (sole writer)│
└─────────────┘                          └──────┬───────┘
       │                                        │
       │  LangChain / LlamaIndex                │ SQLite / MySQL
       │  CrewAI / OpenHands                    ▼
       │  AutoGPT / Hermes / MCP         ┌──────────────┐
       │                                 │  Query       │
       └────────────────────────────────►│  Bridge      │
                                          └──────┬───────┘
                                                 │
                                           ┌─────▼─────┐
                                           │  Web UI   │
                                           │  / MCP    │
                                           └───────────┘
```

### Core Modules

| Module | Lines | Responsibility |
|--------|-------|---------------|
| `probe_uni.py` | ~305 | Stateless hot-rail writer, zero database dependency |
| `archiver.py` | ~470 | Sole writer, sequential scan hot → SQLite |
| `cli.py` | ~378 | CLI: dx/health/skill/query/status/web |
| `watchdog.py` | ~259 | Process guardian + disk alerting |
| `query_bridge.py` | ~336 | Read-only query interface |

### Adapters (Official Reference)

| Adapter | Language | Events |
|---------|----------|--------|
| LangChain | Python | llm_invoke, tool_call, memory_retrieve |
| LlamaIndex | Python | llm_invoke, memory_retrieve, agent_step |
| CrewAI | Python | agent_step, llm_invoke |
| OpenHands | Python | agent_step, llm_invoke, tool_call |
| AutoGPT | Python | agent_step, llm_invoke |
| Hermes | Python | llm_invoke, tool_call, memory_retrieve |
| MCP (LIT) | Python | tool_call, memory_retrieve, llm_invoke |
| OpenClaw | JS | llm_invoke, tool_call, error |
| Vercel AI SDK | JS | llm_invoke, tool_call |

> All adapters use **monkey-patch + zero third-party dependency** design. Silent degradation when target framework is not installed.

---

## Dual Mode

| Mode | Env Var | Storage | Dependency |
|------|---------|---------|------------|
| **Standalone** (default) | `MING_MODE=standalone` | SQLite | Python stdlib only |
| **Cluster** | `MING_MODE=cluster` | MySQL | pymysql only |

---

## Documentation

| Document | Description |
|----------|-------------|
| [00 Quick Start](updocs/00_quick_start_en.md) | 5-min experience of the full pipeline |
| [01 Architecture Design](updocs/01_architecture_design_en.md) | Design philosophy, core iron rules, dual-mode architecture |
| [02 Probes & Adapters](updocs/02_probe_and_adapter_en.md) | Probe system, 16+ adapter ecosystem |
| [03 Diagnosis System](updocs/03_diagnosis_system_en.md) | lit_lite plugin, 157 rules, Always-On |
| [04 Data Model](updocs/04_data_model_en.md) | SQLite schema, hash chain, integrity |
| [05 API Specification](updocs/05_api_specification_en.md) | CLI, Web API, unified query |
| [06 Operations Manual](updocs/06_operations_manual_en.md) | Environment variables, deployment, troubleshooting |
| [07 Testing System](updocs/07_testing_system_en.md) | Performance benchmarks, 413 tests |
| [08 OpenClaw User Guide](updocs/08_mingjing_openclaw_user_guide_en.md) | OpenClaw framework integration guide |

---

## Performance

| Scenario | Events | Archive Rate | RSS Memory |
|----------|--------|-------------|------------|
| 15min × 1000/s | 882K | 99.9% | 18MB |
| 3min × 5000/s | 884K | 100% | 18MB |
| 75s × 2000/s (150K) | 150K | 100% | **38MB** |

---

## License

**Business Source License 1.1** — Free for production use by companies and individuals with global annual revenue < $100K. Non-production use (development, testing, evaluation) has no revenue limit.

---

**Mingjing v0.11.9m — Built for the community.**
