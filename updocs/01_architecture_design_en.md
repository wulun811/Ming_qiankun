# 01 Architecture Design

> **Mingjing = Pure data baseplane for Agent engineering + Medical Record Authority + Plugin Executor**

---

## I. Design Philosophy

### 1.1 System Positioning

Mingjing is a **lightweight observability infrastructure** that provides **zero-intrusion diagnostics and observability** for AI Agent frameworks. Through hot-rail JSONL + SQLite/MySQL persistence, it fully decouples probe writing from data archiving.

**Core capabilities:**
- **0 LLM**: All diagnostic rules are deterministic SQL/YAML, no LLM calls
- **0 Intrusion**: monkey-patch with silent degradation, no business code modification
- **0 Writeback**: Observe only, never write to target systems
- **Read-only**: Query modules use read-only connections
- **0 Networking**: Standalone mode requires zero network access

> **157<!--M=disease_rules--> rule library** (~135 generic + ~22 Tusunsun-specific). Actual triggerable rules depend on event types emitted by the adapter.

---

## II. Core Iron Rules (Violation = P0)

| Rule | Description | Verification |
|------|-------------|--------------|
| **Probe Purity** | Probes write JSONL only, no sqlite3/pymysql import | AST analysis |
| **Sole Writer** | Only the Archiver writes to persistence layer | Code review |
| **Zero Third-party Deps** | Standalone mode: Python stdlib only | `pip list` |
| **Cluster Single Dep** | Only `pymysql` | `pip list` |
| **Read-only External** | Query modules use read-only connections | Code review |
| **Three-layer Decoupling** | Probes don't know DB; Archiver doesn't know plugins; Frontend doesn't know diagnosis | Code review |
| **Lightweight Base** | Core baseplane < 2000<!--M=budget_base--> lines | `wc -l` |
| **Zero Baggage** | No legacy code, no abandoned files | Code review |

---

## III. Design Principles

| Principle | Description |
|-----------|-------------|
| **Observe, Don't Intervene** | Record only, no business logic modification |
| **Hot-rail Decoupling** | Probes write JSONL, Archiver async-consumes, zero concurrency locks |
| **Lightweight First** | Zero ORM, zero third-party deps, zero-config runnable |
| **Complete Evidence Chain** | SHA-256 hash chain + integrity scoring |
| **Dual-mode Architecture** | Standalone (zero deps) vs Cluster (MySQL) via env var |

---

## IV. Architecture Overview

![Mingjing Architecture Diagram](./image/mingimageen.png)

### 4.1 Data Flow

```
Business System → probe_uni.py → JSONL Hot Rail → archiver.py → SQLite/MySQL
                                               ↓
                                         plugin_runner
                                               ↓
                                    lit_lite / web_dashboard / tusunsun
```

**Three-layer Decoupling:**
1. Probes → know JSONL only, not database
2. Archiver → know SQLite only, not plugins
3. Frontend → know data.json only, not diagnosis

### 4.2 Code Budget

| Layer | Modules | Lines | Role |
|-------|---------|-------|------|
| **Core Base** | archiver family + credential_vault + plugin_runner | ~1,350 | Sole writer |
| **Cluster Extension** | cluster_archiver + cluster_pool + bridge | ~463 | MySQL batch archive |
| **Plugin Layer** | lit_lite / web_dashboard / tusunsun | No limit | YAML contract replaceable |
| **Adapter Layer** | 16+ Python/JS/Go adapters | No limit | Each new framework = new node |

> **Lightweight base**: Core non-removable parts < 2000<!--M=budget_base--> lines, plugins/adapters have no budget limit.

---

## V. Dual-mode Architecture

| Dimension | Standalone | Cluster |
|-----------|------------|---------|
| Storage | SQLite | MySQL (InnoDB) |
| Extra Deps | Zero | Only `pymysql` |
| Hot Rail | Local JSONL | Local JSONL (network-decoupled buffer) |
| Archiver | Single-threaded serial | Connection pool + batch write |
| Query | Read local SQLite | Read central MySQL |
| Scale | 1-3 projects / single machine | 5-20+ projects / distributed |
| Switch | `MING_MODE=standalone` | `MING_MODE=cluster` |

---

## VI. Niche Positioning

| Dimension | Mingjing | Competitors |
|-----------|----------|-------------|
| **Runtime memory** | RSS < 40MB, zero deps | Unique tier |
| **Intrusiveness** | monkey-patch silent degradation | Unique tier |
| **Auto-diagnosis** | 157<!--M=disease_rules--> rules | Competitors output metrics only |
| **Diagnosis-prescription separation** | lit_lite → Web Dashboard → Human/LIT review | Unique tier |
| **Cross-framework unified** | 16+ adapters | Competitors are framework-bound |
| **Evidence chain audit** | SHA-256 hash chain | Not available |

---

## VII. Version History

| Version | Milestone |
|---------|-----------|
| v0.8 | Probe purity — JSONL only, no database knowledge |
| v0.11.6 | Diagnosis rule hardening: 18 fixes + 120K stress test |
| v0.11.7 | Tusunsun integration: 30+ events + P0 real-time push |
| v0.11.8 | Credential vault + full audit fixes |
| v0.11.9 | **Always-On alerts**: 15 rules bypassing triage blind spots; unified query API; 413 tests |
| v0.11.9m | Probe management (list/uninstall); Hermes install fix; code review false positive cleanup |
| v0.11.10 | Three-tier compression + zlib; streaming archiver; RAM reduced ~15%; 440 tests |

---

**Design philosophy: Lightweight base (< 2000<!--M=budget_base--> lines), unlimited plugins, unlimited adapters.**
