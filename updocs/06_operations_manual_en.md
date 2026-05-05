# 06 Operations Manual

> **Zero-config runnable**: Default parameters work for most scenarios.

---

## I. Environment Variables

### 1.1 Archiver (Standalone)

| Variable | Default | Description |
|----------|---------|-------------|
| `WQ_ARCHIVER_FLUSH_SEC` | 1.0 | Archive poll interval (seconds) |
| `WQ_ARCHIVER_VACUUM_HOURS` | 24 | SQLite VACUUM interval (hours) |
| `MING_TRIAGE_INTERVAL_SEC` | 1800 | Triage cycle (seconds) |

### 1.2 Archiver (Cluster)

| Variable | Default | Description |
|----------|---------|-------------|
| `WQ_DB_HOST` | — | MySQL host |
| `WQ_DB_PORT` | 3306 | MySQL port |
| `WQ_DB_USER` | root | MySQL user |
| `WQ_DB_NAME` | ming | MySQL database name |
| `WQ_PROJECT_ID` | default | Project ID |

### 1.3 General Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MING_MODE` | standalone | Runtime mode |
| `MING_HOT_DIR` | ~/.ming/hot | Hot rail directory |
| `MING_OTEL_PORT` | 4319 | OTEL receiver port |

---

## II. Credential Vault (v0.11.8)

MySQL password and Tusunsun push token are managed by `credential_vault.py`.

| Credential Key | Environment Variable | Purpose |
|----------------|---------------------|---------|
| `mysql.password` | `MING_SECRET_MYSQL_PASSWORD` | MySQL connection password |
| `tusunsun.pushToken` | `MING_SECRET_TUSUNSUN_PUSHTOKEN` | P0 push auth token |

**Setup (choose one)**:

```bash
# Method 1: Environment variable
export MING_SECRET_MYSQL_PASSWORD="your_password"

# Method 2: ~/.ming/.secrets file (recommended)
echo "mysql.password=your_password" >> ~/.ming/.secrets
chmod 600 ~/.ming/.secrets
```

---

## III. Deployment Guide

### 3.1 Standalone Mode

```bash
MING_MODE=standalone python src/ming.py start
```

### 3.2 Cluster Mode

```bash
# Docker MySQL quick start
docker-compose up -d mysql

# Start archiver
MING_MODE=cluster \
WQ_DB_HOST=127.0.0.1 \
WQ_DB_PORT=3306 \
WQ_DB_USER=root \
WQ_DB_NAME=ming \
python src/ming.py start
```

### 3.3 OpenClaw Integration

```bash
# 1. Install probe plugin
cp -r extensions/openclaw ~/.openclaw/extensions/mingjing-probe
openclaw config set plugins.entries.mingjing-probe.enabled true

# 2. Start archiver + Web panel
python src/ming.py start --daemon
python src/plugins/web_dashboard/server.py --daemon --host 0.0.0.0 --port 18088

# 3. Auto-start on boot (recommended)
python src/ming.py service install
sudo systemctl enable ming-archiver ming-web
```

---

## IV. Monitoring

### 4.1 Archiver Health

```bash
ming health
ming self-check
cat ~/.ming/.archiver_heartbeat  # Timestamp should be recent
```

### 4.2 Web Panel

```bash
curl http://localhost:18088/api/data.json
curl http://localhost:18088/ming/health
```

### 4.3 Disk Capacity

```bash
ls -lh ~/.ming/hot/      # Hot rail file count and size
ls -lh ~/.ming/ming.db   # SQLite size
```

---

## V. Troubleshooting

| Issue | Check Method |
|-------|--------------|
| Archiver not running | `cat ~/.ming/.archiver_heartbeat` |
| No hot rail files | `ls ~/.ming/hot/` (trigger Agent session first) |
| Web panel empty | `curl http://localhost:18088/api/data.json` |
| Probe not enabled | `openclaw config get plugins.entries.mingjing-probe.enabled` |
| No events after Gateway start | Wait for log `[mingjing-probe] Probe runtime started` (2-4 min lazy load) |

---

## VI. Recommended Configurations

```bash
# Scenario 1: Default (1000 events/s)
MING_MODE=standalone python src/ming.py start

# Scenario 2: High throughput (5000 events/s)
WQ_ARCHIVER_FLUSH_SEC=0.5 \
MING_MODE=standalone python src/ming.py start

# Scenario 3: Power saving (200 events/s)
WQ_ARCHIVER_FLUSH_SEC=5.0 \
MING_MODE=standalone python src/ming.py start
```

---

## VII. Backup & Recovery

### 7.1 Backup

```bash
# Archiver auto-backup (daily)
ls ~/.ming/ming.db.bak

# Manual backup
cp ~/.ming/ming.db ~/.ming/ming.db.manual.bak
```

### 7.2 Recovery

```bash
# Stop archiver
python src/ming.py stop

# Restore database
cp ~/.ming/ming.db.bak ~/.ming/ming.db

# Restart archiver
python src/ming.py start
```

---

**Operations philosophy: Zero-config runnable, resident memory < 40MB, 0 LLM calls, 0 network outbound.**
