# 07 Testing System

> **440 passed, 0 failed, 2 skipped** (complete test suite, ~155 seconds)

---

## I. Three-tier Testing

| Tier | Script | Duration | Purpose |
|------|--------|----------|---------|
| **L1: Smoke** | `scripts/smoke_test.py` | ~15s | Quick install verification |
| **L2: Full** | `python -m pytest tests/` | ~35s | Developer/CI verification |
| **L3: Performance** | `scripts/bench_continuity.py` | 1-15min | Performance report |

---

## II. Smoke Test (7 items)

| Test | Content | Pass Criteria |
|------|---------|---------------|
| T-M1 | Install verification | Core files have no third-party deps |
| T-M2 | Process start | Archiver process alive |
| T-M3 | Write event | Hot rail directory has JSONL files |
| T-M4 | Archive performance | Latency < 5s, rate ≥ 200 events/s |
| T-M5 | Integrity | Hash chain coverage ≥ 90% |
| T-M6 | Dashboard API | HTTP 200 + valid JSON |
| T-M7 | Clean exit | exit_code=0 |

```bash
python scripts/smoke_test.py        # Full test
python scripts/smoke_test.py fast   # Fast mode (skip Dashboard)
```

---

## III. Performance Benchmarks

### 3.1 Scenario A: 15min × 1000/s

| Metric | Result |
|--------|--------|
| Total events | 882,400 |
| Archive rate | **99.9%** |
| RSS | 17.7 → 18.2 MB |
| Disk | 2.63 GB |

### 3.2 Scenario B: 3min × 5000/s

| Metric | Result |
|--------|--------|
| Total events | 883,700 |
| Archive rate | **100%** |
| RSS | 17.8 → 18.4 MB |
| Disk | 1.12 GB |

### 3.3 Scenario C: 120s × 1000/s + Live Triage

| Metric | Result |
|--------|--------|
| Total events | 120,000 |
| Drop rate | **0.04%** |
| RSS baseline → peak → end | 29.0 → 54.1 → 45.4 MB |
| CPU avg | ~100% (1 core) |
| Triage time | 2,975 ms (120K events) |
| Diagnoses generated | 45,645 (14 triage rounds) |

### 3.4 Scenario D: 75s × 2000/s (150K events)

| Metric | Result |
|--------|--------|
| Total events | 150,000 |
| Archive rate | **100%** |
| RSS baseline → end | **37.8 → 38.7 MB** (+0.9 MB, +2.4%) |
| DB size | 72 MB |
| RSS range | 38.3~38.9 MB, zero leakage |

```bash
python scripts/bench_continuity.py                     # 15min, 1000/s
python scripts/bench_continuity.py --rate 5000         # 15min, 5000/s
python scripts/bench_continuity.py --total 1000000     # 1M events
python scripts/bench_continuity.py --rate 5000 --duration 300  # 5000/s, 5min
```

---

## IV. Test Files

| Test File | Scope |
|-----------|-------|
| `test_stress_short.py` | Short stress test |
| `test_stress_150k.py` | 150K event stress + performance curve |
| `test_archiver*.py` | Archiver core |
| `test_probe*.py` | Probe functionality |
| `test_probe_management.py` | Probe list/uninstall commands |
| `test_lit*.py` | Diagnostic plugin |
| `test_cli*.py` | CLI commands |

---

## V. Test Outputs

| File | Description |
|------|-------------|
| `reports/smoke_test_results.json` | Smoke test JSON |
| `reports/smoke_test_report.html` | Smoke test visual report |
| `reports/bench_report.html` | Benchmark visual report |
| `reports/bench_data.jsonl` | Benchmark raw time-series |

---

## VI. CI Results

**440 passed, 0 failed, 2 skipped** (v0.11.10)

- 19 new probe management tests
- Covers 22+ modules
- Includes stress tests at 500 events/s for 3s

> **Skipped**: 2 tests from `test_cluster_e2e.py` require a real MySQL + `pymysql` cursor;
> incompatible with the current SQLite mock test environment. Cluster archiver batch writes
> + Bridge fallback pass (3 tests tested); reflector diagnosis is covered by `test_disease_coverage.py`.
> For full Cluster e2e: set `MING_MYSQL_URL`.

---

**Testing philosophy: Three-tier (smoke/full/perf), 440 full tests, 150K event stress test.**
