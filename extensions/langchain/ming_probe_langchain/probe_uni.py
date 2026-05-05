# probe_uni.py —— v0.11.9m 乾坤镜探针（包内副本）
# 完整源码见 https://github.com/mingjing-probe/ming

import os, json, hashlib, time, sys, shutil, threading, atexit, re
from pathlib import Path

_SYSTEM_NAME_RE = re.compile(r"^[\w.\-]+$")

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False


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
    MAX_PAYLOAD_SIZE = 1024 * 1024

    LAMPORT_FILE = Path.home() / ".ming" / ".lamport_clock"

    REQUIRED_LAYERS = {
        "llm_invoke": ["layer_agent", "layer_llm", "layer_network"],
        "tool_call": ["layer_agent", "layer_tool", "layer_network"],
        "memory_retrieve": ["layer_agent", "layer_memory"],
        "agent_step": ["layer_agent"],
        "error": ["layer_agent"],
    }

    def __init__(self, system="python", mode="white"):
        if not _SYSTEM_NAME_RE.match(system):
            raise ValueError(f"system name contains invalid chars: {system}")
        self.system = system
        self.mode = mode
        self._hot_fd = None
        self._hot_path = None
        self._fallback_path = None
        self._buffer = []
        self._lock = threading.Lock()
        self._daemon_running = threading.Event()
        self._lamport_clock = 0
        atexit.register(self._flush_and_close)

    def _get_ts(self):
        return (
            time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
            + f".{int(time.time() * 1000) % 1000:03d}Z"
        )

    def _next_lamport(self):
        now_ms = int(time.time() * 1000)
        with self._lock:
            self._lamport_clock = max(self._lamport_clock + 1, now_ms)
            return self._lamport_clock

    def _ensure_hot_dir(self):
        self.HOT_DIR.mkdir(parents=True, exist_ok=True)

    def _hot_file_path(self):
        return self.HOT_DIR / f"hot_{self.system}.jsonl"

    def _fallback_path_func(self):
        self.FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        return self.FALLBACK_DIR / f"hot_{self.system}.jsonl"

    def _open_hot_fd(self):
        path = self._hot_file_path()
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            if _HAS_FCNTL:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd, path
        except (OSError, BlockingIOError):
            return None, path

    def emit(self, event_type, payload):
        ts = self._get_ts()
        lamport = self._next_lamport()
        event_id = hashlib.sha256(
            f"{ts}{lamport}{event_type}{json.dumps(payload, sort_keys=True, default=str)}".encode()
        ).hexdigest()[:16]
        record = {
            "schema_version": self.SCHEMA_VERSION,
            "event_id": event_id,
            "event_type": event_type,
            "system": self.system,
            "timestamp": ts,
            "lamport_clock": lamport,
            "mode": self.mode,
            "payload": payload,
        }
        self._enqueue(record)

    def _enqueue(self, record):
        with self._lock:
            self._buffer.append(record)
            if len(self._buffer) >= self.BATCH_SIZE:
                batch = self._buffer[:]
                self._buffer.clear()
        self._write_batch(batch)

    def _write_batch(self, batch):
        if not batch:
            return
        lines = (
            "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in batch)
            + "\n"
        )
        data = lines.encode("utf-8")
        fd, path = self._open_hot_fd()
        if fd is not None:
            try:
                os.write(fd, data)
                os.close(fd)
                return
            except OSError:
                try:
                    os.close(fd)
                except OSError:
                    pass
        fpath = self._fallback_path_func()
        try:
            with open(fpath, "ab") as f:
                f.write(data)
        except OSError:
            pass

    def _flush_and_close(self):
        with self._lock:
            batch = self._buffer[:]
            self._buffer.clear()
        self._write_batch(batch)
