# 02 Probes & Adapters

> **Probe purity**: Write JSONL only, never import sqlite3/pymysql

---

## I. Probe System

### 1.1 Probe Modes

| Mode | Description | Integrity Score |
|------|-------------|-----------------|
| `white` | Full payload, all fields visible | 1.0 |
| `black` | Sanitized payload (allowed fields only) | 0.6 |

### 1.2 Probe Methods

| Method | Purpose |
|--------|---------|
| `emit(event_type, payload)` | Batch-write to hot rail |
| `expect(event_type, within_seconds)` | Declare expected event |
| `fulfill(event_type)` | Mark expected event as fulfilled |
| `touch()` | Heartbeat |
| `health()` | Send health metrics |

### 1.3 Event Types

| Category | Event Types |
|----------|-------------|
| **Metadata** | `__register__`, `__touch__`, `__detach__`, `__expect__`, `__fulfill__`, `__health__`, `__chain_reboot__` |
| **Core Business** | `llm_invoke`, `tool_call`, `memory_retrieve`, `agent_step`, `error` |
| **Watchdog Synthetic** | `system_crash`, `silent_anomaly`, `io_anomaly` |
| **Platform** | `platform_snapshot` |

### 1.4 Required Payload Layers

| Event Type | Required Layers |
|------------|-----------------|
| `llm_invoke` | `layer_agent`, `layer_llm`, `layer_network` |
| `tool_call` | `layer_agent`, `layer_tool`, `layer_network` |
| `memory_retrieve` | `layer_agent`, `layer_memory` |
| `agent_step` | `layer_agent` |
| `error` | `layer_agent` |

> Missing layers are flagged with `_incomplete` and `_integrity_hint: "partial"`.

### 1.5 Probe Features

| Feature | Description |
|---------|-------------|
| **Batch write** | Default 10 events/batch, 200ms flush interval |
| **Lamport clock** | Multi-process shared file, monotonic increment |
| **File locking** | Linux: fcntl, Windows: msvcrt |
| **Disk protection** | Auto-reject writes when disk < 500MB |
| **Degradation** | Fallback to TEMP/ming_fallback when hot rail unavailable |

---

## II. Adapter Ecosystem

### 2.1 Python Adapters

| Adapter | Framework | Events Detected |
|---------|-----------|-----------------|
| `probe_langchain.py` | LangChain | llm_invoke, tool_call, memory_retrieve |
| `probe_llamaindex.py` | LlamaIndex | llm_invoke, memory_retrieve, agent_step |
| `probe_crewai.py` | CrewAI | agent_step, llm_invoke |
| `probe_openhands.py` | OpenHands | agent_step, llm_invoke, tool_call |
| `probe_autogpt.py` | AutoGPT | agent_step, llm_invoke |
| `extensions/hermes/` | Hermes Agent | llm_invoke, tool_call, memory_retrieve |
| `probe_lit.py` | LIT | tool_call, memory_retrieve, llm_invoke |
| `probe_opencode_wrapper.py` | OpenCode | llm_invoke, tool_call, memory_retrieve |

### 2.2 JavaScript Adapters

| Adapter | Framework | Events Detected |
|---------|-----------|-----------------|
| `probe_langchain.js` | LangChain.js | llm_invoke, tool_call |
| `probe_lit.js` | LIT | tool_call, memory_retrieve |
| `probe_openclaw.js` | **OpenClaw** ✅ | llm_invoke, tool_call, error |
| `probe_vercel.js` | Vercel AI SDK | llm_invoke, tool_call |
| `probe_bridge.js` | **Tusunsun** ✅ | 30+ event types |

### 2.3 Go Adapters

| Adapter | Framework |
|---------|-----------|
| `probe_langchain_go.go` | LangChainGo |
| `probe_opencode_wrapper.go` | OpenCode |

---

## III. Adapter Design Patterns

### 3.1 Method A: Monkey-patch (9 adapters)

Runtime method replacement wrapping event emission. Zero impact when target class doesn't exist.

**Applicable to**: LangChain, LlamaIndex, CrewAI, OpenHands, AutoGPT, LIT, Vercel.

### 3.2 Method B: Plugin Hooks (3 adapters)

Register callbacks via official framework API, zero intrusion.

**Applicable to**:
- OpenClaw: `globalThis.openclaw.hooks`
- Hermes: `ctx.register_hook()`
- Tusunsun: `core.on()` EventEmitter

### 3.3 Method C: Wrapper + Polling (3 adapters)

Start target as subprocess, collect via DB/filesystem, no target process modification.

**Applicable to**: OpenCode wrapper (SQLite polling).

---

## IV. Diagnostic Limits by Adapter

> **Important**: 157 rules depend on specific event types. Different adapters emit different events, so actual triggerable diagnoses vary.

| Adapter | Diagnosis Limit | Notes |
|---------|:---------------:|-------|
| **langchain** | 100 | No platform_snapshot (-12 rules) |
| **hermes** | 89 | No memory_retrieve (-17 rules) |
| **opencode** | 61 | No memory_retrieve + platform_snapshot |
| **openclaw** | 65 | No memory_retrieve + platform_snapshot |
| **tusunsun** | 75 | 30 Tusunsun-specific rules |
| **autogpt** | 14 | Only agent_step + llm_invoke |
| **crewai** | 38 | No tool_call + memory_retrieve |
| **vercel** | 13 | Only llm_invoke + llm_output |

---

## V. Core Probe Runtimes

| File | Language | Description |
|------|----------|-------------|
| `probe_uni.py` | Python | Core Python probe, base for all Python adapters |
| `probe_node.js` | JavaScript | Core JS probe, dependency of all JS adapters |
| `probe_go.go` | Go | Core Go probe, provides Emit() and Wrap |

---

**Adapters unlimited: Every new framework = a new ecosystem node.**
