# web_exporter.py —— v0.11.10 只读数据生成器（前端对齐版）
# 职责：读 SQLite + 扫描热轨 + 检查心跳 → 输出 data.json
# 依赖：标准库 only

import sqlite3, json, time, os, hashlib, subprocess, sys
from pathlib import Path

_src = str(Path(__file__).parent.parent.parent)
if _src not in sys.path:
    sys.path.insert(0, _src)
from archiver_compress import resolve_payload


def _get_excluded():
    systems = set()
    # 主要来源：~/.ming/.paused/{system}（探针端文件信号）
    paused_dir = Path.home() / ".ming" / ".paused"
    if paused_dir.is_dir():
        for f in paused_dir.iterdir():
            if f.is_file():
                systems.add(f.name)
    # 兜底来源：旧版 excluded_systems.json
    try:
        p = Path.home() / ".ming" / "excluded_systems.json"
        if p.exists():
            systems.update(json.loads(p.read_text()).get("systems", []))
    except Exception:
        pass
    return systems


def _normalize_ts(ts):
    """处理 timestamp 单位混用（历史数据可能为毫秒）"""
    if ts is None or ts <= 0:
        return 0
    now_sec = time.time()
    if ts > now_sec * 100:
        return ts / 1000
    return ts


FIRST_SEEN_ALIASES = ("first_seen", "min_ts", "start_time", "first_event")
LAST_SEEN_ALIASES = (
    "last_event",
    "last_seen",
    "maxts",
    "last_heartbeat",
    "last_update",
    "last_retrieve",
    "timestamp",
)


KNOWN_PROBES = {"tusunsun", "langchain", "openclaw", "mingjing", "opencode"}


def _is_known(s):
    return any(s == name or s.startswith(name + "_") for name in KNOWN_PROBES)


DB = Path.home() / ".ming" / "ming.db"
HOT = Path.home() / ".ming" / "hot"
HEARTBEAT = Path.home() / ".ming" / ".archiver_heartbeat"
OUT = Path.home() / ".ming" / "web"
OTEL_HEARTBEAT = Path.home() / ".ming" / ".otel_heartbeat"
OTEL_CHECKPOINT = Path.home() / ".ming" / ".otel_checkpoint"
DISEASES_YAML = Path(__file__).parent.parent.parent.parent / "config" / "diseases.yaml"
REMEDIES_YAML = Path(__file__).parent.parent.parent.parent / "config" / "remedies.yaml"


def _safe_query(conn, sql, params=(), default=None):
    """安全执行查询，出错返回默认值"""
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:
        return default if default is not None else []


def _has_column(conn, table, column):
    """检查表是否存在某列"""
    try:
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return any(r[1] == column for r in info)
    except sqlite3.OperationalError:
        return False


def _get_channel_mix(conn):
    """统计 _ingest_channel 分布比例"""
    if not _has_column(conn, "events", "_ingest_channel"):
        return {"hooks": 1.0, "otel": 0.0, "duplicate": 0.0}
    try:
        rows = conn.execute("""
            SELECT _ingest_channel, COUNT(*) as cnt
            FROM events
            WHERE _ingest_channel IS NOT NULL
            GROUP BY _ingest_channel
        """).fetchall()
        total = sum(r[1] for r in rows)
        if total == 0:
            return {"hooks": 1.0, "otel": 0.0, "duplicate": 0.0}
        mix = {"hooks": 0.0, "otel": 0.0, "duplicate": 0.0}
        for ch, cnt in rows:
            if ch in mix:
                mix[ch] = round(cnt / total, 2)
        return mix
    except sqlite3.OperationalError:
        return {"hooks": 1.0, "otel": 0.0, "duplicate": 0.0}


def _get_otel_status(conn, now):
    """OTEL 双向翻译枢纽状态"""
    receiver_online = False
    received_count = 0
    dropped_count = 0
    last_received_ts = 0
    if OTEL_HEARTBEAT.exists():
        try:
            hb = json.loads(OTEL_HEARTBEAT.read_text())
            last_received_ts = hb.get("last_ts", 0)
            received_count = hb.get("received_count", 0)
            dropped_count = hb.get("dropped_count", 0)
            receiver_online = (now - last_received_ts) < 60
        except (json.JSONDecodeError, ValueError):
            try:
                last_received_ts = float(OTEL_HEARTBEAT.read_text().strip())
                receiver_online = (now - last_received_ts) < 60
            except ValueError:
                pass

    exporter_last = 0
    pushed_count = 0
    dead_letter_count = 0
    checkpoint_ts = 0
    if OTEL_CHECKPOINT.exists():
        try:
            cp = json.loads(OTEL_CHECKPOINT.read_text())
            exporter_last = cp.get("last_export_ts", 0)
            pushed_count = cp.get("pushed_count", 0)
            dead_letter_count = cp.get("dead_letter_count", 0)
            checkpoint_ts = cp.get("checkpoint_ts", 0)
        except (json.JSONDecodeError, ValueError):
            try:
                exporter_last = float(OTEL_CHECKPOINT.read_text().strip())
            except ValueError:
                pass

    port = int(os.getenv("MING_OTEL_PORT", "4319"))

    return {
        "receiver": {
            "online": receiver_online,
            "port": port,
            "received_count": received_count,
            "dropped_count": dropped_count,
            "last_received_ts": last_received_ts,
        },
        "exporter": {
            "last_export_ts": exporter_last,
            "pushed_count": pushed_count,
            "dead_letter_count": dead_letter_count,
            "checkpoint_ts": checkpoint_ts,
        },
        "channel_mix": _get_channel_mix(conn),
    }


