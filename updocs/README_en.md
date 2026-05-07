# Mingjing v0.11.10 — Documentation Index

> **AI Agent Diagnostic Refraction Array** — Zero-intrusion observability for LLM calls, tool execution, memory retrieval, and Agent orchestration.

---

## One-liner

Mingjing is a **lightweight observability infrastructure** providing **non-intrusive diagnostic capabilities** for LangChain, LlamaIndex, CrewAI, OpenHands, AutoGPT, Hermes, OpenClaw, Vercel AI SDK, and other AI Agent frameworks.

**Core constraints**: 0 LLM, 0 intrusion, 0 writeback, 0 networking (Standalone mode), read-only queries only.

---

## Core Features

| Feature | Description |
|---------|-------------|
| **Zero intrusion** | monkey-patch silent degradation, no business code modification |
| **Zero LLM calls** | 157 deterministic diagnostic rules (SQL/YAML), no LLM invocations |
| **Zero third-party deps** | Standalone mode: Python stdlib only |
| **Ultra lightweight** | RSS < 40MB, core code < 2000 lines |
| **Auto-diagnosis** | 15 Always-On real-time alerts + 157 triage rules |
| **Cross-framework unified** | 16+ adapters, rules "recognize fields, not systems" |
| **Evidence chain audit** | SHA-256 hash chain + integrity scoring |
| **Unlimited plugins** | Diagnosis/Web dashboard replaceable via YAML contract |

---

## Quick Start

### 1. Install

```bash
pip install mingjing
```

### 2. Run (zero dependencies)

```bash
MING_MODE=standalone ming start
```

### 3. Emit test events

```bash
python -c "
from probe_uni import ProbeUni
p = ProbeUni(system='demo', mode='white')
p.emit('llm_invoke', {
    'layer_agent': {'step_id': 'test', 'session_id': 's1'},
    'layer_llm': {'model': 'gpt-4', 'input_tokens': 100},
    'layer_network': {'target_host': 'api.openai.com'}
})
print('Event emitted!')
"
```

### 4. View diagnostics

```bash
ming dx list
```

### 5. Web Dashboard (Optional)

```bash
ming web serve             # Start
# Browser: http://localhost:18088
ming web stop              # Stop anytime, core unaffected
```

---

## Performance Benchmarks

| Scenario | Events | Archive Rate | RSS Memory |
|----------|--------|-------------|------------|
| 15min × 1000/s | 882K | 99.9% | 18MB |
| 3min × 5000/s | 884K | 100% | 18MB |
| 75s × 2000/s (150K) | 150K | 100% | **38MB** |

> **Resident memory < 40MB** (archiver). Web panel optional + ~50MB. 0 LLM calls, 0 network outbound.

---

## Documentation Index

| Document | Description |
|----------|-------------|
| [00 Quick Start](./00_quick_start_en.md) | 5-min full pipeline experience |
| [01 Architecture Design](./01_architecture_design_en.md) | Design philosophy, core iron rules, dual-mode architecture |
| [02 Probes & Adapters](./02_probe_and_adapter_en.md) | Probe system, 16+ adapter ecosystem |
| [03 Diagnosis System](./03_diagnosis_system_en.md) | lit_lite plugin, 157 rules, Always-On |
| [04 Data Model](./04_data_model_en.md) | SQLite schema, hash chain, integrity |
| [05 API Specification](./05_api_specification_en.md) | CLI, Web API, unified query |
| [06 Operations Manual](./06_operations_manual_en.md) | Environment variables, deployment, troubleshooting |
| [07 Testing System](./07_testing_system_en.md) | Performance benchmarks, 440 tests |
| [08 OpenClaw User Guide](./08_mingjing_openclaw_user_guide_en.md) | OpenClaw framework integration guide |

---

## License

**Business Source License 1.1**

- Free for production use by companies and individuals with global annual revenue < $100K
- Non-production use (development, testing, evaluation) has no revenue limit

---

**Mingjing v0.11.10 — Pure data baseplane + Plugin executor + Three-layer decoupling. Lightweight base, unlimited ecosystem.**
