# archiver.py —— 0.11.9m 单线程归档器 + 病历校验 + 自动清理 + 分诊触发 + 异常恢复
# 职责：热轨扫描 → 元数据分离 → integrity_score → 四表写入 → TTL 清理 → 自动分诊
# 拆分：TTL→archiver_ttl / 备份→archiver_backup / 确认→archiver_confirm / Schema→archiver_schema / 分诊→archiver_triage / 评分→archiver_score
import os, json, time, sqlite3, hashlib, shutil, threading, gc, ctypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

from archiver_schema import init_schema, init_diagnoses_table, check_wal_integrity
from archiver_score import score_event
from archiver_util import (
    write_heartbeat,
    log_error as _log,
    run_vacuum as _vacuum,
    update_expectation as _update_exp,
)
from archiver_exclude import is_system_excluded
from archiver_compress import (
    compress_payload,
    verify_compress_integrity,
    MIN_COMPRESS_BYTES,
)

try:
    from archiver_ttl import purge_expired
except ImportError:
    purge_expired = None
try:
    from archiver_backup import run_backup
except ImportError:
    run_backup = None
try:
    from archiver_confirm import apply_confirmations
except ImportError:
    apply_confirmations = None
try:
    from archiver_triage import trigger_triage, run_diagnosis_async
except ImportError:
    trigger_triage = None
    run_diagnosis_async = None
try:
    from archiver_summary import summarize_month
