# cluster_archiver.py —— 0.11.9m 集群归档器：MySQL 批量写入 + 守护模式
# 职责：从热轨读取事件，批量写入 MySQL wq_events 表，失败逐条回退
# 新增：start_daemon/stop 守护模式、MySQL 心跳表、staging 中间态保护
# 依赖：pymysql, json, hashlib, pathlib, time, threading, shutil

import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from cluster_pool import SimplePool

logger = logging.getLogger(__name__)


class ClusterArchiver:
    TABLE = "wq_events"
    HEARTBEAT_TABLE = "wq_heartbeat"
    BATCH_SIZE = 100
    FAILED_DIR = Path.home() / ".ming" / "_failed"

    def __init__(self, pool: SimplePool, project_id: str = None):
        self.pool = pool
        self.project_id = project_id or os.getenv("WQ_PROJECT_ID", "default")
        self.hot_dir = Path(
            os.getenv("MING_HOT_DIR", str(Path.home() / ".ming" / "hot"))
        )
        self.staging_dir = self.hot_dir / "staging"
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.FAILED_DIR.mkdir(parents=True, exist_ok=True)
        self._alive = False
        self._daemon_thread = None
        self._buffer = []
        self._lock = threading.Lock()
        self._max_buffer = int(os.getenv("WQ_MAX_BUFFER_SIZE", "10000"))
        self.flush_interval = float(os.getenv("WQ_ARCHIVER_FLUSH_SEC", "5"))
        self._last_flush = time.time()

    def start_daemon(self):
        """启动守护模式：定时 tick + MySQL 心跳"""
        self._alive = True
        self._daemon_thread = threading.Thread(target=self._daemon_loop, daemon=True)
        self._daemon_thread.start()
        self._heartbeat()

    def stop(self):
        """停止守护模式，刷盘残留事件"""
        self._alive = False
        if self._daemon_thread:
            self._daemon_thread.join(timeout=10)
        self._write_buffer()
        self._heartbeat()

    def _daemon_loop(self):
        """守护线程主循环"""
        try:
            while self._alive:
                try:
                    self.tick()
                except Exception as e:
                    logger.error(f"daemon error: {e}")
                time.sleep(self.flush_interval)
        except Exception as e:
            logger.error(f"daemon loop fatal: {e}")

    def tick(self):
        """定时调用：读热轨 → 缓冲 → 批量写入 → 心跳"""
        self._replay_failed()
        new_events = self._read_hot_files()
        with self._lock:
            self._buffer.extend(new_events)
            if len(self._buffer) > self._max_buffer:
                dropped = len(self._buffer) - self._max_buffer
                overflow = self._buffer[:dropped]
                self._buffer = self._buffer[dropped:]
                logger.warning(f"缓冲区溢出，丢弃 {dropped} 条最旧事件（保留最新）")
                for evt in overflow:
                    self._save_failed(evt)
        self._write_buffer()
        if self._alive:
            self._heartbeat()

    def _read_hot_files(self) -> list:
        """扫描热轨目录，读取待归档事件，移动到 staging/ 中间态"""
        events = []
        for f in sorted(self.hot_dir.glob("*.jsonl")):
            try:
                staging_path = self.staging_dir / f.name
                f.rename(staging_path)
                lines = staging_path.read_text(encoding="utf-8").strip().splitlines()
                for line in lines:
                    try:
                        ev = json.loads(line)
                        if not ev.get("event_type", "").startswith("__"):
                            ev["_staging_file"] = str(staging_path)
                            events.append(ev)
                    except json.JSONDecodeError:
                        continue
            except Exception:
                continue
        return events

    def _write_buffer(self):
        """批量写入缓冲事件，成功后删除 staging 文件"""
        with self._lock:
            if not self._buffer:
                return
            batch = self._buffer[: self.BATCH_SIZE]
        result = self.archive_batch(batch)
        if result["inserted"] > 0:
            inserted_files = set()
            with self._lock:
                for evt in batch[: result["inserted"]]:
                    sf = evt.get("_staging_file")
                    if sf:
                        inserted_files.add(sf)
                self._buffer = self._buffer[result["inserted"] :]
            self._cleanup_staging(inserted_files)
        elif result["failed"] > 0:
            # 所有事件都失败并保存到 _failed/，清理对应的 staging 文件
            failed_files = set()
            with self._lock:
                for evt in batch:
                    sf = evt.get("_staging_file")
                    if sf:
                        failed_files.add(sf)
                self._buffer = self._buffer[len(batch) :]
            self._cleanup_staging(failed_files)

    def _cleanup_staging(self, inserted_files: set):
        """只删除已成功写入的 staging 文件"""
        for filepath in inserted_files:
            try:
                Path(filepath).unlink()
            except Exception:
                pass

    def archive_batch(self, events: list) -> dict:
        if not events:
            return {"inserted": 0, "failed": 0, "fallback": 0}

        inserted, failed = 0, 0
        batch = events[: self.BATCH_SIZE]

        conn = self.pool.get()
        if not conn:
            return self._fallback_all(batch)

        try:
            with conn.cursor() as cur:
                sql = (
                    f"INSERT INTO {self.TABLE} "
                    "(project_id, `system`, mode, event_type, payload, prev_hash, curr_hash, `timestamp`, integrity, chain_status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                )
                rows = []
                prev_hash = self._get_last_hash(cur)

                for evt in batch:
                    curr_hash, payload = self._compute_hash(prev_hash, evt)
                    rows.append(
                        (
                            self.project_id,
                            evt.get("system", self.project_id),
                            evt.get("mode", "white"),
                            evt.get("event_type", "unknown"),
                            payload,
                            prev_hash,
                            curr_hash,
                            evt.get("timestamp", time.time()),
                            "pending",
                            "linked",
                        )
                    )
                    prev_hash = curr_hash

                cur.executemany(sql, rows)
                conn.commit()
                inserted = len(rows)
        except Exception:
            conn.rollback()
            for evt in batch:
                ok = self._insert_single(conn, evt)
                if ok:
                    inserted += 1
                else:
                    failed += 1
                    self._save_failed(evt)
        finally:
            self.pool.put(conn)

        return {"inserted": inserted, "failed": failed, "fallback": 0}

    def _insert_single(self, conn, evt: dict) -> bool:
        try:
            with conn.cursor() as cur:
                prev_hash = self._get_last_hash(cur)
                curr_hash, payload = self._compute_hash(prev_hash, evt)
                cur.execute(
                    f"INSERT INTO {self.TABLE} (project_id, `system`, mode, event_type, payload, prev_hash, curr_hash, `timestamp`, integrity, chain_status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        self.project_id,
                        evt.get("system", self.project_id),
                        evt.get("mode", "white"),
                        evt.get("event_type", "unknown"),
                        payload,
                        prev_hash,
                        curr_hash,
                        evt.get("timestamp", time.time()),
                        "pending",
                        "linked",
                    ),
                )
                conn.commit()
                return True
        except Exception:
            conn.rollback()
            return False

    def _compute_hash(self, prev_hash: str, evt: dict) -> tuple:
        """P2-07: 统一哈希计算，返回 (curr_hash, payload_json)"""
        payload = json.dumps(evt.get("payload", {}), sort_keys=True)
        content = json.dumps(
            {
                "system": evt.get("system", self.project_id),
                "event_type": evt.get("event_type", "unknown"),
                "payload": payload,
                "timestamp": evt.get("timestamp", time.time()),
            },
            sort_keys=True,
        )
        return hashlib.sha256(f"{prev_hash}{content}".encode()).hexdigest(), payload

    def _get_last_hash(self, cur) -> str:
        cur.execute(
            f"SELECT curr_hash FROM {self.TABLE} WHERE project_id = %s ORDER BY id DESC LIMIT 1",
            (self.project_id,),
        )
        row = cur.fetchone()
        return row[0] if row else "0" * 64

    def _fallback_all(self, events: list) -> dict:
        for evt in events:
            self._save_failed(evt)
        return {"inserted": 0, "failed": len(events), "fallback": len(events)}

    def _save_failed(self, evt: dict):
        self.FAILED_DIR.mkdir(parents=True, exist_ok=True)
        ts = int(time.time() * 1000)
        uid = uuid.uuid4().hex[:8]
        filepath = (
            self.FAILED_DIR / f"failed_{ts}_{evt.get('system', 'unknown')}_{uid}.json"
        )
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(evt, f, ensure_ascii=False)

    def _replay_failed(self):
        """尝试重放 _failed 目录中的事件"""
        if not self.FAILED_DIR.is_dir():
            return
        failed_files = sorted(self.FAILED_DIR.glob("failed_*.json"))
        if not failed_files:
            return
        replayed = 0
        for fp in failed_files:
            try:
                evt = json.loads(fp.read_text(encoding="utf-8"))
                with self._lock:
                    self._buffer.append(evt)
                fp.unlink()
                replayed += 1
            except Exception:
                continue
        if replayed:
            logger.info(f"重放 {replayed} 条失败事件")

    def _heartbeat(self):
        """写入 MySQL 心跳表，供 Watchdog 查询"""
        conn = self.pool.get()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {self.HEARTBEAT_TABLE} (project_id, archiver_pid, last_beat, mode, buffer_count) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON DUPLICATE KEY UPDATE last_beat=VALUES(last_beat), buffer_count=VALUES(buffer_count)",
                    (
                        self.project_id,
                        os.getpid(),
                        time.time(),
                        "white",
                        len(self._buffer),
                    ),
                )
                conn.commit()
        except Exception:
            pass
        finally:
            self.pool.put(conn)
