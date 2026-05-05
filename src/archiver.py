# archiver.py —— 0.11.9m 单线程归档器 + 病历校验 + 自动清理 + 分诊触发 + 异常恢复
# 职责：热轨扫描 → 元数据分离 → integrity_score → 四表写入 → TTL 清理 → 自动分诊
# 拆分：TTL→archiver_ttl / 备份→archiver_backup / 确认→archiver_confirm / Schema→archiver_schema / 分诊→archiver_triage / 评分→archiver_score
import os, json, time, sqlite3, hashlib, shutil, threading
from pathlib import Path

from archiver_ttl import purge_expired
from archiver_backup import run_backup
from archiver_confirm import apply_confirmations
from archiver_schema import init_schema, init_diagnoses_table
from archiver_triage import trigger_triage, run_diagnosis_async
from archiver_score import score_event
from archiver_util import (
    write_heartbeat,
    log_error as _log,
    run_vacuum as _vacuum,
    update_expectation as _update_exp,
)
from archiver_exclude import is_system_excluded


class Archiver:
    HOT = Path(os.getenv("MING_HOT_DIR", str(Path.home() / ".ming" / "hot")))
    COLD = Path.home() / ".ming" / "cold"
    STAGING = Path.home() / ".ming" / "staging"
    DB = Path.home() / ".ming" / "ming.db"
    HEARTBEAT = Path.home() / ".ming" / ".archiver_heartbeat"
    CONFIRMATIONS = Path.home() / ".ming" / ".confirmations.jsonl"
    TRIAGE_SNAPSHOT = Path.home() / ".ming" / "triage_snapshot.json"
    ERROR_LOG = Path.home() / ".ming" / ".archiver_errors.jsonl"
    FLUSH_INTERVAL = float(os.getenv("WQ_ARCHIVER_FLUSH_SEC", "1.0"))
    VACUUM_INTERVAL = float(os.getenv("WQ_ARCHIVER_VACUUM_HOURS", "24")) * 3600
    TRIAGE_INTERVAL = float(os.getenv("MING_TRIAGE_INTERVAL_SEC", "1800"))
    COLD_TTL_DAYS = int(os.getenv("MING_COLD_TTL_DAYS", "7"))

    def __init__(self, db_path=None, hot_dir=None, cold_dir=None):
        self.DB = Path(db_path) if db_path else self.DB
        self.HOT = Path(hot_dir) if hot_dir else self.HOT
        self.COLD = Path(cold_dir) if cold_dir else self.COLD
        self.STAGING = self.HOT.parent / "staging"
        self.DB.parent.mkdir(parents=True, exist_ok=True)
        self.HOT.mkdir(parents=True, exist_ok=True)
        self.COLD.mkdir(parents=True, exist_ok=True)
        self.STAGING.mkdir(parents=True, exist_ok=True)
        self.HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._alive = True
        self._daemon_thread = None
        self._last_vacuum = 0
        self._triage_running = threading.Event()
        self._last_backup = 0
        self._scan_hot_dir()

    def _open_db(self, read_only=False, timeout=5):
        conn = sqlite3.connect(str(self.DB))
        conn.execute(f"PRAGMA busy_timeout={timeout * 1000}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA cache_size = -2000")
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        return conn

    def _init_db(self):
        conn = self._open_db()
        try:
            init_schema(conn)
        finally:
            conn.close()

    def _scan_hot_dir(self):
        self._cleanup_stale_staging()

    def _cleanup_stale_staging(self):
        for st in sorted(self.STAGING.glob("*.jsonl")):
            try:
                dest = self.HOT / st.name
                if dest.exists():
                    st.unlink()
                else:
                    st.rename(dest)
            except Exception:
                pass

    def _diagnoses_table(self):
        return f"diagnoses_{time.strftime('%Y')}"

    def run_once(self) -> int:
        if not self._alive:
            return 0
        now_sec = time.strftime("%Y%m%d_%H%M%S")
        try:
            hot_files = sorted(
                [
                    f
                    for f in self.HOT.glob("*.jsonl")
                    if not f.name.endswith(f"_{now_sec}.jsonl")
                ],
                key=lambda p: p.stat().st_mtime,
            )
        except Exception as e:
            self._log_error(e)
            return 0
        if not hot_files:
            self._heartbeat()
            return 0
        staging_files = {}
        for filepath in hot_files:
            sys_name = filepath.stem.split("_")[0]
            if is_system_excluded(sys_name):
                try:
                    filepath.unlink()
                except (OSError, FileNotFoundError):
                    pass
                continue
            staging_path = self.STAGING / filepath.name
            if staging_path.exists():
                staging_path.unlink()
            try:
                filepath.rename(staging_path)
                staging_files[filepath.name] = staging_path
            except (OSError, FileNotFoundError):
                continue
        if not staging_files:
            self._heartbeat()
            return 0
        conn = self._open_db()
        try:
            cursor = conn.cursor()
            tbl = init_diagnoses_table(cursor)
            all_events = []
            metadata = {}
            expectations = []
            health_records = []
            diagnoses = []
            anchors = []
            for filepath in sorted(
                staging_files.values(), key=lambda p: p.stat().st_mtime
            ):
                try:
                    lines = filepath.read_text(encoding="utf-8").strip().splitlines()
                except Exception:
                    continue
                for line in lines:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    etype = ev.get("event_type", "")
                    system = ev.get("system", "")
                    if not system and etype not in ("__expect__", "__fulfill__"):
                        continue
                    if etype == "__expect__":
                        expectations.append(ev)
                    elif etype == "__fulfill__":
                        self._update_expectation(
                            cursor, system, ev.get("payload", {}).get("expected_event")
                        )
                    elif etype == "__health__":
                        health_records.append(ev)
                        ev["integrity_score"] = 1.0
                        ev["integrity"] = "verified"
                        all_events.append(ev)
                    elif etype == "__diagnosis__":
                        diagnoses.append(ev)
                    elif etype == "__anchor__":
                        anchors.append(ev)
                    elif etype.startswith("__"):
                        if etype == "__register__":
                            metadata[system] = {
                                "register": ev.get("payload", {}),
                                "last_touch": ev.get("timestamp", 0),
                            }
                        elif etype == "__touch__":
                            ts = ev.get("timestamp", 0)
                            ev_pid = ev.get("pid") or ev.get("payload", {}).get(
                                "pid", 0
                            )
                            if system in metadata:
                                metadata[system]["last_touch"] = ts
                                if (
                                    "pid" not in metadata[system]
                                    or metadata[system]["pid"] == 0
                                ):
                                    metadata[system]["touch_pid"] = ev_pid
                            else:
                                metadata[system] = {
                                    "last_touch": ts,
                                    "touch_pid": ev_pid,
                                }
                    else:
                        score = self._integrity_score(ev)
                        ev["integrity_score"] = score
                        if score >= 0.85:
                            ev["integrity"] = "verified"
                        elif score >= 0.5:
                            ev["integrity"] = "rebooted"
                        else:
                            ev["integrity"] = "corrupted"
                        all_events.append(ev)
            if all_events:
                all_events.sort(
                    key=lambda x: (x.get("lamport", 0), x.get("monotonic_ms", 0))
                )
                last_hash = cursor.execute(
                    "SELECT curr_hash FROM events ORDER BY id DESC LIMIT 1"
                ).fetchone()
                prev = last_hash[0] if last_hash else "0" * 64
                batch = []
                for ev in all_events:
                    content = json.dumps(
                        {
                            "system": ev["system"],
                            "event_type": ev["event_type"],
                            "payload": ev.get("payload", {}),
                            "timestamp": ev.get("timestamp", 0),
                        },
                        sort_keys=True,
                    )
                    curr = hashlib.sha256(f"{prev}{content}".encode()).hexdigest()
                    batch.append(
                        (
                            ev["system"],
                            ev.get("mode", "white"),
                            ev["event_type"],
                            json.dumps(ev.get("payload", {})),
                            ev.get("content_hash", ""),
                            prev,
                            curr,
                            ev.get("timestamp", 0),
                            ev.get("integrity", "pending"),
                            ev.get("chain_status", "linked"),
                            ev.get("integrity_score", 1.0),
                            0,
                            ev.get("monotonic_ms"),
                            ev.get("lamport"),
                        )
                    )
                    prev = curr
                cursor.executemany(
                    "INSERT OR IGNORE INTO events (system, mode, event_type, payload, content_hash, prev_hash, curr_hash, timestamp, integrity, chain_status, integrity_score, ttl_protected, monotonic_ms, lamport) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    batch,
                )
            for system, meta in metadata.items():
                reg = meta.get("register")
                pid = reg.get("pid", 0) if reg else meta.get("touch_pid", 0)
                if not pid:
                    pid = 0
                registered_at = (
                    reg.get("registered_at", meta.get("last_touch", 0))
                    if reg
                    else meta.get("last_touch", 0)
                )
                last_seen = meta.get("last_touch", registered_at)
                mode = reg.get("mode", "white") if reg else "white"
                cursor.execute(
                    "INSERT OR REPLACE INTO system_pid (system, pid, registered_at, last_seen, mode) VALUES (?, ?, ?, ?, ?)",
                    (system, pid, registered_at, last_seen, mode),
                )
            for hr in health_records:
                p = hr.get("payload", {})
                cursor.execute(
                    "INSERT INTO probe_health (system, window_start, emit_count, drop_count, disk_free_mb, last_errors, integrity_score) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        hr["system"],
                        hr["timestamp"],
                        p.get("emit_success_count_1m", 0),
                        p.get("emit_drop_count_1m", 0),
                        p.get("disk_free_mb", -1),
                        json.dumps(p.get("last_errors", [])),
                        1.0,
                    ),
                )
            for exp in expectations:
                p = exp.get("payload", {})
                cursor.execute(
                    "INSERT INTO expectations (system, expected_event, deadline, fulfilled, created_at) VALUES (?, ?, ?, 0, ?)",
                    (
                        exp["system"],
                        p.get("expected_event"),
                        p.get("deadline"),
                        exp.get("timestamp", time.time()),
                    ),
                )
            dx_prev_hash = cursor.execute(
                f"SELECT curr_hash FROM {tbl} ORDER BY id DESC LIMIT 1"
            ).fetchone()
            dx_prev = dx_prev_hash[0] if dx_prev_hash else "0" * 64
            for dx in diagnoses:
                evidence_json = json.dumps(dx.get("evidence", []), sort_keys=True)
                dx_content = json.dumps(
                    {
                        "diagnosis_id": dx.get("diagnosis_id"),
                        "system": dx.get("system"),
                        "name": dx.get("diagnosis_name"),
                        "evidence_hash": dx.get("evidence_hash", ""),
                    },
                    sort_keys=True,
                )
                dx_curr = hashlib.sha256(f"{dx_prev}{dx_content}".encode()).hexdigest()
                evidence_hash_computed = hashlib.sha256(
                    evidence_json.encode()
                ).hexdigest()[:16]
                cursor.execute(
                    f"""
                    INSERT OR IGNORE INTO {tbl} (
                        diagnosis_id, system, fault_id, diagnosis_name, confidence,
                        severity, evidence, evidence_hash, evidence_quality,
                        inference_chain, plugin_name, plugin_version, status, created_at,
                        prev_hash, curr_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dx.get("diagnosis_id"),
                        dx.get("system"),
                        dx.get("fault_id"),
                        dx.get("diagnosis_name"),
                        dx.get("confidence"),
                        dx.get("severity"),
                        evidence_json,
                        evidence_hash_computed,
                        dx.get("evidence_quality", 0),
                        dx.get("inference_chain"),
                        dx.get("plugin_name"),
                        dx.get("plugin_version"),
                        dx.get("status", "pending"),
                        dx.get("timestamp", time.time()),
                        dx_prev,
                        dx_curr,
                    ),
                )
                dx_prev = dx_curr
            for a in anchors:
                p = a.get("payload", {})
                cursor.execute(
                    "INSERT OR REPLACE INTO system_anchors (file_path, sha256, registered_at) VALUES (?, ?, ?)",
                    (
                        p.get("file_path", ""),
                        p.get("sha256", ""),
                        a.get("timestamp", time.time()),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        for staging_path in staging_files.values():
            try:
                dest = self.COLD / staging_path.name
                staging_path.rename(dest)
            except OSError:
                try:
                    shutil.move(str(staging_path), str(dest))
                except Exception as e:
                    self._log_error(e)
            except Exception as e:
                self._log_error(e)
        self._heartbeat()
        self._auto_backup()
        self._purge_expired_events()
        self._apply_confirmations()
        self._maybe_run_triage()
        self._maybe_cleanup_cold()
        if all_events:
            self._run_diagnosis()
        return len(all_events)

    def _integrity_score(self, event):
        return score_event(event)

    def _update_expectation(self, cursor, system, expected_event):
        _update_exp(cursor, system, expected_event)

    def _heartbeat(self):
        write_heartbeat(str(self.HEARTBEAT))

    def _log_error(self, error, consecutive_count=0):
        _log(str(self.ERROR_LOG), error, consecutive_count)

    def _apply_confirmations(self):
        tbl = self._diagnoses_table()
        conn = self._open_db()
        try:
            apply_confirmations(conn, tbl, str(self.CONFIRMATIONS))
        except Exception:
            pass
        finally:
            conn.close()

    def _maybe_run_triage(self):
        trigger_triage(
            self._triage_running,
            self.TRIAGE_SNAPSHOT,
            self.TRIAGE_INTERVAL,
            self._log_error,
        )

    def _run_diagnosis(self):
        run_diagnosis_async(self._log_error)

    def _auto_backup(self):
        self._last_backup = run_backup(str(self.DB), self._last_backup, self._log_error)

    def _purge_expired_events(self):
        try:
            tbl = self._diagnoses_table()
            conn = self._open_db()
            try:
                purge_expired(conn, tbl)
            finally:
                conn.close()
        except Exception as e:
            self._log_error(e)

    def _maybe_cleanup_cold(self):
        if self.COLD_TTL_DAYS <= 0:
            return
        now = time.time()
        if not hasattr(self, "_last_cold_cleanup"):
            self._last_cold_cleanup = 0
        if now - self._last_cold_cleanup < 3600:
            return
        cutoff = now - self.COLD_TTL_DAYS * 86400
        removed = 0
        for cf in sorted(self.COLD.glob("*.jsonl")):
            try:
                if cf.stat().st_mtime < cutoff:
                    cf.unlink()
                    removed += 1
            except Exception:
                pass
        self._last_cold_cleanup = now
        if removed:
            self._log_error(
                f"cold_cleanup: removed {removed} files older than {self.COLD_TTL_DAYS}d"
            )

    def vacuum(self):
        result = _vacuum(str(self.DB), self._last_vacuum, self.VACUUM_INTERVAL)
        if result is None:
            return 0
        self._last_vacuum = time.time()
        return result

    def start_daemon(self):
        self._alive = True
        self._consecutive_errors = 0
        self._max_consecutive_errors = 100

        def loop():
            while self._alive:
                try:
                    self.run_once()
                    self.vacuum()
                    self._consecutive_errors = 0
                except Exception as e:
                    self._consecutive_errors += 1
                    self._log_error(e, self._consecutive_errors)
                    if self._consecutive_errors >= self._max_consecutive_errors:
                        break
                    backoff = min(self._consecutive_errors * self.FLUSH_INTERVAL, 30)
                    time.sleep(backoff)
                    continue
                time.sleep(self.FLUSH_INTERVAL)

        self._daemon_thread = threading.Thread(target=loop, daemon=True)
        self._daemon_thread.start()

    def stop(self):
        self._alive = False
        if self._daemon_thread:
            self._daemon_thread.join(timeout=10)