except ImportError:
    summarize_month = None


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
    CHECKPOINT_INTERVAL = float(os.getenv("MING_CHECKPOINT_INTERVAL_SEC", "300"))
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
        self._last_checkpoint = 0
        self._triage_running = threading.Event()
        self._last_backup = 0
        self._startup_time = time.time()
        self._trim_counter = 0
        self._idle_cycles = 0
        self.warm_days = 14
        self.min_compress_bytes = MIN_COMPRESS_BYTES
        self._last_compress = 0
        self._last_summary_check = 0
        # 缓存 ctypes.CDLL，避免每次调用都重新创建
        try:
            self._libc = ctypes.CDLL("libc.so.6")
        except OSError:
            self._libc = None  # musl libc (Alpine/Docker) or non-Linux
        self._scan_hot_dir()
        self._conn = None  # 持久连接，在 daemon 线程中惰性创建

    def _open_db(self, read_only=False, timeout=5):
        conn = sqlite3.connect(str(self.DB))
        conn.execute(f"PRAGMA busy_timeout={timeout * 1000}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA cache_size = -2000")
        conn.execute("PRAGMA mmap_size = 8388608")
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        return conn

    def _init_db(self):
        # P0-NEW-3: 启动时检查 WAL/SHM 完整性
        if not check_wal_integrity(str(self.DB)):
            # 主数据库也损坏了，需要重建
            self._log_error("数据库损坏，尝试重建")
            try:
                self.DB.unlink(missing_ok=True)
                self.DB.with_suffix(".db-wal").unlink(missing_ok=True)
                self.DB.with_suffix(".db-shm").unlink(missing_ok=True)
            except OSError as e:
                self._log_error(f"删除损坏数据库失败: {e}")

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
        if self._conn is None:
            self._conn = self._open_db()
        conn = self._conn
        got_events = False
        total_events = 0
        try:
            cursor = conn.cursor()
            tbl = init_diagnoses_table(cursor)
            for staging_path in sorted(
                staging_files.values(), key=lambda p: p.stat().st_mtime
            ):
                file_events = self._process_one_file(cursor, tbl, staging_path)
                if file_events > 0:
                    total_events += file_events
                    got_events = True
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        conn.rollback()
        self._heartbeat()
        self._auto_backup()
        if purge_expired:
            self._purge_expired_events(conn)
            conn.rollback()
        if apply_confirmations:
            self._apply_confirmations(conn)
            conn.rollback()
        self._maybe_run_triage()
        self._maybe_cleanup_cold()
        self._compress_old_events()
        self._summarize_old_events()
        if got_events:
            self._run_diagnosis()
        gc.collect()
        if self._libc:
            try:
                self._libc.malloc_trim(0)
            except Exception:
                pass
        return total_events

    def _integrity_score(self, event):
        return score_event(event)

    def _update_expectation(self, cursor, system, expected_event):
        _update_exp(cursor, system, expected_event)

    def _process_one_file(self, cursor, tbl, staging_path):
        try:
            lines = staging_path.read_text(encoding="utf-8").strip().splitlines()
        except Exception:
            return 0
        all_events = []
        metadata = {}
        expectations = []
        health_records = []
        diagnoses = []
        anchors = []
        for line in lines:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = ev.get("event_type", "")
            system = ev.get("system", "")
            if not isinstance(system, str):
                continue  # P0: system 必须是字符串，防止 dict 用作 dict key
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
                    ev_pid = ev.get("pid") or ev.get("payload", {}).get("pid", 0)
                    if system in metadata:
                        metadata[system]["last_touch"] = ts
                        if (
                            "pid" not in metadata[system]
                            or metadata[system]["pid"] == 0
                        ):
                            metadata[system]["touch_pid"] = ev_pid
                        if "mode" not in metadata[system]:
                            metadata[system]["mode"] = ev.get("mode", "white")
                    else:
                        metadata[system] = {
                            "last_touch": ts,
                            "touch_pid": ev_pid,
                            "mode": ev.get("mode", "white"),
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
                payload_dict = ev.get("payload", {})
                payload_text = json.dumps(
                    payload_dict, ensure_ascii=False, sort_keys=True
                )
                payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
                content = json.dumps(
                    {
                        "system": ev["system"],
                        "event_type": ev["event_type"],
                        "payload_hash": payload_hash,
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
                        json.dumps(payload_dict),
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
                        payload_hash,
                    )
                )
                prev = curr
            cursor.executemany(
                "INSERT OR IGNORE INTO events (system, mode, event_type, payload, content_hash, prev_hash, curr_hash, timestamp, integrity, chain_status, integrity_score, ttl_protected, monotonic_ms, lamport, payload_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            mode = reg.get("mode", "white") if reg else meta.get("mode", "white")
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
            evidence_hash_computed = hashlib.sha256(evidence_json.encode()).hexdigest()[
                :16
            ]
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
        return len(all_events)

    def _heartbeat(self):
        write_heartbeat(str(self.HEARTBEAT))

    def _log_error(self, error, consecutive_count=0):
        _log(str(self.ERROR_LOG), error, consecutive_count)

    def _apply_confirmations(self, conn=None):
        tbl = self._diagnoses_table()
        own = False
        if conn is None:
            conn = self._open_db()
            own = True
        try:
            apply_confirmations(conn, tbl, str(self.CONFIRMATIONS))
        except Exception:
            pass
        finally:
            if own:
                conn.close()

    def _maybe_run_triage(self):
        if trigger_triage:
            trigger_triage(
                self._triage_running,
                self.TRIAGE_SNAPSHOT,
                self.TRIAGE_INTERVAL,
                self._log_error,
            )

    def _run_diagnosis(self):
        if run_diagnosis_async:
            run_diagnosis_async(self._log_error)

    def _auto_backup(self):
        if not run_backup or time.time() - self._startup_time < 3600:
            return
        old_last = self._last_backup
        self._last_backup = run_backup(str(self.DB), self._last_backup, self._log_error)
        if self._last_backup != old_last:
            gc.collect()
            if self._libc:
                try:
                    self._libc.malloc_trim(0)
                except Exception:
                    pass

    def _purge_expired_events(self, conn=None):
        try:
            tbl = self._diagnoses_table()
            own = False
            if conn is None:
                conn = self._open_db()
                own = True
            try:
                purge_expired(conn, tbl)
            finally:
                if own:
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
        try:
            with os.scandir(str(self.COLD)) as it:
                entries = [
                    (e.name, e.stat().st_mtime)
                    for e in it
                    if e.name.endswith(".jsonl") and e.is_file()
                ]
            entries.sort(key=lambda x: x[1])
            for name, mtime in entries:
                if mtime < cutoff:
                    try:
                        (self.COLD / name).unlink()
                        removed += 1
                    except Exception:
                        pass
        except Exception:
            pass
        self._last_cold_cleanup = now
        if removed:
            self._log_error(
                f"cold_cleanup: removed {removed} files older than {self.COLD_TTL_DAYS}d"
            )

    def _summarize_old_events(self):
        if not summarize_month:
            return
        now = time.time()
        if now - self._last_summary_check < 86400:
            return
        self._last_summary_check = now
        dt = datetime.now(timezone.utc)
        if dt.day != 1:
            return
        prev = dt.replace(day=1) - timedelta(days=1)
        year_month = prev.strftime("%Y_%m")
        try:
            conn = self._open_db()
            try:
                result = summarize_month(conn, year_month)
                if result and "error" in result:
                    self._log_error(f"monthly summary error: {result['error']}")
                elif result and "skipped" not in result:
                    self._log_error(
                        f"monthly summary: {year_month} aggregated "
                        f"{result['total_events']} events across {len(result['systems'])} systems"
                    )
            finally:
                conn.close()
        except Exception as e:
            self._log_error(f"monthly summary error: {e}")

    def _check_disk_space(self):
        free_mb = shutil.disk_usage(str(self.DB.parent)).free / (1024 * 1024)
        db_size_mb = os.path.getsize(self.DB) / (1024 * 1024)
        if free_mb < db_size_mb * 1.5:
            self._log_error(
                f"Insufficient disk space: free={free_mb:.0f}MB, db={db_size_mb:.0f}MB, need 1.5x"
            )
            return False
        return True

    def _commit_batch(self, conn, batch):
        if not batch:
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            for blob, ev_id in batch:
                conn.execute(
                    "INSERT INTO events_blob (event_id, payload_blob) VALUES (?, ?) "
                    "ON CONFLICT(event_id) DO UPDATE SET payload_blob = excluded.payload_blob",
                    (ev_id, blob),
                )
                conn.execute(
                    "UPDATE events SET payload = NULL, storage_tier = 1, compress_attempts = 0 WHERE id = ?",
                    (ev_id,),
                )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass  # ROLLBACK 失败时不遮蔽原始异常
            raise

    def _compress_old_events(self):
        now = time.time()
        if now - self._last_compress < 3600:
            return
        self._last_compress = now
        if not self._check_disk_space():
            return
        try:
            conn = self._open_db()
            try:
                cutoff = now - self.warm_days * 86400
                scanned = 0
                success = 0
                fail = 0
                start_ts = time.time()
                batch = []
                offset = 0
                batch_size = 500
                while True:
                    rows = conn.execute(
                        """
                        SELECT e.id, e.payload, e.payload_hash
                        FROM events e
                        WHERE e.storage_tier = 0 AND e.timestamp < ?
                          AND e.ttl_protected = 0
                          AND e.payload IS NOT NULL
                          AND LENGTH(e.payload) >= ?
                        LIMIT ? OFFSET ?
                        """,
                        (cutoff, self.min_compress_bytes, batch_size, offset),
                    ).fetchall()
                    if not rows:
                        break
                    scanned += len(rows)
                    for ev_id, payload_text, payload_hash in rows:
                        try:
                            payload_dict = json.loads(payload_text)
                            blob = compress_payload(payload_dict)
                            if not verify_compress_integrity(payload_dict, blob):
                                raise ValueError("Compression corrupted payload")
                            batch.append((blob, ev_id))
                            success += 1
                        except Exception:
                            attempts = conn.execute(
                                "UPDATE events SET compress_attempts = compress_attempts + 1 WHERE id = ? RETURNING compress_attempts",
                                (ev_id,),
                            ).fetchone()[0]
                            if attempts >= 3:
                                conn.execute(
                                    "UPDATE events SET storage_tier = 2 WHERE id = ?",
                                    (ev_id,),
                                )
                                self._log_error(
                                    f"Compression failed after 3 attempts for event {ev_id}, marked as failed"
                                )
                            fail += 1
                        if len(batch) >= 500:
                            self._commit_batch(conn, batch)
                            batch = []
                            gc.collect()
                            if self._libc:
                                try:
                                    self._libc.malloc_trim(0)
                                except Exception:
                                    pass
                    if batch:
                        self._commit_batch(conn, batch)
                        batch = []
                    offset += batch_size
                if scanned > 0:
                    elapsed = (time.time() - start_ts) * 1000
                    self._log_error(
                        f"compress: scanned={scanned} compressed={success} failed={fail} "
                        f"elapsed={elapsed:.0f}ms"
                    )
            finally:
                conn.close()
        except Exception as e:
            self._log_error(f"compress error: {e}")

    def vacuum(self):
        result = _vacuum(str(self.DB), self._last_vacuum, self.VACUUM_INTERVAL)
        if result is None:
            return 0
        self._last_vacuum = time.time()
        return result

    def checkpoint(self):
        now = time.time()
        if now - self._last_checkpoint < self.CHECKPOINT_INTERVAL:
            return 0
        try:
            conn = self._open_db()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.close()
            self._last_checkpoint = now
            wal = self.DB.with_suffix(".db-wal")
            if wal.exists():
                return wal.stat().st_size
            return 0
        except Exception:
            return 0

    def start_daemon(self):
        self._alive = True
        self._consecutive_errors = 0
        self._max_consecutive_errors = 100
        try:
            conn = self._open_db()
            conn.execute(
                "INSERT OR REPLACE INTO system_pid (system, pid, registered_at, last_seen, mode) VALUES (?, ?, ?, ?, ?)",
                ("mingjing", os.getpid(), time.time(), time.time(), "white"),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

        try:
            _libc = ctypes.CDLL("libc.so.6")
        except OSError:
            _libc = None  # musl libc (Alpine/Docker) or non-Linux

        def loop():
            while self._alive:
                try:
                    events = self.run_once()
                    self.checkpoint()
                    if events > 0:
                        self._idle_cycles = 0
                    else:
                        self._idle_cycles += 1
                    self.vacuum()
                    self._consecutive_errors = 0
                except Exception as e:
                    self._consecutive_errors += 1
                    self._log_error(e, self._consecutive_errors)
                    if self._consecutive_errors >= self._max_consecutive_errors:
                        self._log_error(
                            Exception(
                                f"连续错误达 {self._max_consecutive_errors} 次，daemon 线程退出"
                            ),
                            0,
                        )
                        self._alive = False
                        break
                    backoff = min(self._consecutive_errors * self.FLUSH_INTERVAL, 30)
                    time.sleep(backoff)
                    continue
                self._trim_counter += 1
                if self._trim_counter >= 60:
                    self._trim_counter = 0
                    gc.collect()
                    if _libc:
                        _libc.malloc_trim(0)
                    self._cleanup_stale_staging()
                idle_sleep = min(self._idle_cycles * 0.5, 10.0) if self._idle_cycles > 10 else self.FLUSH_INTERVAL
                time.sleep(idle_sleep)
            self._alive = False

        self._daemon_thread = threading.Thread(target=loop, daemon=True)
        self._daemon_thread.start()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def close(self):
        conn = getattr(self, '_conn', None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        self._conn = None

    def stop(self):
        self._alive = False
        if self._daemon_thread:
            self._daemon_thread.join(timeout=10)
        self.close()