def _get_triage(conn, now):
    """动态分诊覆盖率 — 从 triage_snapshot.json 读取"""
    snapshot_path = Path.home() / ".ming" / "triage_snapshot.json"
    if not snapshot_path.exists():
        return {
            "total_diseases": 157,
            "covered": 0,
            "tiers": {
                "P0": {"covered": 0, "total": 0},
                "P1": {"covered": 0, "total": 0},
                "P2": {"covered": 0, "total": 0},
            },
            "missing_fields": [],
            "snapshot_age_seconds": 0,
        }

    try:
        snapshot = json.loads(snapshot_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {
            "total_diseases": 157,
            "covered": 0,
            "tiers": {
                "P0": {"covered": 0, "total": 0},
                "P1": {"covered": 0, "total": 0},
                "P2": {"covered": 0, "total": 0},
            },
            "missing_fields": [],
            "snapshot_age_seconds": 0,
        }

    summary = snapshot.get("summary", {})
    rule_status = snapshot.get("rule_status", {})
    suggestions = snapshot.get("suggestions", [])

    tiers = {
        "P0": {"covered": 0, "total": 0},
        "P1": {"covered": 0, "total": 0},
        "P2": {"covered": 0, "total": 0},
    }

    # P2-6: 从 diseases.yaml 加载 severity 映射
    severity_map = {}
    if DISEASES_YAML.exists():
        try:
            diseases = _parse_yaml_simple(DISEASES_YAML) or []
            severity_map = {
                d["id"]: d.get("severity", "P2") for d in diseases if "id" in d
            }
        except Exception:
            pass

    for did, info in rule_status.items():
        # P2-6: 优先使用 YAML 中的 severity
        sev = severity_map.get(did, "P2")
        if sev not in tiers:
            # 回退到前缀映射
            for prefix, s in [
                ("SYS", "P0"),
                ("NET", "P1"),
                ("MDL", "P1"),
                ("TLT", "P1"),
                ("AGT", "P0"),
                ("MEM", "P2"),
                ("PRB", "P1"),
                ("DQT", "P1"),
                ("BIZ", "P1"),
            ]:
                if did.startswith(prefix):
                    sev = s
                    break
        if sev in tiers:
            tiers[sev]["total"] += 1
            if info.get("status") == "ready":
                tiers[sev]["covered"] += 1

    return {
        "total_diseases": summary.get("total", len(rule_status)),
        "covered": summary.get("ready", 0),
        "tiers": tiers,
        "missing_fields": [s["field"] for s in suggestions[:10]],
        "snapshot_age_seconds": int(now - snapshot.get("generated_at", now)),
    }


def _get_per_system_triage(conn, systems):
    """每系统可诊出数量 — 只增不减，上限 157"""
    MAX_FILE = Path.home() / ".ming" / ".max_diagnosable.json"

    disease_deps = {}
    total_diseases = 0
    if DISEASES_YAML.exists():
        try:
            import yaml

            with open(DISEASES_YAML, "r", encoding="utf-8") as f:
                rules = yaml.safe_load(f) or []
            total_diseases = len(rules)
            for rule in rules:
                rid = rule.get("id", "")
                deps = rule.get("depends", {})
                ev_types = deps.get("event_types", [])
                if ev_types:
                    disease_deps[rid] = set(ev_types)
        except Exception:
            pass

    # 读历史最大值
    stored_max = {}
    if MAX_FILE.exists():
        try:
            stored_max = json.loads(MAX_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass

    # 查 DB 当前事件类型
    per_system_event_types = {}
    for s in systems:
        rows = conn.execute(
            "SELECT DISTINCT event_type FROM events WHERE system = ?", (s,)
        ).fetchall()
        per_system_event_types[s] = {r[0] for r in rows}

    result = {}
    new_max = dict(stored_max)
    changed = False

    for s in systems:
        sys_events = per_system_event_types.get(s, set())
        current_count = sum(
            1 for evs in disease_deps.values() if evs.issubset(sys_events)
        )

        stored_count = stored_max.get(s, 0)
        final_count = max(current_count, stored_count)
        if final_count > stored_count:
            new_max[s] = final_count
            changed = True

        result[s] = {"ready": final_count, "total": total_diseases}

    if changed:
        try:
            MAX_FILE.parent.mkdir(parents=True, exist_ok=True)
            MAX_FILE.write_text(json.dumps(new_max, ensure_ascii=False))
        except Exception:
            pass

    return result


def _get_self_health(now):
    """自举健康指标"""
    archiver_lag = 999
    if HEARTBEAT.exists():
        try:
            last = float(HEARTBEAT.read_text().strip())
            archiver_lag = now - last
        except ValueError:
            pass

    wal_count = 0
    try:
        wal_path = DB.parent / f"{DB.name}-wal"
        if wal_path.exists():
            wal_count = 1
    except OSError:
        pass

    memory_rss = 0
    try:
        status = Path("/proc/self/status").read_text()
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                memory_rss = round(int(line.split()[1]) / 1024, 1)
                break
    except (OSError, IndexError, ValueError):
        pass

    return {
        "archiver_lag_sec": round(archiver_lag, 1),
        "probe_loss_rate": 0,
        "wal_count": wal_count,
        "memory_rss_mb": memory_rss,
        "last_meta_health_ts": now,
    }


def _read_proc_status(pid):
    """读 /proc/<pid>/status，返回 {rss_mb, vsz_mb} 或 None"""
    try:
        text = Path(f"/proc/{pid}/status").read_text()
        result = {}
        for line in text.splitlines():
            if line.startswith("VmRSS:"):
                result["rss_mb"] = round(int(line.split()[1]) / 1024, 1)
            elif line.startswith("VmSize:"):
                result["vsz_mb"] = round(int(line.split()[1]) / 1024, 1)
        return result if result else None
    except (OSError, ValueError):
        return None


def _proc_cpu_pct(pid):
    """读进程 CPU 占用率（ps 工具）"""
    try:
        r = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return 0.0


def _proc_fd_count(pid):
    """数 /proc/<pid>/fd 文件描述符数"""
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except (PermissionError, FileNotFoundError, OSError):
        return 0


def _proc_uptime_min(pid):
    """读进程运行时长（分钟）"""
    try:
        with open("/proc/uptime") as f:
            sys_uptime = float(f.read().split()[0])
        with open(f"/proc/{pid}/stat") as f:
            stat = f.read().strip()
            fields = stat[stat.rfind(")") + 2 :].split()
            if len(fields) >= 20:
                starttime_ticks = int(fields[19])
                hz = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
                uptime_sec = sys_uptime - (starttime_ticks / hz)
                return round(max(0, uptime_sec) / 60, 1)
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _collect_gpu():
    """采集 GPU 信息（nvidia-smi 不可用时返回空列表）"""
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []
        gpus = []
        for line in r.stdout.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 6:
                try:
                    gpus.append(
                        {
                            "index": int(parts[0]),
                            "name": parts[1],
                            "util_pct": round(float(parts[2]), 1),
                            "mem_used_mb": round(float(parts[3]), 1),
                            "mem_total_mb": round(float(parts[4]), 1),
                            "temp_c": round(float(parts[5]), 1),
                        }
                    )
                except (ValueError, IndexError):
                    continue
        return gpus
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []


def _read_archiver_pid():
    try:
        text = (Path.home() / ".ming" / "standalone.pid").read_text().strip()
        pid = int(text)
        if Path(f"/proc/{pid}").exists():
            return pid
    except (OSError, ValueError):
        pass
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        conn.execute("PRAGMA busy_timeout=3000")
        row = conn.execute(
            "SELECT pid FROM system_pid WHERE system = 'mingjing' AND pid > 0 LIMIT 1"
        ).fetchone()
        conn.close()
        if row and Path(f"/proc/{row[0]}").exists():
            return row[0]
    except Exception:
        pass
    return None


def _collect_footprint():
    """采集乾坤镜自身及所有已注册系统的资源脚印"""
    systems = []

    # 1. 乾坤镜自身（归档器进程，不是 web 面板自身）
    archiver_pid = _read_archiver_pid()
    mingjing_pid = archiver_pid if archiver_pid else os.getpid()
    self_status = _read_proc_status(mingjing_pid)
    if self_status:
        systems.append(
            {
                "system": "mingjing",
                "rss_mb": self_status.get("rss_mb", 0),
                "vsz_mb": self_status.get("vsz_mb", 0),
                "cpu_pct": round(_proc_cpu_pct(mingjing_pid), 1),
                "fd_count": _proc_fd_count(mingjing_pid),
                "uptime_min": _proc_uptime_min(mingjing_pid),
                "excluded": False,
            }
        )

    # 1b. Web 面板自身（可选 GUI，区别于归档器）
    web_pid = os.getpid()
    web_status = _read_proc_status(web_pid)
    if web_status:
        systems.append(
            {
                "system": "mingjing web",
                "rss_mb": web_status.get("rss_mb", 0),
                "vsz_mb": web_status.get("vsz_mb", 0),
                "cpu_pct": round(_proc_cpu_pct(web_pid), 1),
                "fd_count": _proc_fd_count(web_pid),
                "uptime_min": _proc_uptime_min(web_pid),
                "excluded": False,
                "mode": "white",
            }
        )

    # 2. 已注册系统
    excluded = _get_excluded()
    seen_pids = {web_pid, mingjing_pid}
    if DB.exists():
        try:
            conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout=3000")
            rows = conn.execute(
                "SELECT system, pid, mode FROM system_pid WHERE pid > 0 ORDER BY system"
            ).fetchall()
            conn.close()
            for system, pid, mode in rows:
                if system == "mingjing":
                    continue
                status = _read_proc_status(pid)
                if not status:
                    continue
                seen_pids.add(pid)
                systems.append(
                    {
                        "system": system,
                        "rss_mb": status.get("rss_mb", 0),
                        "vsz_mb": status.get("vsz_mb", 0),
                        "cpu_pct": round(_proc_cpu_pct(pid), 1),
                        "fd_count": _proc_fd_count(pid),
                        "uptime_min": _proc_uptime_min(pid),
                        "excluded": system in excluded,
                        "mode": mode,
                    }
                )
                # Scan /proc for main process (higher RSS) matching system name
                best_main = None
                for entry in os.listdir("/proc"):
                    if not entry.isdigit() or int(entry) in seen_pids:
                        continue
                    try:
                        cmd = (
                            Path(f"/proc/{entry}/cmdline")
                            .read_bytes()
                            .decode(errors="replace")
                            .replace("\x00", " ")
                        )
                        if system.lower() in cmd.lower():
                            st = _read_proc_status(int(entry))
                            if st and st["rss_mb"] > 50:
                                if (
                                    not best_main
                                    or st["rss_mb"] > best_main["status"]["rss_mb"]
                                ):
                                    best_main = {"pid": int(entry), "status": st}
                    except (OSError, ValueError):
                        pass
                if best_main:
                    seen_pids.add(best_main["pid"])
                    s = best_main["status"]
                    systems.append(
                        {
                            "system": system,
                            "rss_mb": s["rss_mb"],
                            "vsz_mb": s.get("vsz_mb", 0),
                            "cpu_pct": round(_proc_cpu_pct(best_main["pid"]), 1),
                            "fd_count": _proc_fd_count(best_main["pid"]),
                            "uptime_min": _proc_uptime_min(best_main["pid"]),
                            "excluded": system in excluded,
                            "mode": "white",
                        }
                    )
        except sqlite3.OperationalError:
            pass

    return {"systems": systems, "gpu": _collect_gpu()}


def _write_empty_data():
    """DB 不存在或无数据时生成空 data.json"""
    OUT.mkdir(parents=True, exist_ok=True)
    data = {
        "exported_at": time.time(),
        "systems": [],
        "recent_events": [],
        "diagnoses": [],
        "health": [],
        "missing_expectations": [],
        "token_by_system": [],
        "token_by_model": [],
        "latency_stats": {},
        "call_freq": [],
        "integrity_stats": {
            "total": 0,
            "verified": 0,
            "corrupted": 0,
            "rebooted": 0,
            "rate": 100,
        },
        "capacity": {
            "db_size_mb": 0,
            "db_size_limit_mb": 2048,
            "event_count": 0,
            "event_count_limit": 1000000,
            "disk_free_mb": 0,
            "disk_total_mb": 0,
            "hot_files": 0,
            "hot_size_mb": 0,
        },
        "sys_pids": {},
        "hot_unarchived_files": 0,
        "hot_size_mb": 0,
        "archiver_alive": False,
        "archiver_age_seconds": 999,
        "otel_status": {
            "receiver": {
                "online": False,
                "port": 4319,
                "received_count": 0,
                "dropped_count": 0,
                "last_received_ts": 0,
            },
            "exporter": {
                "last_export_ts": 0,
                "pushed_count": 0,
                "dead_letter_count": 0,
                "checkpoint_ts": 0,
            },
            "channel_mix": {"hooks": 1.0, "otel": 0.0, "duplicate": 0.0},
        },
        "triage": {
            "total_diseases": 0,
            "covered": 0,
            "tiers": {},
            "missing_fields": [],
            "snapshot_age_seconds": 0,
        },
        "per_system_triage": {},
        "diagnoses_by_system": {},
        "self_health": {
            "archiver_lag_sec": 0,
            "probe_loss_rate": 0,
            "wal_count": 0,
            "memory_rss_mb": 0,
            "last_meta_health_ts": 0,
        },
        "alerts": [
            {"level": "info", "title": "无数据", "message": "数据库尚未创建或无事件"}
        ],
        "instance_cards": [],
        "global_summary": {
            "total_instances": 0,
            "healthy_count": 0,
            "unhealthy_count": 0,
            "total_active_diseases": 0,
            "total_p0": 0,
            "total_p1": 0,
            "total_p2": 0,
            "health_level": "healthy",
            "health_level_label": "健康",
            "integrity_rate": 100,
            "triage_ready": 0,
            "triage_total": 157,
            "exported_ago_seconds": 0,
        },
        "disease_distribution": {"by_layer": {}},
        "resource_footprint": {"systems": [], "gpu": []},
    }
    # P1-5: 原子写入
    tmp_path = OUT / "data.json.tmp"
    final_path = OUT / "data.json"
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(str(tmp_path), str(final_path))
    except OSError:
        if tmp_path.exists():
            tmp_path.unlink()
        raise
    return data


def export():
    OUT.mkdir(parents=True, exist_ok=True)

    if not DB.exists():
        return _write_empty_data()

    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA mmap_size = 0")
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return _write_empty_data()

    year = time.strftime("%Y")
    tbl = f"diagnoses_{year}"
    now = time.time()

    # === 基础系统列表 ===
    systems = []
    try:
        systems = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT system FROM events WHERE system NOT IN ('__host__', 'unknown', 'all') ORDER BY system"
            ).fetchall()
            if _is_known(r[0])
        ]
    except sqlite3.OperationalError:
        pass
    if "mingjing" not in systems:
        systems.append("mingjing")

    # === 第 2 层回退：批量查询所有系统的事件时间范围（全量历史）===
    sys_time_range = {}
    try:
        _rows = conn.execute(
            "SELECT system, MIN(timestamp), MAX(timestamp) FROM events GROUP BY system"
        ).fetchall()
        for _r in _rows:
            sys_time_range[_r[0]] = (_normalize_ts(_r[1]), _normalize_ts(_r[2]))
    except sqlite3.OperationalError:
        pass

    # === 诊断病历 ===
    diagnoses = []
    try:
        since = time.time() - 86400
        diag_cols = "diagnosis_id, system, fault_id, diagnosis_name, confidence, severity, status, evidence_quality, created_at, evidence, inference_chain"
        rows = conn.execute(
            f"SELECT {diag_cols} FROM {tbl} WHERE created_at > ? ORDER BY created_at DESC LIMIT 200",
            (since,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            # 构建 detail 对象（替代不存在的 detail 列）
            evidence_raw = d.pop("evidence", "[]")
            inference_raw = d.pop("inference_chain", "")
            try:
                evidence_list = json.loads(evidence_raw) if evidence_raw else []
            except (json.JSONDecodeError, TypeError):
                evidence_list = []

            # 提取模型信息：优先从证据中取，取不到则查 events 表
            model = None
            for ev in evidence_list:
                if isinstance(ev, dict):
                    model = ev.get("key_fields", {}).get("layer_llm", {}).get("model")
                    if not model:
                        model = ev.get("layer_llm", {}).get("model")
                    if model:
                        break
            # lit_lite 证据是 SQL 结果不含 model，回查 events 表
            if not model:
                try:
                    diag_time = d.get("created_at")
                    if diag_time:
                        row = conn.execute(
                            """
                            SELECT json_extract(payload, '$.layer_llm.model') as m
                            FROM events
                            WHERE system = ? AND event_type = 'llm_invoke'
                              AND timestamp BETWEEN ? - 3600 AND ?
                              AND json_extract(payload, '$.layer_llm.model') IS NOT NULL
                            ORDER BY timestamp DESC LIMIT 1
                            """,
                            (d.get("system"), diag_time, diag_time),
                        ).fetchone()
                        if row:
                            model = row[0]
                except Exception:
                    pass

            # === 3 层时间回退 ===
            # 第 1 层：从证据中提取时间别名（精确）
            occurred_at = None
            occurred_at_last = None
            for ev in evidence_list:
                if not isinstance(ev, dict):
                    continue
                kf = ev.get("key_fields", {})
                for alias in FIRST_SEEN_ALIASES:
                    val = kf.get(alias)
                    if val is not None:
                        try:
                            val = _normalize_ts(float(val))
                            if occurred_at is None or val < occurred_at:
                                occurred_at = val
                        except (ValueError, TypeError):
                            pass
                        break
                for alias in LAST_SEEN_ALIASES:
                    val = kf.get(alias)
                    if val is not None:
                        try:
                            val = _normalize_ts(float(val))
                            if occurred_at_last is None or val > occurred_at_last:
                                occurred_at_last = val
                        except (ValueError, TypeError):
                            pass
                        break

            # 第 2 层：系统级事件时间范围回退（近似，全量历史）
            sys_ts = sys_time_range.get(d.get("system"))
            if occurred_at is None and sys_ts:
                occurred_at = sys_ts[0]
            if occurred_at_last is None and sys_ts:
                occurred_at_last = sys_ts[1]

            # 第 3 层：created_at 兜底
            if occurred_at is None:
                occurred_at = d.get("created_at", 0)

            d["detail"] = {
                "rule": inference_raw or "",
                "evidence": evidence_list,
                "model": model,
            }
            d["occurred_at"] = occurred_at
            d["occurred_at_last"] = occurred_at_last or occurred_at
            diagnoses.append(d)
    except sqlite3.OperationalError:
        pass

    # === 探针健康 ===
    health = []
    for s in systems:
        try:
            row = conn.execute(
                """
                SELECT emit_count, drop_count, disk_free_mb, integrity_score, window_start
                FROM probe_health WHERE system = ? ORDER BY window_start DESC LIMIT 1
            """,
                (s,),
            ).fetchone()
            if row:
                h = dict(row)
                h["system"] = s
                h["alive"] = (now - h.get("window_start", 0)) < 60
                # 补充 pid 和 mode
                pid_row = conn.execute(
                    "SELECT pid, mode FROM system_pid WHERE system = ? LIMIT 1",
                    (s,),
                ).fetchone()
                if pid_row:
                    h["pid"] = pid_row[0]
                    h["mode"] = pid_row[1] or "white"
                health.append(h)
            else:
                # 回退1：从 events 表最新事件时间推导（最可靠）
                evt_row = conn.execute(
                    "SELECT MAX(timestamp) FROM events WHERE system = ?",
                    (s,),
                ).fetchone()
                if evt_row and evt_row[0]:
                    last_evt = evt_row[0]
                    # 从 system_pid 补充 pid 和 mode
                    pid_row = conn.execute(
                        "SELECT pid, mode FROM system_pid WHERE system = ? LIMIT 1",
                        (s,),
                    ).fetchone()
                    health.append(
                        {
                            "system": s,
                            "emit_count": 0,
                            "drop_count": 0,
                            "disk_free_mb": -1,
                            "integrity_score": 1.0,
                            "window_start": last_evt,
                            "alive": (now - last_evt) < 120,
                            "pid": pid_row[0] if pid_row else 0,
                            "mode": pid_row[1] if pid_row else "white",
                        }
                    )
                else:
                    # 回退2：从 system_pid 推导
                    pid_row = conn.execute(
                        "SELECT pid, last_seen, mode FROM system_pid WHERE system = ? ORDER BY last_seen DESC LIMIT 1",
                        (s,),
                    ).fetchone()
                    if pid_row and pid_row[1]:
                        last_seen = pid_row[1] or 0
                        health.append(
                            {
                                "system": s,
                                "emit_count": 0,
                                "drop_count": 0,
                                "disk_free_mb": -1,
                                "integrity_score": 1.0,
                                "window_start": last_seen,
                                "alive": (now - last_seen) < 120,
                                "pid": pid_row[0],
                                "mode": pid_row[2] or "white",
                            }
                        )
        except sqlite3.OperationalError:
            pass

    # === 未满足预期 ===
    missing = []
    try:
        expectations = conn.execute(
            """
            SELECT system, expected_event, deadline, fulfilled
            FROM expectations WHERE fulfilled = 0 AND deadline < ?
        """,
            (now,),
        ).fetchall()
        missing = [dict(r) for r in expectations]
    except sqlite3.OperationalError:
        pass

    # === 最近事件（全局 LIMIT 200，按时间排序）===
    recent_events = []
    has_integrity = _has_column(conn, "events", "integrity")
    has_ingest_channel = _has_column(conn, "events", "_ingest_channel")
    cols = "e.id, e.system, e.event_type, e.mode, e.integrity_score, e.lamport, e.timestamp, e.payload, e.storage_tier, b.payload_blob"
    if has_integrity:
        cols += ", e.integrity"
    if has_ingest_channel:
        cols += ", e._ingest_channel"
    rows = _safe_query(
        conn,
        f"SELECT {cols} FROM events e LEFT JOIN events_blob b ON e.id = b.event_id ORDER BY e.timestamp DESC LIMIT 200",
    )
    for r in rows:
        d = dict(r)
        try:
            d["payload_obj"] = resolve_payload(d)
        except (json.JSONDecodeError, TypeError):
            d["payload_obj"] = {}
        if not d.get("integrity"):
            score = d.get("integrity_score", 0) or 0
            d["integrity"] = (
                "verified"
                if score >= 0.9
                else ("rebooted" if score >= 0.5 else "corrupted")
            )
        if not d.get("_ingest_channel"):
            d["_ingest_channel"] = "hooks"
        d["model"] = (
            d.get("payload_obj", {}).get("layer_llm", {}).get("model")
            if isinstance(d.get("payload_obj"), dict)
            else None
        )
        recent_events.append(d)

    # === Token 统计（按系统）===
    token_by_system = []
    for s in systems:
        rows = _safe_query(
            conn,
            "SELECT e.storage_tier, e.payload, b.payload_blob "
            "FROM events e LEFT JOIN events_blob b ON e.id = b.event_id "
            "WHERE e.system = ? AND e.event_type = 'llm_invoke'",
            (s,),
        )
        inp = out = cnt = 0
        lats = []
        for r in rows:
            cnt += 1
            p = resolve_payload(dict(r))
            if not isinstance(p, dict):
                continue
            inp += p.get("layer_llm", {}).get("input_tokens", 0) or 0
            out += p.get("layer_llm", {}).get("output_tokens", 0) or 0
            lat = p.get("layer_llm", {}).get("latency_ms", 0) or 0
            if lat:
                lats.append(lat)
        token_by_system.append(
            {
                "system": s,
                "input_tokens": inp,
                "output_tokens": out,
                "total_tokens": inp + out,
                "call_count": cnt,
                "avg_latency": sum(lats) / len(lats) if lats else 0,
                "max_latency": max(lats) if lats else 0,
            }
        )
    token_by_system.sort(key=lambda x: x.get("total_tokens", 0), reverse=True)

    # === Token 统计（按模型）===
    model_rows = _safe_query(
        conn,
        "SELECT e.storage_tier, e.payload, b.payload_blob "
        "FROM events e LEFT JOIN events_blob b ON e.id = b.event_id "
        "WHERE e.event_type = 'llm_invoke'",
    )
    model_agg = {}
    for r in model_rows:
        p = resolve_payload(dict(r))
        if not isinstance(p, dict):
            continue
        model = (p.get("layer_llm") or {}).get("model")
        if not model:
            continue
        inp = (p.get("layer_llm") or {}).get("input_tokens", 0) or 0
        out = (p.get("layer_llm") or {}).get("output_tokens", 0) or 0
        if model not in model_agg:
            model_agg[model] = {"input_tokens": 0, "output_tokens": 0, "call_count": 0}
        model_agg[model]["input_tokens"] += inp
        model_agg[model]["output_tokens"] += out
        model_agg[model]["call_count"] += 1
    token_by_model = sorted(
        [
            {"model": m, **v, "total_tokens": v["input_tokens"] + v["output_tokens"]}
            for m, v in model_agg.items()
        ],
        key=lambda x: x["total_tokens"],
        reverse=True,
    )

    # === 延迟分布（所有 LLM 调用）===
    latency_rows = _safe_query(
        conn,
        "SELECT e.storage_tier, e.payload, b.payload_blob "
        "FROM events e LEFT JOIN events_blob b ON e.id = b.event_id "
        "WHERE e.event_type = 'llm_invoke'",
    )
    latencies = []
    for r in latency_rows:
        p = resolve_payload(dict(r))
        if not isinstance(p, dict):
            continue
        lat = (p.get("layer_llm") or {}).get("latency_ms")
        if lat is not None:
            latencies.append(lat)
    latency_stats = {}
    if latencies:
        latencies.sort()
        n = len(latencies)
        latency_stats = {
            "count": n,
            "min": latencies[0],
            "max": latencies[-1],
            "avg": sum(latencies) / n,
            "p50": latencies[n // 2],
            "p95": latencies[int(n * 0.95)],
            "p99": latencies[int(n * 0.99)],
        }

    # === API 调用频率（最近 5 分钟，按系统）===
    call_freq = []
    window = now - 300
    for s in systems:
        rows = _safe_query(
            conn,
            """
            SELECT COUNT(*) as cnt
            FROM events
            WHERE system = ? AND event_type = 'llm_invoke' AND timestamp > ?
        """,
            (s, window),
        )
        if rows and rows[0]:
            cnt = rows[0][0]
            call_freq.append(
                {"system": s, "calls_5min": cnt, "calls_per_min": round(cnt / 5, 1)}
            )
    call_freq.sort(key=lambda x: x["calls_5min"], reverse=True)

    # === 哈希链完整性统计 ===
    integrity_stats = {}
    rows = _safe_query(
        conn,
        """
        SELECT integrity, COUNT(*) as cnt
        FROM events
        WHERE integrity IS NOT NULL AND integrity != 'pending'
        GROUP BY integrity
    """,
    )
    for r in rows:
        integrity_stats[r[0]] = r[1]
    total_integrity = sum(integrity_stats.values())
    integrity_stats["total"] = total_integrity
    integrity_stats["rate"] = (
        round(integrity_stats.get("verified", 0) / max(total_integrity, 1) * 100, 2)
        if total_integrity > 0
        else 100
    )
    # 记录 pending 数量供前端参考
    pending_rows = _safe_query(
        conn,
        "SELECT COUNT(*) FROM events WHERE integrity = 'pending'",
    )
    if pending_rows and pending_rows[0] and pending_rows[0][0] > 0:
        integrity_stats["pending"] = pending_rows[0][0]

    # === 容量指标 ===
    db_size = DB.stat().st_size if DB.exists() else 0
    event_count = 0
    try:
        event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    hot_files = len(list(HOT.glob("*.jsonl"))) if HOT.exists() else 0
    hot_size = 0
    if HOT.exists():
        for f in HOT.glob("*.jsonl"):
            try:
                hot_size += f.stat().st_size
            except OSError:
                pass

    # 磁盘使用
    try:
        stat = os.statvfs(str(DB.parent))
        disk_free_mb = (stat.f_bfree * stat.f_frsize) / (1024 * 1024)
        disk_total_mb = (stat.f_blocks * stat.f_frsize) / (1024 * 1024)
    except OSError:
        disk_free_mb = 0
        disk_total_mb = 0

    capacity = {
        "db_size_mb": round(db_size / (1024 * 1024), 1),
        "db_size_limit_mb": 2048,
        "event_count": event_count,
        "event_count_limit": 1000000,
        "disk_free_mb": round(disk_free_mb, 0),
        "disk_total_mb": round(disk_total_mb, 0),
        "hot_files": hot_files,
        "hot_size_mb": round(hot_size / (1024 * 1024), 1),
    }

    # === 系统 PID 信息 ===
    sys_pids = {}
    for s in systems:
        try:
            row = conn.execute(
                """
                SELECT pid, registered_at, last_seen, mode
                FROM system_pid WHERE system = ? ORDER BY last_seen DESC LIMIT 1
            """,
                (s,),
            ).fetchone()
            if row:
                sys_pids[s] = dict(row)
        except sqlite3.OperationalError:
            pass

    # === OTEL 状态 ===
    otel_status = _get_otel_status(conn, now)

    # === 动态分诊 ===
    triage = _get_triage(conn, now)
    per_system_triage = _get_per_system_triage(conn, systems)

    # === 每系统诊断聚合 ===
    diagnoses_by_system = {}
    for d in diagnoses:
        s = d.get("system", "unknown")
        if s not in diagnoses_by_system:
            diagnoses_by_system[s] = {"total": 0, "P0": 0, "P1": 0, "P2": 0}
        diagnoses_by_system[s]["total"] += 1
        sev = d.get("severity", "P2")
        if sev in ("P0", "P1", "P2"):
            diagnoses_by_system[s][sev] += 1

    # === v1.3: 疾病中心聚合 ===
    disease_catalog = _load_disease_catalog()
    remedies = _load_remedies()
    diagnoses = _enrich_diagnoses(diagnoses, remedies)

    dismissed = _load_dismissed_diseases()
    archived = _load_archived_diseases()
    resets = _load_health_resets()

    global_integrity_rate = integrity_stats.get("rate", 100) / 100.0
    instance_cards = _build_instance_cards(
        diagnoses,
        systems,
        health,
        sys_pids,
        global_integrity_rate,
        dismissed,
        archived,
        resets,
        per_system_triage,
    )
    disease_distribution = _build_disease_distribution(disease_catalog, instance_cards)

    global_level, global_label = _calc_global_health_level(instance_cards)
    healthy_count = sum(1 for c in instance_cards if c["health_level"] == "healthy")
    total_active_faults = set()
    total_p0 = total_p1 = total_p2 = 0
    for c in instance_cards:
        for dx in c["diagnoses"]:
            if dx.get("status") == "false_positive":
                continue
            fid = dx.get("fault_id", "")
            if fid not in total_active_faults:
                total_active_faults.add(fid)
                sev = dx.get("severity", "")
                if sev == "P0":
                    total_p0 += 1
                elif sev == "P1":
                    total_p1 += 1
                elif sev == "P2":
                    total_p2 += 1

    global_summary = {
        "total_instances": len(instance_cards),
        "healthy_count": healthy_count,
        "unhealthy_count": len(instance_cards) - healthy_count,
        "total_active_diseases": len(total_active_faults),
        "total_p0": total_p0,
        "total_p1": total_p1,
        "total_p2": total_p2,
        "health_level": global_level,
        "health_level_label": global_label,
        "integrity_rate": integrity_stats.get("rate", 100),
        "triage_ready": triage.get("covered", 0),
        "triage_total": triage.get("total_diseases", 157),
        "exported_ago_seconds": 0,
    }

    conn.close()

    # === 自举健康 ===
    self_health = _get_self_health(now)

    # === 探针丢包率 ===
    total_emit = 0
    total_drop = 0
    for h in health:
        total_emit += h.get("emit_count", 0) or 0
        total_drop += h.get("drop_count", 0) or 0
    if total_emit > 0:
        self_health["probe_loss_rate"] = round(total_drop / total_emit, 4)

    # === 归档器心跳 ===
    archiver_alive = False
    archiver_age = 999
    if HEARTBEAT.exists():
        try:
            last = float(HEARTBEAT.read_text().strip())
            archiver_age = now - last
            archiver_alive = archiver_age < 60
        except ValueError:
            pass

    # === 组装 data.json ===
    data = {
        "exported_at": now,
        "systems": systems,
        "recent_events": recent_events,
        "diagnoses": diagnoses,
        "health": health,
        "missing_expectations": missing,
        "token_by_system": token_by_system,
        "token_by_model": token_by_model,
        "latency_stats": latency_stats,
        "call_freq": call_freq,
        "integrity_stats": integrity_stats,
        "capacity": capacity,
        "sys_pids": sys_pids,
        "hot_unarchived_files": hot_files,
        "hot_size_mb": capacity["hot_size_mb"],
        "archiver_alive": archiver_alive,
        "archiver_age_seconds": archiver_age,
        "otel_status": otel_status,
        "triage": triage,
        "per_system_triage": per_system_triage,
        "diagnoses_by_system": diagnoses_by_system,
        "self_health": self_health,
        "alerts": [],
        # v1.3: 疾病中心新字段
        "instance_cards": instance_cards,
        "global_summary": global_summary,
        "disease_distribution": disease_distribution,
        "resource_footprint": _collect_footprint(),
    }

    # === 生成告警 ===
    if not archiver_alive:
        data["alerts"].append(
            {
                "level": "error",
                "title": "归档器离线",
                "message": "归档器心跳超时 (>60s)",
            }
        )
    for h in health:
        sys_name = h.get("system") or "unknown"
        if h.get("disk_free_mb", -1) >= 0 and h.get("disk_free_mb", 9999) < 500:
            data["alerts"].append(
                {
                    "level": "error",
                    "title": f"{sys_name} 磁盘临界",
                    "message": f"磁盘剩余 < 500MB ({h['disk_free_mb']:.0f}MB)",
                }
            )
        if h.get("drop_count", 0) > 0:
            data["alerts"].append(
                {
                    "level": "warning",
                    "title": f"{sys_name} 探针丢包",
                    "message": f"丢弃 {h['drop_count']} 条事件",
                }
            )
    if hot_files > 100:
        data["alerts"].append(
            {
                "level": "warning",
                "title": "热轨堆积",
                "message": f"未归档文件 {hot_files} 个",
            }
        )
    if integrity_stats.get("rate", 100) < 99:
        data["alerts"].append(
            {
                "level": "error",
                "title": "哈希链断裂",
                "message": f"完整性仅 {integrity_stats['rate']}%",
            }
        )
    # Token 告警：单系统 5 分钟内调用超过 1000 次
    for cf in call_freq:
        if cf["calls_5min"] > 1000:
            data["alerts"].append(
                {
                    "level": "warning",
                    "title": f"{cf['system']} API 调用频繁",
                    "message": f"5 分钟内 {cf['calls_5min']} 次调用",
                }
            )

    # P1-5: 原子写入 data.json
    _apply_alert_keys(data)
    _filter_dismissed_alerts(data)
    tmp_path = OUT / "data.json.tmp"
    final_path = OUT / "data.json"
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(str(tmp_path), str(final_path))
    except OSError:
        if tmp_path.exists():
            tmp_path.unlink()
        raise

    # 同步到 web_dashboard 目录（前端 http server 读取路径）
    try:
        web_dir = Path(__file__).parent
        web_data = web_dir / "data.json"
        web_tmp = web_dir / "data.json.tmp"
        web_tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(str(web_tmp), str(web_data))
    except OSError:
        pass

    return data


def _apply_alert_keys(data: dict):
    """为每条 alert 追加 alert_key（sha256 前 16 位），用于前端忽略匹配
    哈希链断裂使用固定 key，避免百分比波动导致 key 变化
    """
    for a in data.get("alerts", []):
        if a.get("title") == "哈希链断裂":
            a["alert_key"] = "hash_chain_break"
        else:
            raw = f"{a.get('title', '')}|{a.get('message', '')}"
            a["alert_key"] = hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_dismissed_alerts() -> set:
    """加载已忽略的 alert_key 集合"""
    path = Path.home() / ".ming" / "dismissed_alerts.json"
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("alert_keys", []))
    except Exception:
        return set()


def _filter_dismissed_alerts(data: dict):
    """从 alerts 列表中移除已被忽略的告警"""
    dismissed = _load_dismissed_alerts()
    if not dismissed:
        return
    original = data.get("alerts", [])
    data["alerts"] = [a for a in original if a.get("alert_key") not in dismissed]


# ===== v1.3: 疾病中心后端 — 实例卡片聚合 =====


def _yaml_value(val):
    """Convert basic YAML scalar values to Python types"""
    if val == "[]":
        return []
    if val == "{}":
        return {}
    if val.lower() == "true":
        return True
    if val.lower() == "false":
        return False
    if val.lower() == "null" or val == "~":
        return None
    if val.startswith("[") and val.endswith("]"):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            pass
    return val


def _parse_yaml_simple(path: Path) -> list:
    """零依赖 YAML 解析器，支持列表格式和 multiline | 值"""
    content = path.read_text(encoding="utf-8")
    items = []
    current = {}
    in_multiline = False
    multiline_key = None
    multiline_val = []
    for line in content.split("\n"):
        if in_multiline:
            if line.startswith("    ") or line.strip() == "":
                multiline_val.append(line)
                continue
            else:
                current[multiline_key] = "\n".join(multiline_val).strip()
                in_multiline = False
                multiline_val = []
        stripped = line.strip()
        if stripped.startswith("- "):
            if current and ("id" in current or "rule_id" in current):
                items.append(current)
            current = {}
            kv = stripped[2:].split(":", 1)
            if len(kv) == 2:
                current[kv[0].strip()] = _yaml_value(
                    kv[1].strip().strip('"').strip("'")
                )
        elif ":" in stripped and not stripped.startswith("#"):
            kv = stripped.split(":", 1)
            key = kv[0].strip()
            val = kv[1].strip()
            if val == "|":
                in_multiline = True
                multiline_key = key
                multiline_val = []
            elif val:
                current[key] = _yaml_value(val.strip('"').strip("'"))
    if current and ("id" in current or "rule_id" in current):
        items.append(current)
    return items


def _load_dismissed_diseases():
    path = Path.home() / ".ming" / "dismissed_diseases.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_archived_diseases():
    path = Path.home() / ".ming" / "archived_diseases.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_health_resets():
    path = Path.home() / ".ming" / "health_resets.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_disease_catalog():
    """加载 diseases.yaml，返回 {fault_id: {name, layer, severity, description}}"""
    catalog = {}
    if not DISEASES_YAML.exists():
        return catalog
    try:
        diseases = _parse_yaml_simple(DISEASES_YAML)
        for d in diseases:
            rid = d.get("id", "")
            if rid:
                catalog[rid] = {
                    "name": d.get("name", ""),
                    "layer": d.get("layer", ""),
                    "severity": d.get("severity", "P2"),
                    "description": d.get("description", ""),
                }
    except Exception:
        pass
    return catalog


def _load_remedies():
    """加载 remedies.yaml，返回 {rule_id: {emergency, immediate, short_term, long_term}}"""
    remedies = {}
    if not REMEDIES_YAML.exists():
        return remedies
    try:
        items = _parse_yaml_simple(REMEDIES_YAML)
        for item in items:
            rid = item.get("rule_id", "")
            if not rid:
                continue
            tiers = item.get("tiers", {})
            if isinstance(tiers, dict):
                remedies[rid] = {
                    "emergency": tiers.get("emergency", {}),
                    "immediate": tiers.get("immediate", {}),
                    "short_term": tiers.get("short_term", {}),
                    "long_term": tiers.get("long_term", {}),
                }
    except Exception:
        pass
    return remedies


def _calc_instance_health_level(
    instance_diagnoses,
    global_integrity_rate,
    dismissed_faults,
    archived_faults,
    reset_ts,
):
    """
    四档等级制:
    危急 (critical)      — 有 P0 疾病
    警告 (warning)       — 有 P1 疾病，或全局完整性 < 60%
    亚健康 (sub_healthy) — 有 P2 疾病，或全局完整性 60-90%
    健康 (healthy)       — 无 P0/P1/P2 疾病，完整性 >= 90%

    已忽略/已归档的疾病不计入健康评估。
    健康复位: 如果实例有复位时间且所有活跃疾病的 first_seen < reset_ts，返回 healthy。
    """
    if reset_ts:
        all_old = True
        for dx in instance_diagnoses:
            fid = dx.get("fault_id", "")
            if fid in dismissed_faults or fid in archived_faults:
                continue
            if dx.get("first_seen", 0) >= reset_ts:
                all_old = False
                break
        if all_old:
            return "healthy", "健康"

    active_faults = set()
    has_p0 = has_p1 = has_p2 = False

    for dx in instance_diagnoses:
        if dx.get("status") == "false_positive":
            continue
        fid = dx.get("fault_id", "")
        if fid in dismissed_faults or fid in archived_faults:
            continue
        if fid in active_faults:
            continue
        active_faults.add(fid)
        sev = dx.get("severity", "")
        if sev == "P0":
            has_p0 = True
        elif sev == "P1":
            has_p1 = True
        elif sev == "P2":
            has_p2 = True

    if has_p0:
        return "critical", "危急"
    if has_p1 or global_integrity_rate < 0.60:
        return "warning", "警告"
    if has_p2 or global_integrity_rate < 0.90:
        return "sub_healthy", "亚健康"

    return "healthy", "健康"


def _calc_global_health_level(instance_cards):
    level_order = {"critical": 0, "warning": 1, "sub_healthy": 2, "healthy": 3}
    if not instance_cards:
        return "healthy", "健康"
    worst = min(
        instance_cards,
        key=lambda c: level_order.get(c.get("health_level", "healthy"), 3),
    )
    return worst.get("health_level", "healthy"), worst.get("health_level_label", "健康")


def _enrich_diagnoses(diagnoses, remedies):
    """为诊断补充 remedy, has_remedy 信息"""
    for d in diagnoses:
        fid = d.get("fault_id", "")
        remedy = remedies.get(fid)
        d["remedy"] = remedy
        d["has_remedy"] = remedy is not None
    return diagnoses


def _build_instance_cards(
    diagnoses,
    systems,
    health_data,
    sys_pids,
    global_integrity_rate,
    dismissed,
    archived,
    resets,
    per_system_triage,
):
    """核心: 按实例聚合诊断，合并同 fault_id，计算健康等级"""
    DISPLAY_NAMES = {"mingjing": "乾坤镜"}
    excluded = _get_excluded()
    cards = {}
    for s in systems:
        display_name = DISPLAY_NAMES.get(s, s)
        cards[s] = {
            "system": s,
            "pid_suffix": str(s).split("-")[-1] if "-" in s else "",
            "display_name": display_name,
            "pid": 0,
            "health_level": "healthy",
            "health_level_label": "健康",
            "last_heartbeat": 0,
            "heartbeat_ago_seconds": 999,
            "triage_ready": 0,
            "triage_total": 0,
            "diagnoses": [],
            "archived_diagnoses": [],
            "diagnosis_count": 0,
            "p0_count": 0,
            "p1_count": 0,
            "p2_count": 0,
            "excluded": s in excluded,
        }

    # 补充 PID 和心跳信息
    for h in health_data:
        sys = h.get("system", "")
        if sys in cards:
            cards[sys]["pid"] = h.get("pid", 0)
            cards[sys]["last_heartbeat"] = h.get("window_start", 0)
            cards[sys]["heartbeat_ago_seconds"] = (
                round(time.time() - cards[sys]["last_heartbeat"], 0)
                if cards[sys]["last_heartbeat"]
                else 999
            )

    # 补充 system_pid 数据（回退）
    for sys_name, pid_info in sys_pids.items():
        if sys_name in cards and cards[sys_name]["pid"] == 0:
            cards[sys_name]["pid"] = pid_info.get("pid", 0)

    # 补充 per-system triage
    for sys_name, triage_info in per_system_triage.items():
        if sys_name in cards:
            cards[sys_name]["triage_ready"] = triage_info.get("ready", 0)
            cards[sys_name]["triage_total"] = triage_info.get("total", 0)

    # 按系统分组诊断
    by_system = {}
    for d in diagnoses:
        sys = d.get("system", "unknown")
        if sys == "all":
            continue
        if sys not in by_system:
            by_system[sys] = []
        by_system[sys].append(d)

    sev_order = {"P0": 0, "P1": 1, "P2": 2, "META": 3}
    now = time.time()

    # 内部系统（OTEL 桥、主机监控）不创建实例卡片
    # 注意：mingjing 是乾坤镜自身，需要展示但标记为系统级
    INTERNAL_SYSTEMS = {"otel_bridge", "__host__", "unknown", "all"}
    DISPLAY_NAMES = {"mingjing": "乾坤镜"}

    for sys, dx_list in by_system.items():
        if sys in INTERNAL_SYSTEMS:
            continue
        display_name = DISPLAY_NAMES.get(sys, sys)
        if sys not in cards:
            display_name = DISPLAY_NAMES.get(sys, sys)
            cards[sys] = {
                "system": sys,
                "pid_suffix": "",
                "display_name": display_name,
                "pid": 0,
                "health_level": "healthy",
                "health_level_label": "健康",
                "last_heartbeat": 0,
                "heartbeat_ago_seconds": 999,
                "triage_ready": 0,
                "triage_total": 0,
                "diagnoses": [],
                "archived_diagnoses": [],
                "diagnosis_count": 0,
                "p0_count": 0,
                "p1_count": 0,
                "p2_count": 0,
                "excluded": sys in excluded,
            }

        display_name = cards[sys]["display_name"]

        # 合并同 fault_id 的诊断
        grouped = {}
        for d in dx_list:
            fid = d.get("fault_id", "")
            if fid not in grouped:
                grouped[fid] = []
            grouped[fid].append(d)

        active = []
        archived_list = []

        for fid, g_list in grouped.items():
            g_list.sort(key=lambda x: x.get("created_at", 0), reverse=True)
            latest = dict(g_list[0])
            latest["occurrence_count"] = len(g_list)
            latest["first_seen"] = min(dx.get("occurred_at", 0) for dx in g_list)
            latest["last_seen"] = max(dx.get("occurred_at_last", 0) for dx in g_list)
            latest["duration_seconds"] = max(0, now - latest["first_seen"])
            latest["merged_from"] = len(g_list)

            dk = f"{fid}:{display_name}"
            if dk in dismissed:
                continue
            if dk in archived:
                latest["is_dismissed"] = False
                latest["is_archived"] = True
                archived_list.append(latest)
            else:
                latest["is_dismissed"] = False
                latest["is_archived"] = False
                active.append(latest)

        # 按严重度排序
        active.sort(
            key=lambda x: (
                sev_order.get(x.get("severity", "P2"), 9),
                -x.get("confidence", 0),
            )
        )
        archived_list.sort(
            key=lambda x: (
                sev_order.get(x.get("severity", "P2"), 9),
                -x.get("confidence", 0),
            )
        )

        cards[sys]["diagnoses"] = active
        cards[sys]["archived_diagnoses"] = archived_list
        cards[sys]["diagnosis_count"] = len(active)

        for d in active:
            sev = d.get("severity", "P2")
            if sev == "P0":
                cards[sys]["p0_count"] += 1
            elif sev == "P1":
                cards[sys]["p1_count"] += 1
            elif sev == "P2":
                cards[sys]["p2_count"] += 1

    # 计算每个实例的健康等级
    now = time.time()
    for c in cards.values():
        display_name = c["display_name"]
        c["probe_time"] = (
            round(now - c["last_heartbeat"], 0) if c["last_heartbeat"] else -1
        )

        dismissed_faults = set()
        for key in dismissed:
            if key.endswith(f":{display_name}"):
                dismissed_faults.add(key.rsplit(":", 1)[0])
        archived_faults = set()
        for key in archived:
            if key.endswith(f":{display_name}"):
                archived_faults.add(key.rsplit(":", 1)[0])
        reset_ts = resets.get(display_name)

        level, label = _calc_instance_health_level(
            c["diagnoses"] if c["diagnoses"] else c["archived_diagnoses"],
            global_integrity_rate,
            dismissed_faults,
            archived_faults,
            reset_ts,
        )
        c["health_level"] = level
        c["health_level_label"] = label

    result = sorted(cards.values(), key=lambda x: x.get("display_name", ""))

    # mingjing 是系统级实例，无 PID / 无探针 / 无疾病谱
    for c in result:
        if c["system"] == "mingjing":
            c["pid"] = -1
            c["last_heartbeat"] = time.time()
            c["heartbeat_ago_seconds"] = 0
            c["triage_ready"] = -1
            c["triage_total"] = 0
            break

    return result


def _build_disease_distribution(disease_catalog, instance_cards):
    """按 layer 聚合检出统计"""
    layers = {
        "system": {"total": 0, "detected": 0, "instances": set()},
        "network": {"total": 0, "detected": 0, "instances": set()},
        "model": {"total": 0, "detected": 0, "instances": set()},
        "tool": {"total": 0, "detected": 0, "instances": set()},
        "agent": {"total": 0, "detected": 0, "instances": set()},
        "memory": {"total": 0, "detected": 0, "instances": set()},
        "probe": {"total": 0, "detected": 0, "instances": set()},
        "data_quality": {"total": 0, "detected": 0, "instances": set()},
        "business": {"total": 0, "detected": 0, "instances": set()},
    }

    # 统计每种疾病的总数
    for fid, info in disease_catalog.items():
        layer = info.get("layer", "")
        if layer in layers:
            layers[layer]["total"] += 1

    # 统计检出情况
    for card in instance_cards:
        for dx in card.get("diagnoses", []):
            fid = dx.get("fault_id", "")
            layer = disease_catalog.get(fid, {}).get("layer", "")
            if layer in layers:
                layers[layer]["detected"] += 1
                layers[layer]["instances"].add(card["display_name"])

    # 转换 set 为 list
    for layer_name in layers:
        layers[layer_name]["instances"] = sorted(list(layers[layer_name]["instances"]))

    return {"by_layer": layers}


if __name__ == "__main__":
    export()


def query_events(
    system: str = None,
    event_type: str = None,
    since: float = None,
    until: float = None,
    models: list = None,
    keyword: str = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """动态查询事件，支持筛选和分页"""
    if not DB.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        return []

    # Build basic WHERE (skip model/keyword — handled in Python for compressed rows)
    conditions = []
    params = []
    if system:
        conditions.append("e.system = ?")
        params.append(system)
    if event_type:
        conditions.append("e.event_type = ?")
        params.append(event_type)
    if since is not None:
        conditions.append("e.timestamp >= ?")
        params.append(since)
    if until is not None:
        conditions.append("e.timestamp <= ?")
        params.append(until)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    has_ingest_channel = _has_column(conn, "events", "_ingest_channel")
    has_integrity = _has_column(conn, "events", "integrity")
    cols = "e.id, e.system, e.event_type, e.mode, e.integrity_score, e.lamport, e.timestamp, e.payload, e.storage_tier, b.payload_blob"
    if has_integrity:
        cols += ", e.integrity"
    if has_ingest_channel:
        cols += ", e._ingest_channel"

    # Fetch with LEFT JOIN — filter model/keyword in Python to handle compressed rows
    sql = f"""
        SELECT {cols}
        FROM events e LEFT JOIN events_blob b ON e.id = b.event_id
        {where}
        ORDER BY e.timestamp DESC
    """
    try:
        all_rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []

    # Filter by model and keyword in Python
    model_set = set(models) if models else None
    filtered = []
    for r in all_rows:
        d = dict(r)
        try:
            p = resolve_payload(d)
        except Exception:
            p = None
        d["payload_obj"] = p if isinstance(p, dict) else {}
        if model_set:
            m = d["payload_obj"].get("layer_llm", {}).get("model")
            if m not in model_set:
                continue
        if keyword:
            text = json.dumps(d["payload_obj"], ensure_ascii=False)
            if keyword not in text:
                continue
        if not d.get("integrity"):
            score = d.get("integrity_score", 0) or 0
            d["integrity"] = (
                "verified"
                if score >= 0.9
                else ("rebooted" if score >= 0.5 else "corrupted")
            )
        if not d.get("_ingest_channel"):
            d["_ingest_channel"] = "hooks"
        d["model"] = (
            d["payload_obj"].get("layer_llm", {}).get("model")
            if isinstance(d["payload_obj"], dict)
            else None
        )
        filtered.append(d)

    events = filtered[offset : offset + limit]

    conn.close()
    return events


def get_distinct_models() -> list[str]:
    """获取所有 LLM 模型列表"""
    if not DB.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.OperationalError:
        return []

    try:
        rows = conn.execute(
            "SELECT e.storage_tier, e.payload, b.payload_blob "
            "FROM events e LEFT JOIN events_blob b ON e.id = b.event_id "
            "WHERE e.event_type = 'llm_invoke'"
        ).fetchall()
        models = set()
        for r in rows:
            p = resolve_payload(dict(r))
            if not isinstance(p, dict):
                continue
            m = (p.get("layer_llm") or {}).get("model")
            if m:
                models.add(m)
        return sorted(models)
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
