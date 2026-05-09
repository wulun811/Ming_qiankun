# watchdog.py —— 0.11.9m 被动看门狗
# 职责：检查归档器心跳 + 系统 PID 存活 + 自健康检测
# 不直接写 SQLite，合成事件写入热轨

import os, time, json, sqlite3
from pathlib import Path


class PassiveWatchdog:
    DB = Path.home() / ".ming" / "ming.db"
    HOT = Path.home() / ".ming" / "hot"
    HEARTBEAT = Path.home() / ".ming" / ".archiver_heartbeat"
    SILENCE_THRESHOLD = 120
    ARCHIVER_TIMEOUT = 60
    CHECK_INTERVAL = 30

    def __init__(self, webhook_fn=None, check_interval=30, heartbeat_timeout=60):
        self.webhook = webhook_fn or (lambda alert: None)
        self.CHECK_INTERVAL = check_interval
        self.ARCHIVER_TIMEOUT = heartbeat_timeout
        self._alive = False
        self._consecutive_failures = 0
        self._restart_attempts = 0
        self._max_restarts = 3

    def _check_archiver(self, project_id: str = None) -> dict:
        """检查归档器健康状态，Standalone 读文件，Cluster 读 MySQL 心跳表"""
        backend = os.getenv("ARCHIVER_BACKEND", "sqlite")
        if backend == "mysql":
            return self._check_archiver_mysql(project_id)
        return self._check_archiver_file()

    def _check_archiver_file(self) -> dict:
        """Standalone 模式：检查心跳文件"""
        if not self.HEARTBEAT.exists():
            self._consecutive_failures += 1
            status = "dead" if self._consecutive_failures >= 3 else "stale"
            return {
                "status": status,
                "consecutive_failures": self._consecutive_failures,
            }

        try:
            last = float(self.HEARTBEAT.read_text().strip())
            if time.time() - last > self.ARCHIVER_TIMEOUT:
                self._consecutive_failures += 1
                status = "dead" if self._consecutive_failures >= 3 else "stale"
                return {
                    "status": status,
                    "consecutive_failures": self._consecutive_failures,
                }

            self._consecutive_failures = 0
            return {"status": "alive", "consecutive_failures": 0}
        except (ValueError, OSError):
            self._consecutive_failures += 1
            status = "dead" if self._consecutive_failures >= 3 else "stale"
            return {
                "status": status,
                "consecutive_failures": self._consecutive_failures,
            }

    def _check_archiver_mysql(self, project_id: str = None) -> dict:
        """Cluster 模式：检查 MySQL 心跳表"""
        try:
            import pymysql
        except ImportError:
            return {
                "status": "unknown",
                "consecutive_failures": 0,
                "msg": "pymysql not available",
            }

        pid = project_id or os.getenv("WQ_PROJECT_ID", "default")
        host = os.getenv("WQ_MYSQL_HOST", "localhost")
        port = int(os.getenv("WQ_MYSQL_PORT", "3306"))
        user = os.getenv("WQ_MYSQL_USER", "wq")
        from credential_vault import get_secret

        password = get_secret("mysql.password") or None
        database = os.getenv("WQ_MYSQL_DATABASE", "ming")

        try:
            conn = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database,
                connect_timeout=5,
            )
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT project_id, archiver_pid, last_beat, buffer_count FROM wq_heartbeat WHERE project_id = %s",
                        (pid,),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()

            if not row:
                self._consecutive_failures += 1
                return {
                    "status": "stale",
                    "consecutive_failures": self._consecutive_failures,
                    "msg": "no heartbeat record",
                }

            last_beat = row[2]
            if time.time() - last_beat > self.ARCHIVER_TIMEOUT:
                self._consecutive_failures += 1
                status = "dead" if self._consecutive_failures >= 3 else "stale"
                return {
                    "status": status,
                    "consecutive_failures": self._consecutive_failures,
                    "buffer_count": row[3],
                }

            self._consecutive_failures = 0
            return {
                "status": "alive",
                "consecutive_failures": 0,
                "buffer_count": row[3],
            }
        except Exception as e:
            self._consecutive_failures += 1
            status = "dead" if self._consecutive_failures >= 3 else "stale"
            return {
                "status": status,
                "consecutive_failures": self._consecutive_failures,
                "msg": str(e),
            }

    def _get_pid_info(self, system: str):
        """获取系统 PID 信息，优先 SQLite，热轨兜底"""
        try:
            conn = sqlite3.connect(f"file:{self.DB}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT system, pid, registered_at, last_seen, mode FROM system_pid WHERE system = ?",
                    (system,),
                ).fetchone()
            finally:
                conn.close()

            if row:
                return {
                    "system": row[0],
                    "pid": row[1],
                    "registered_at": row[2],
                    "last_seen": row[3],
                    "mode": row[4],
                    "source": "sqlite",
                }
        except Exception:
            pass

        # SQLite 无记录？去热轨兜底
        hot_files = sorted(
            self.HOT.glob(f"{system}_*.jsonl"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        for filepath in hot_files[:3]:
            try:
                for line in filepath.read_text(encoding="utf-8").strip().splitlines():
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if ev.get("event_type") == "__register__":
                        return {
                            "system": system,
                            "pid": ev["payload"]["pid"],
                            "registered_at": ev["payload"].get("registered_at"),
                            "last_seen": ev["timestamp"],
                            "mode": ev["payload"].get("mode", "white"),
                            "source": "hot_fallback",
                        }
            except Exception:
                continue

        return None

    def _is_pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _synthesize_event(self, system: str, event_type: str, payload: dict):
        """合成事件：写入热轨而非直接写 SQLite"""
        self.HOT.mkdir(parents=True, exist_ok=True)
        ts_ms = int(time.time() * 1000)
        filepath = self.HOT / f"watchdog_{system}_{ts_ms}_000.jsonl"
        record = {
            "system": system,
            "mode": "watchdog",
            "event_type": event_type,
            "payload": {
                **payload,
                "_source": "watchdog_synthetic",
                "_time": time.time(),
            },
            "timestamp": time.time(),
        }
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def check_all(self):
        """执行一次完整检查"""
        alerts = []

        # 检查 1：归档器心跳
        archiver_status = self._check_archiver()
        if archiver_status["status"] == "dead":
            alerts.append(
                {
                    "severity": "P0",
                    "type": "archiver_dead",
                    "msg": f"归档器心跳停止，连续失败 {archiver_status['consecutive_failures']} 次",
                    "action": "human_escalated",
                }
            )
            # 尝试重启归档器
            if self._restart_attempts < self._max_restarts:
                self._restart_attempts += 1
                alerts.append(
                    {
                        "severity": "P1",
                        "type": "archiver_restart_attempt",
                        "msg": f"尝试重启归档器（{self._restart_attempts}/{self._max_restarts}）",
                        "action": "auto_restart",
                    }
                )
            else:
                # 重启失败，emit __archiver_down__ 事件
                self._synthesize_event(
                    "__self__",
                    "__archiver_down__",
                    {
                        "restart_attempts": self._restart_attempts,
                        "max_restarts": self._max_restarts,
                        "last_heartbeat": self.HEARTBEAT.read_text().strip()
                        if self.HEARTBEAT.exists()
                        else None,
                    },
                )
                alerts.append(
                    {
                        "severity": "P0",
                        "type": "archiver_down",
                        "msg": f"归档器重启失败（{self._max_restarts} 次），已 emit __archiver_down__ 事件",
                        "action": "human_escalated",
                    }
                )
        elif archiver_status["status"] == "stale":
            alerts.append(
                {
                    "severity": "P1",
                    "type": "archiver_heartbeat_stale",
                    "msg": f"归档器心跳超时，连续失败 {archiver_status['consecutive_failures']} 次",
                    "action": "human_review",
                }
            )
            self._restart_attempts = 0  # 重置重启计数
        else:
            # alive 状态：重置重启计数，确保恢复后计数器归零
            self._restart_attempts = 0

        # 检查 2：自健康指标（archiver_lag）
        try:
            from self_health import check_archiver_lag

            lag, status = check_archiver_lag()
            if lag is not None and lag > 30:
                alerts.append(
                    {
                        "severity": "P0",
                        "type": "self_health_archiver_lag",
                        "msg": f"自健康检测：归档器延迟 {lag:.1f}s",
                        "action": "human_review",
                    }
                )
        except ImportError:
            pass  # self_health 模块不可用时跳过

        for a in alerts:
            self.webhook(a)

        return alerts

    def start_daemon(self):
        self._alive = True

    def stop(self):
        self._alive = False
