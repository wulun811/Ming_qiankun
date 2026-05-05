# 03 Diagnosis System

> **lit_lite is a diagnostic plugin**, replaceable via YAML contract with other engines.

---

## I. Rule Library Overview

| Type | Count | Description |
|------|-------|-------------|
| **Generic rules** | ~135 | Triggerable by any adapter (except Tusunsun-specific) |
| **Tusunsun-specific** | ~22 | Depend on kunxiansuo/ying/sandbox events |
| **Always-On** | 15 | Bypass triage, real-time alerting |
| **Ghost/Deferred** | 13 | GHOST_FIELDS (7) + DEFERRED_IDS (6), reference preserved |

> **Total: 157 rule definitions** (`config/diseases.yaml`).

---

## II. Diagnostic Mechanism

### 2.1 Triage Cycle

- Default: every 30 minutes (`MING_TRIAGE_INTERVAL_SEC=1800`)
- Can be shortened to 30s for dense monitoring
- Always-On rules skip triage check, execute on every archiver flush

### 2.2 Scoring Algorithm

**Per-dimension score = maximum** confidence among all matching rules in that dimension. No match = 0.

### 2.3 Confidence Calculation

| Severity | Base Confidence |
|----------|-----------------|
| P0 | 90% |
| P1 | 85% |
| P2 | 75% |

**Final confidence = Base confidence × triage_multiplier**

### 2.4 Cross-validation Beyond 100%

When a system is hit by ≥2 independent rules simultaneously, confidence rises to **100%**.

Rationale: Two independent observation sources confirming the same symptom = confirmed observation.

| Scenario | Confidence |
|----------|------------|
| Single rule hit | ≤90% |
| Cross-validated | **100%** |

---

## III. Always-On Real-time Alerts (v0.11.9)

### 3.1 Background

Triage runs every 30 minutes. P0/P1 critical rules (e.g., dangerous tool calls, token spikes) can't tolerate blind spots.

### 3.2 Mechanism

Add `always_on: true` to rule definition. Diagnostic loop skips triage snapshot check and executes SQL directly.

### 3.3 Current Always-On Rules (15, currently Tusunsun-only)

| ID | Name | Severity |
|----|------|----------|
| SYS-024 | Kunxiansuo repeated circuit-break | P0 |
| SYS-026 | Kunxiansuo anchor completely lost | P0 |
| SYS-027 | Sandbox process killed | P0 |
| SYS-031 | Health endpoint severely degraded | P0 |
| SYS-032 | Token consumption spike | P1 |
| SYS-033 | System crash | P0 |
| SYS-036 | Archiver heartbeat slow | P1 |
| SYS-038 | Probe packet loss | P1 |
| MDL-040 | Model API key not configured | P0 |
| TLT-058 | Dangerous tool call | P0 |
| TLT-059 | Tool side-effects too large | P2 |
| MEM-083 | Memory retrieval failure | P1 |
| AGT-068 | Step sequence loop | P1 |
| AGT-069 | Agent idle for too long | P1 |
| AGT-070 | Agent crash restart | P0 |

---

## IV. Evidence Quality Grades

| Grade | Standard | Code Reachable |
|-------|----------|----------------|
| 1 = Basic | Has event ID or key field | ✅ Min reachable |
| 2 = Complete | ≥ 1 event ID + ≥ 3 key fields | ✅ |
| 3 = Cross-validated | ≥ 2 event IDs + ≥ 6 key fields | ✅ |

---

## V. Semantic Gate

Network-related diagnoses **must** include `layer_network` in evidence, otherwise confidence is capped at 50%, quality at 1.

---

## VI. Rule Audit History

### 6.1 First Round Fixes (8 rules)

| Rule ID | Issue | Fix |
|---------|-------|-----|
| TLT-048 | `exec` in blacklist wrongly flagged OpenClaw bash tool | Removed `exec` |
| TLT-043 | SQL only checks status='success', misses downstream errors | Added EXISTS subquery |
| SYS-003 | Global max/min ratio falsely diagnosed warm-up | Sliding window (1.8x) |
| SYS-004 | Threshold 1GB too low | Raised to 5GB |
| SYS-008 | HAVING condition always true | Hardcoded strftime |

### 6.2 Second Round Fixes (10 rules)

| Rule ID | Issue | Fix |
|---------|-------|-----|
| AGT-060 | No HAVING condition | AVG subquery for imbalance detection |
| MDL-038 | Desc says "has LLM no tool output", SQL only counts LLM | Added NOT EXISTS |
| MEM-069 | Desc says "has retrieval no store", SQL only counts retrieve | Added NOT EXISTS |
| TLT-042 | SQL identical to TLT-046 | Ghost |
| MEM-076 | SQL identical to disabled MEM-075 | Ghost |

---

## VII. Prescription System (Planned 🚧)

`config/remedies.yaml` defines prescription rules, executed by `src/remedy_engine.py`.

> Currently diagnosis-only. Prescription execution requires MCP or human review.

---

**Diagnosis philosophy: Deterministic rules (0 LLM), complete evidence chain (SHA-256), graded confidence (P0-P2).**
