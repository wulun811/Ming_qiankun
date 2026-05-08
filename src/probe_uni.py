# probe_uni.py —— v0.11.9m 乾坤镜探针
# 职责：纯粹的无状态热轨写入器，零数据库依赖
# 依赖：标准库 only
# 禁止：import sqlite3, import requests, import threading（除 daemon 外）

import os, json, hashlib, time, sys, shutil, threading, atexit, re
from pathlib import Path

_SYSTEM_NAME_RE = re.compile(r"^[\w.\-]+$")

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False  # Windows 不支持 fcntl


class ProbeUni:
    HOT_DIR = Path(os.getenv("MING_HOT_DIR", str(Path.home() / ".ming" / "hot")))
    FALLBACK_DIR = (
        Path(os.getenv("TEMP", os.getenv("TMPDIR", str(Path.home() / "tmp"))))
        / "ming_fallback"
    )
    DISK_MIN_MB = 500
    SCHEMA_VERSION = "0.11.9m"
    BATCH_SIZE = 10
    FLUSH_INTERVAL_MS = 200
    DISK_CHECK_INTERVAL_S = 5
    MAX_BATCH_SIZE = 1000
    _attached_pids: set = set()
    MAX_PAYLOAD_SIZE = 1024 * 1024  # 1MB

    # 多进程共享 Lamport 时钟文件
    LAMPORT_FILE = Path.home() / ".ming" / ".lamport_clock"

    REQUIRED_LAYERS = {
        "llm_invoke": ["layer_agent", "layer_llm", "layer_network"],
        "tool_call": ["layer_agent", "layer_tool", "layer_network"],
        "memory_retrieve": ["layer_agent", "layer_memory"],
        "agent_step": ["layer_agent"],
        "error": ["layer_agent"],
        "plugin.init_failed": ["layer_system"],
        "circuit.state_change": ["layer_system"],
        "db.flush_error": ["layer_system"],
        "db.mode_degraded": ["layer_system"],
        "memory.readonly_entered": ["layer_system"],
        "push.action_failed": ["layer_system"],
        "http.error": ["layer_network"],
    }

    def __init__(self, system: str, mode: str = "white", pid: int = None):
        if not system or not _SYSTEM_NAME_RE.match(system):
            raise ValueError(
                f"无效的系统名: {system!r}（仅允许字母、数字、点、下划线、连字符）"
            )
        self.system = system
        self.mode = mode
        self.pid = pid or os.getpid()
        self._batch = []
        self._lock = threading.Lock()
        self._last_flush = time.time()
        self._last_disk_check = 0
        self._disk_free_mb = 9999
        self._self_errors = []
        self._last_wall_time = 0
        self._last_mono_ms = 0
        self._file_seq = 0
        self._init_hot_dir()
        self._init_lamport()

        # 黑盒模式自动降级标记
        if self.mode == "black":
            self._base_integrity = 0.6
        else:
            self._base_integrity = 1.0

        # P2-22: 注册退出刷盘，确保最后批次不丢失
        atexit.register(self._flush_on_exit)

        self.emit(
            "__register__",
            {
                "pid": self.pid,
                "mode": self.mode,
                "schema_version": self.SCHEMA_VERSION,
                "registered_at": time.time(),
                "_base_integrity": self._base_integrity,
            },
        )

    def _init_hot_dir(self):
        try:
            self.HOT_DIR.mkdir(parents=True, exist_ok=True)
            test = self.HOT_DIR / ".probe_write_test"
            test.write_text("1")
            test.unlink()
        except (OSError, PermissionError):
            self.HOT_DIR = self.FALLBACK_DIR
            self.HOT_DIR.mkdir(parents=True, exist_ok=True)
            self._record_error("hot_dir_fallback_to_tmp")

    def _init_lamport(self):
        try:
            if not self.LAMPORT_FILE.exists():
                self.LAMPORT_FILE.write_text("0")
        except Exception:
            pass

    def _next_lamport_batch(self, n: int) -> int:
        """原子获取 n 个 Lamport 序号，返回起始值"""
        if not _HAS_FCNTL:
            # P3-4: Windows 添加进程 ID 到排序键避免重复
            return int(time.time() * 1000) + (self.pid % 10000)
        try:
            with open(self.LAMPORT_FILE, "r+") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    start = int(f.read().strip() or "0")
                    end = start + n
                    f.seek(0)
                    f.write(str(end))
                    f.truncate()
                    return start + 1
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            return int(time.time() * 1000)

    def _record_error(self, msg: str):
        """P2-23: _self_errors 上限 100，防止无界增长"""
        self._self_errors.append(msg)
        if len(self._self_errors) > 100:
            self._self_errors = self._self_errors[-100:]

    def _flush_on_exit(self):
        """P2-22: atexit 刷盘，确保退出前最后批次不丢失"""
        try:
            with self._lock:
                if self._batch:
                    self._flush_batch()
        except Exception:
            pass

    def _validate_layers(self, event_type: str, payload: dict):
        missing = [
            l for l in self.REQUIRED_LAYERS.get(event_type, []) if l not in payload
        ]
        if missing:
            payload["_incomplete"] = missing
            payload["_integrity_hint"] = "partial"

    def emit(self, event_type: str, payload: dict):
        # 暂停检查：~/.ming/.paused/{system} 存在则静默
        paused_file = Path.home() / ".ming" / ".paused" / self.system
        if paused_file.exists():
            return False

        # P2-2: payload 大小限制
        try:
            payload_str = json.dumps(payload, ensure_ascii=False)
            if len(payload_str.encode("utf-8")) > self.MAX_PAYLOAD_SIZE:
                self._record_error(f"Payload too large: {len(payload_str)} bytes")
                return False
        except (TypeError, ValueError) as e:
            self._record_error(f"Payload serialization failed: {e}")
            return False

        self._validate_layers(event_type, payload)

        now_wall = time.time()
        now_mono = time.monotonic() * 1000

        # 时钟跳变检测
        if self._last_wall_time > 0:
            wall_delta = now_wall - self._last_wall_time
            mono_delta = (now_mono - self._last_mono_ms) / 1000
            if abs(wall_delta - mono_delta) > 5:
                payload["_clock_skew"] = True
                payload["_time_reliable"] = False
            else:
                payload["_time_reliable"] = True

        self._last_wall_time = now_wall
        self._last_mono_ms = now_mono

        # 黑盒模式标记
        if self.mode == "black":
            payload["_base_integrity"] = 0.6

        record = {
            "system": self.system,
            "mode": self.mode,
            "event_type": event_type,
            "payload": payload,
            "timestamp": now_wall,
            "monotonic_ms": now_mono,
            "_pid": self.pid,
            "_schema_version": self.SCHEMA_VERSION,
        }
        with self._lock:
            self._batch.append(record)
            should_flush = (
                len(self._batch) >= self.BATCH_SIZE
                or (time.time() - self._last_flush) * 1000 > self.FLUSH_INTERVAL_MS
            )

        if should_flush:
            self._flush_batch()

    def _flush_batch(self):
        with self._lock:
            now = time.time()
            if now - self._last_disk_check > self.DISK_CHECK_INTERVAL_S:
                try:
                    self._disk_free_mb = shutil.disk_usage(str(self.HOT_DIR)).free / (
                        1024 * 1024
                    )
                except Exception:
                    self._disk_free_mb = -1
                self._last_disk_check = now

            if self._disk_free_mb < self.DISK_MIN_MB:
                self._batch = [
                    ev
                    for ev in self._batch
                    if ev["event_type"].startswith("__") or ev["event_type"] == "error"
                ]
                if not self._batch:
                    return

            if not self._batch:
                return

            # P1-7: 序列化预过滤，防止不可序列化对象污染批次
            valid_batch = []
            for ev in self._batch:
                try:
                    json.dumps(ev, ensure_ascii=False)
                    valid_batch.append(ev)
                except (TypeError, ValueError) as e:
                    self._record_error(f"Dropped unserializable event: {e}")

            if not valid_batch:
                self._batch = []
                return

            self._batch = valid_batch
            batch_to_flush = self._batch[:]

        # 批量分配 Lamport（多进程同步）
        n = len(batch_to_flush)
        start = self._next_lamport_batch(n)
        for i, ev in enumerate(batch_to_flush):
            ev["lamport"] = start + i

        ts = time.strftime("%Y%m%d_%H%M%S")
        self._file_seq += 1
        filepath = (
            self.HOT_DIR / f"{self.system}_{ts}_{self.pid}_{self._file_seq:04d}.jsonl"
        )
        lines = [json.dumps(ev, ensure_ascii=False) + "\n" for ev in batch_to_flush]

        try:
            with open(filepath, "a", encoding="utf-8") as f:
                if _HAS_FCNTL:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except (OSError, IOError):
                        pass
                f.writelines(lines)
                if _HAS_FCNTL:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except (OSError, IOError):
                        pass
            with self._lock:
                # P1-10: 只在成功后清空，且检查批次未被替换
                if self._batch is batch_to_flush or self._batch == batch_to_flush:
                    self._batch = []
            self._last_flush = time.time()
        except OSError as e:
            self._record_error(str(e))
            print(f"[MING-RETRY] {e}", file=sys.stderr)
            # P0-3: 不清空 batch，下次重试
            with self._lock:
                if len(self._batch) > self.MAX_BATCH_SIZE:
                    dropped = len(self._batch) - self.MAX_BATCH_SIZE
                    self._batch = self._batch[-self.MAX_BATCH_SIZE :]
                    self._record_error(f"Batch capped: dropped {dropped} oldest events")

    def expect(self, event_type: str, within_seconds: int):
        self.emit(
            "__expect__",
            {
                "expected_event": event_type,
                "deadline": time.time() + within_seconds,
                "fulfilled": False,
            },
        )

    def fulfill(self, event_type: str):
        self.emit("__fulfill__", {"expected_event": event_type})

    def touch(self):
        self.emit("__touch__", {"pid": self.pid})

    def health(self):
        self.emit(
            "__health__",
            {
                "emit_success_count_1m": len(self._batch),
                "emit_drop_count_1m": 0,
                "last_errors": self._self_errors[-5:],
                "disk_free_mb": self._disk_free_mb,
                "buffer_queue_size": len(self._batch),
            },
        )

    @staticmethod
    def attach(pid: int, system_name: str = None):
        """黑盒模式：附加到已有进程，通过 /proc/{pid} 采集（Linux only）"""
        if pid in ProbeUni._attached_pids:
            return None
        ProbeUni._attached_pids.add(pid)
        probe = ProbeUni(system=system_name or f"pid_{pid}", mode="black", pid=pid)
        probe._start_polling(pid)
        return probe

    def _start_polling(self, pid: int, interval: int = 5):
        def loop():
            baseline = self._snapshot_io(pid)
            while True:
                time.sleep(interval)
                try:
                    current = self._snapshot_io(pid)
                    diff = self._io_diff(baseline, current)
                    for change in diff:
                        self.emit("io_anomaly", change)
                    baseline = current
                except (ProcessLookupError, OSError):
                    self.emit(
                        "system_crash", {"pid": pid, "inferred": "process_vanished"}
                    )
                    ProbeUni._attached_pids.discard(pid)
                    break

        threading.Thread(target=loop, daemon=True).start()

    def _snapshot_io(self, pid: int):
        base = f"/proc/{pid}"
        if not os.path.exists(base):
            return {}
        fds = {}
        fd_dir = f"{base}/fd"
        if os.path.isdir(fd_dir):
            for fd in os.listdir(fd_dir):
                try:
                    fds[fd] = os.readlink(f"{fd_dir}/{fd}")
                except (OSError, PermissionError):
                    continue
        return {"fds": fds}

    def _io_diff(self, old: dict, new: dict):
        changes = []
        old_fds = old.get("fds", {})
        new_fds = new.get("fds", {})
        for fd, target in new_fds.items():
            if fd not in old_fds:
                changes.append({"fd_type": "new", "io_target": target})
        return changes
