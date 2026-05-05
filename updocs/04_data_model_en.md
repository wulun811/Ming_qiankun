# 04 Data Model

> **Sole Writer**: Only the Archiver writes to the persistence layer.

---

## I. SQLite Table Structure

### 1.1 events Table (14 columns)

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER | Auto-increment primary key |
| `system` | TEXT | System name |
| `event_type` | TEXT | Event type |
| `payload` | JSON | Event payload |
| `content_hash` | TEXT | Content hash |
| `prev_hash` | TEXT | Previous record hash |
| `curr_hash` | TEXT | Current record hash |
| `timestamp` | REAL | Event timestamp |
| `integrity` | TEXT | pending/verified/corrupted/rebooted |
| `chain_status` | TEXT | linked/broken |
| `integrity_score` | REAL | 0.0-1.0 |
| `ttl_protected` | INTEGER | 0=cleanable, 1=protected |
| `monotonic_ms` | REAL | Monotonic increasing millisecond timestamp |
| `lamport` | INTEGER | Lamport clock value |

### 1.2 Other Tables

| Table | Purpose |
|-------|---------|
| `system_pid` | System process registration |
| `diagnoses_YYYY` | Diagnosis records (partitioned by year) |
| `expectations` | Expected event declarations |
| `probe_health` | Probe health metrics |

---

## II. Hash Chain Protocol

```
content = json.dumps({"system": ..., "event_type": ..., "payload": ..., "timestamp": ...}, sort_keys=True)
curr_hash = SHA-256(prev_hash + content)  # Full 64-char hex
```

---

## III. Integrity States

| State | Meaning |
|-------|---------|
| `pending` | Just archived, not verified |
| `verified` | Hash chain verified |
| `corrupted` | Hash chain broken |
| `rebooted` | Anchor event after chain restart |

---

## IV. Chain Restart Protocol

1. Broken records stay `integrity='corrupted'` (never modified)
2. Next valid record's `prev_hash` = `'chain_reboot_' + prev_curr_hash[:48]`
3. Archiver auto-inserts `__chain_reboot__` anchor event
4. New chain starts from anchor

---

## V. Data Retention Policy

| Data Type | Retention |
|-----------|-----------|
| hot/*.jsonl | Moved to cold/ after archive |
| cold/*.jsonl | Permanent (manual cleanup) |
| events table | 90-day TTL, diagnosis-referenced events protected |
| system_pid | Permanent |
| diagnoses_YYYY | Permanent |
| expectations | Cleaned when fulfilled=1 |
| probe_health | Last 30 days |

---

## VI. MySQL Table Structure (Cluster Mode)

Cluster mode uses MySQL `wq_events` table, fields aligned with SQLite events table, plus:

| Extra Field | Type | Description |
|-------------|------|-------------|
| `project_id` | VARCHAR(64) | Project ID (distributed multi-tenant) |
| `created_at` | DATETIME | Archive timestamp |

---

## VII. Integrity Scoring

Archiver computes integrity score on every flush:

| Condition | Score |
|-----------|-------|
| Hash chain continuous | 1.0 |
| Hash chain broken | 0.0 |
| Chain restart first record | 0.5 |
| Payload missing key fields | Degraded |

---

**Evidence chain philosophy: SHA-256 hash chain, integrity scoring, chain restart protocol.**
