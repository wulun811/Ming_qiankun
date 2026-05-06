# server.py —— v0.11.10 前端插件 HTTP 服务
# 职责：提供静态文件服务 + /api/data.json 端点
# 依赖：标准库 only（http.server, json, webbrowser, threading）
# 用法:
#   前台调试: python server.py [--port 8080]
#   后台运行: python server.py --daemon [--port 8080]

import http.server, json, webbrowser, threading, time, os, sys, signal, socket, subprocess, importlib
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

KNOWN_PROBES = {"tusunsun", "langchain", "openclaw", "mingjing", "opencode"}


def _is_known(s):
    return any(s == name or s.startswith(name + "_") for name in KNOWN_PROBES)


WEB_DIR = Path.home() / ".ming" / "web"
DATA_FILE = WEB_DIR / "data.json"
PID_FILE = Path.home() / ".ming" / "web.pid"
INDEX_FILE = Path(__file__).parent / "index.html"

# P0-5: 安全扩展名白名单
SAFE_EXTENSIONS = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css",
    ".js": "application/javascript",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".json": "application/json",
}

# 延迟导入 web_exporter，避免启动时依赖问题
_web_exporter = None


def _get_exporter():
    global _web_exporter
    if _web_exporter is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "web_exporter", Path(__file__).parent / "web_exporter.py"
        )
        _web_exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_web_exporter)
        sys.modules["web_exporter"] = _web_exporter
    return _web_exporter


# v0.11.9m: 统一查询入口支持的查询名
AVAILABLE_QUERIES = [
    "token-breakdown",
    "token-spike",
    "tool-dangerous",
    "step-sequence",
    "step-loop",
    "memory-retrieve",
    "event-stats",
    "system-stats",
    "agent-status",
    "event-timeline",
]


class MingjingHandler(http.server.BaseHTTPRequestHandler):
    auth_token = None  # P0-6: 可选的 token 认证

    def _check_auth(self) -> bool:
        """P0-6: 验证 token（如果设置了）"""
        if not self.auth_token:
            return True
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:] == self.auth_token
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        token_param = params.get("token", [None])[0]
        if token_param:
            return token_param == self.auth_token
        return False

    def do_GET(self):
        # P0-6: 认证检查
        if not self._check_auth():
            self.send_error(401, "Unauthorized")
            return

        parsed = urlparse(self.path)
        if parsed.path == "/api/data.json":
            self._serve_json()
        elif parsed.path == "/api/events":
            self._serve_events(parsed)
        elif parsed.path == "/api/models":
            self._serve_models()
        elif parsed.path == "/api/config":
            self._serve_config()
        elif parsed.path == "/api/health-reset-status":
            self._serve_health_reset_status(parsed)
        elif parsed.path == "/ming/health":
            self._serve_ming_health()
        elif parsed.path == "/ming/diagnoses":
            self._serve_ming_diagnoses(parsed)
        elif parsed.path == "/ming/coverage":
            self._serve_ming_coverage(parsed)
        elif parsed.path == "/ming/tokens":
            self._serve_ming_tokens(parsed)
        elif parsed.path == "/ming/integrity":
            self._serve_ming_integrity(parsed)
        elif parsed.path == "/api/query":
            self._serve_unified_query(parsed)
        elif parsed.path == "/api/token/breakdown":
            self._serve_token_breakdown(parsed)
        elif parsed.path == "/api/token/spike":
            self._serve_token_spike(parsed)
        elif parsed.path == "/api/tool/audit":
            self._serve_tool_audit(parsed)
        elif parsed.path == "/api/agent/status":
            self._serve_agent_status()
        elif parsed.path == "/api/events/timeline":
            self._serve_event_timeline(parsed)
        elif parsed.path == "/" or parsed.path == "/index.html":
            self._serve_file(INDEX_FILE, "text/html; charset=utf-8")
        else:
            filepath = WEB_DIR / parsed.path.lstrip("/")
            # P0-5: 路径遍历防护
            if not filepath.resolve().is_relative_to(WEB_DIR.resolve()):
                self.send_error(403, "Forbidden")
                return
            if not filepath.exists() or not filepath.is_file():
                self.send_error(404)
                return
            # P0-5: 扩展名白名单检查
            ext = filepath.suffix.lower()
            if ext not in SAFE_EXTENSIONS:
                self.send_error(403, "Forbidden")
                return
            self._serve_file(filepath, SAFE_EXTENSIONS[ext])

    def do_POST(self):
        if not self._check_auth():
            self.send_error(401, "Unauthorized")
            return
        parsed = urlparse(self.path)
        if parsed.path == "/api/dismiss-alert":
            self._handle_dismiss_alert()
        elif parsed.path == "/api/dismiss-disease":
            self._handle_dismiss_disease()
        elif parsed.path == "/api/restore-disease":
            self._handle_restore_disease()
        elif parsed.path == "/api/archive-disease":
            self._handle_archive_disease()
        elif parsed.path == "/api/health-reset":
            self._handle_health_reset()
        elif parsed.path == "/api/exclude":
            self._handle_exclude()
        else:
            self.send_error(404, "Not Found")

    def _handle_dismiss_alert(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            self._send_json(400, {"error": "invalid JSON"})
            return
        alert_key = body.get("alert_key")
        if not alert_key or not isinstance(alert_key, str) or len(alert_key) > 64:
            self._send_json(400, {"error": "invalid alert_key"})
            return
        import sqlite3

        dismissed_path = Path.home() / ".ming" / "dismissed_alerts.json"
        try:
            if dismissed_path.exists():
                data = json.loads(dismissed_path.read_text(encoding="utf-8"))
            else:
                data = {}
            keys = data.get("alert_keys", [])
            if alert_key not in keys:
                keys.append(alert_key)
                data["alert_keys"] = keys
            dismissed_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # 立即重新导出 data.json，让下一次刷新就能看到效果
            _get_exporter().export()
            self._send_json(200, {"status": "dismissed", "alert_key": alert_key})
        except OSError:
            self._send_json(500, {"error": "failed to write dismissed_alerts.json"})

    def _handle_dismiss_disease(self):
        """忽略某实例的某疾病 (key = fault_id:display_name)"""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            self._send_json(400, {"error": "invalid JSON"})
            return
        fault_id = body.get("fault_id")
        display_name = body.get("display_name")
        expires_hours = body.get("expires_hours", 24)
        if not fault_id or not display_name:
            self._send_json(400, {"error": "fault_id and display_name required"})
            return

        key = f"{fault_id}:{display_name}"
        dismissed_path = Path.home() / ".ming" / "dismissed_diseases.json"
        try:
            if dismissed_path.exists():
                data = json.loads(dismissed_path.read_text(encoding="utf-8"))
            else:
                data = {}
            data[key] = {
                "dismissed_at": time.time(),
                "expires_at": time.time() + expires_hours * 3600,
            }
            dismissed_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _get_exporter().export()
            self._send_json(200, {"status": "dismissed", "key": key})
        except OSError:
            self._send_json(500, {"error": "failed to write dismissed_diseases.json"})

    def _handle_restore_disease(self):
        """恢复被忽略/归档的疾病"""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            self._send_json(400, {"error": "invalid JSON"})
            return
        fault_id = body.get("fault_id")
        display_name = body.get("display_name")
        if not fault_id or not display_name:
            self._send_json(400, {"error": "fault_id and display_name required"})
            return

        key = f"{fault_id}:{display_name}"
        restored = False

        # 从已忽略中恢复
        dismissed_path = Path.home() / ".ming" / "dismissed_diseases.json"
        try:
            if dismissed_path.exists():
                data = json.loads(dismissed_path.read_text(encoding="utf-8"))
            else:
                data = {}
            if key in data:
                del data[key]
                dismissed_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                restored = True
        except OSError:
            pass

        # 从已归档中恢复
        archived_path = Path.home() / ".ming" / "archived_diseases.json"
        try:
            if archived_path.exists():
                data = json.loads(archived_path.read_text(encoding="utf-8"))
            else:
                data = {}
            if key in data:
                del data[key]
                archived_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                restored = True
        except OSError:
            pass

        if restored:
            _get_exporter().export()
            self._send_json(200, {"status": "restored", "key": key})
        else:
            self._send_json(404, {"error": "key not found"})

    def _handle_archive_disease(self):
        """归档某实例的某疾病 (key = fault_id:display_name)，隐藏到折叠区"""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            self._send_json(400, {"error": "invalid JSON"})
            return
        fault_id = body.get("fault_id")
        display_name = body.get("display_name")
        if not fault_id or not display_name:
            self._send_json(400, {"error": "fault_id and display_name required"})
            return

        key = f"{fault_id}:{display_name}"
        archived_path = Path.home() / ".ming" / "archived_diseases.json"
        try:
            if archived_path.exists():
                data = json.loads(archived_path.read_text(encoding="utf-8"))
            else:
                data = {}
            data[key] = {"archived_at": time.time()}
            archived_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _get_exporter().export()
            self._send_json(200, {"status": "archived", "key": key})
        except OSError:
            self._send_json(500, {"error": "failed to write archived_diseases.json"})

    def _handle_health_reset(self):
        """健康复位：将某实例强制设为健康，新病情出现后自动失效"""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            self._send_json(400, {"error": "invalid JSON"})
            return
        display_name = body.get("display_name")
        if not display_name:
            self._send_json(400, {"error": "display_name required"})
            return

        resets_path = Path.home() / ".ming" / "health_resets.json"
        try:
            if resets_path.exists():
                data = json.loads(resets_path.read_text(encoding="utf-8"))
            else:
                data = {}
            data[display_name] = time.time()
            resets_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            _get_exporter().export()
            self._send_json(
                200,
                {
                    "status": "reset",
                    "display_name": display_name,
                    "reset_at": data[display_name],
                },
            )
        except OSError:
            self._send_json(500, {"error": "failed to write health_resets.json"})

    def _handle_exclude(self):
        """暂停/恢复观察某实例（探针端文件信号）"""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
        except Exception:
            self._send_json(400, {"error": "invalid JSON"})
            return
        action = body.get("action")
        system = body.get("system")
        if action not in ("add", "remove") or not system:
            self._send_json(400, {"error": "action (add/remove) and system required"})
            return
        try:
            paused_dir = Path.home() / ".ming" / ".paused"
            paused_dir.mkdir(parents=True, exist_ok=True)
            paused_file = paused_dir / system
            if action == "add":
                paused_file.write_text("")
                # 同步更新旧版 excluded_systems.json（archiver 兜底）
                self._sync_excluded_json(system, "add")
            else:
                try:
                    paused_file.unlink()
                except FileNotFoundError:
                    pass
                self._sync_excluded_json(system, "remove")
            _get_exporter().export()
            self._send_json(200, {"status": "ok", "action": action, "system": system})
        except OSError:
            self._send_json(500, {"error": "failed to update paused marker"})

    @staticmethod
    def _sync_excluded_json(system, action):
        """保持 excluded_systems.json 与 paused 文件同步（旧版兼容）"""
        p = Path.home() / ".ming" / "excluded_systems.json"
        try:
            if p.exists():
                data = json.loads(p.read_text())
            else:
                data = {"systems": []}
            systems = set(data.get("systems", []))
            if action == "add":
                systems.add(system)
            else:
                systems.discard(system)
            data["systems"] = sorted(systems)
            p.write_text(json.dumps(data, ensure_ascii=False))
        except OSError:
            pass

    def _serve_health_reset_status(self, parsed):
        """查询某实例的健康复位状态"""
        params = parse_qs(parsed.query)
        display_name = params.get("display_name", [None])[0]
        if not display_name:
            self._send_json(400, {"error": "display_name required"})
            return

        resets_path = Path.home() / ".ming" / "health_resets.json"
        try:
            if resets_path.exists():
                data = json.loads(resets_path.read_text(encoding="utf-8"))
            else:
                data = {}
            reset_ts = data.get(display_name)
            self._send_json(
                200,
                {
                    "display_name": display_name,
                    "has_reset": reset_ts is not None,
                    "reset_at": reset_ts,
                },
            )
        except Exception:
            self._send_json(500, {"error": "failed to read health_resets.json"})

    def _serve_json(self):
        if DATA_FILE.exists():
            self._serve_file(DATA_FILE, "application/json")
        else:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b'{"error": "data.json not generated yet"}')

    def _serve_events(self, parsed):
        try:
            params = parse_qs(parsed.query)

            # P1-6: 参数验证
            def _parse_float_param(params, name):
                if name not in params:
                    return None
                try:
                    return float(params[name][0])
                except (ValueError, IndexError):
                    return None

            def _parse_int_param(params, name, default=50, min_val=1, max_val=1000):
                if name not in params:
                    return default
                try:
                    val = int(params[name][0])
                    return max(min_val, min(val, max_val))
                except (ValueError, IndexError):
                    return default

            def _parse_model_param(params):
                if "model" not in params or not params["model"][0]:
                    return None
                return params["model"][0].split(",")

            since = _parse_float_param(params, "since")
            if since is None and "since" in params:
                self._send_json(400, {"error": "invalid 'since' parameter"})
                return

            until = _parse_float_param(params, "until")
            if until is None and "until" in params:
                self._send_json(400, {"error": "invalid 'until' parameter"})
                return

            limit = _parse_int_param(
                params, "limit", default=50, min_val=1, max_val=1000
            )
            offset = _parse_int_param(params, "offset", default=0, min_val=0)

            exporter = _get_exporter()
            events = exporter.query_events(
                system=params.get("system", [None])[0],
                event_type=params.get("event_type", [None])[0],
                since=since,
                until=until,
                models=_parse_model_param(params),
                keyword=params.get("keyword", [None])[0],
                limit=limit,
                offset=offset,
            )
            self._send_json(200, {"events": events, "count": len(events)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _serve_models(self):
        try:
            exporter = _get_exporter()
            models = exporter.get_distinct_models()
            self._send_json(200, {"models": models})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _serve_config(self):
        home = str(Path.home())
        config = {
            "batch_size": int(os.getenv("WQ_ARCHIVER_BATCH_SIZE", "1000")),
            "flush_sec": float(os.getenv("WQ_ARCHIVER_FLUSH_SEC", "1.0")),
            "vacuum_hours": int(os.getenv("WQ_ARCHIVER_VACUUM_HOURS", "24")),
            "hot_dir": os.getenv("MING_HOT_DIR", f"{home}/.ming/hot"),
            "cold_dir": str(Path.home() / ".ming" / "cold"),
            "sqlite_path": str(Path.home() / ".ming" / "ming.db"),
        }
        self._send_json(200, config)

    def _read_data(self):
        if not DATA_FILE.exists():
            return None
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _filter_system(self, data, query):
        """从 parsed query 提取 system 参数，过滤列表"""
        params = parse_qs(query)
        target = params.get("system", [None])[0]
        if not target or not data:
            return data
        if isinstance(data, list):
            return [x for x in data if x.get("system") == target]
        return data

    def _serve_ming_health(self):
        data = self._read_data()
        if not data:
            self._send_json(503, {"error": "data.json 未就绪"})
            return
        self._send_json(
            200,
            {
                "self_health": data.get("self_health", {}),
                "archiver_alive": data.get("archiver_alive", False),
                "archiver_age_seconds": data.get("archiver_age_seconds", -1),
                "capacity": data.get("capacity", {}),
                "integrity_stats": data.get("integrity_stats", {}),
                "exported_at": data.get("exported_at"),
            },
        )

    def _serve_ming_diagnoses(self, parsed):
        data = self._read_data()
        if not data:
            self._send_json(503, {"error": "data.json 未就绪"})
            return
        cards = data.get("instance_cards", [])
        if not cards:
            self._send_json(200, {"diagnoses": []})
            return

        params = parse_qs(parsed.query)
        target = params.get("system", [None])[0]
        results = []
        for c in cards:
            if target and c.get("system") != target:
                continue
            for dx in c.get("diagnoses", []):
                results.append(
                    {
                        "system": c.get("system"),
                        "fault_id": dx.get("fault_id"),
                        "name": dx.get("diagnosis_name"),
                        "severity": dx.get("severity"),
                        "confidence": dx.get("confidence"),
                        "first_seen": dx.get("first_seen"),
                        "last_seen": dx.get("last_seen"),
                        "occurrence_count": dx.get("occurrence_count"),
                        "status": dx.get("status"),
                    }
                )
            for dx in c.get("archived_diagnoses", []):
                results.append(
                    {
                        "system": c.get("system"),
                        "fault_id": dx.get("fault_id"),
                        "name": dx.get("diagnosis_name"),
                        "severity": dx.get("severity"),
                        "confidence": dx.get("confidence"),
                        "first_seen": dx.get("first_seen"),
                        "last_seen": dx.get("last_seen"),
                        "occurrence_count": dx.get("occurrence_count"),
                        "status": "archived",
                    }
                )
        self._send_json(
            200,
            {
                "diagnoses": results,
                "count": len(results),
            },
        )

    def _serve_ming_coverage(self, parsed):
        data = self._read_data()
        if not data:
            self._send_json(503, {"error": "data.json 未就绪"})
            return
        cards = data.get("instance_cards", [])
        params = parse_qs(parsed.query)
        target = params.get("system", [None])[0]
        results = []
        for c in cards:
            if target and c.get("system") != target:
                continue
            results.append(
                {
                    "system": c.get("system"),
                    "triage_ready": c.get("triage_ready"),
                    "triage_total": c.get("triage_total"),
                    "health_level": c.get("health_level"),
                    "health_level_label": c.get("health_level_label"),
                }
            )
        self._send_json(
            200,
            {
                "coverage": results,
                "global": data.get("triage", {}),
            },
        )

    def _serve_ming_tokens(self, parsed):
        data = self._read_data()
        if not data:
            self._send_json(503, {"error": "data.json 未就绪"})
            return
        token_by_system = self._filter_system(
            data.get("token_by_system", []), parsed.query
        )
        self._send_json(
            200,
            {
                "token_by_system": token_by_system,
                "token_by_model": data.get("token_by_model", []),
                "latency_stats": data.get("latency_stats", {}),
                "call_freq": data.get("call_freq", []),
            },
        )

    def _serve_ming_integrity(self, parsed):
        data = self._read_data()
        if not data:
            self._send_json(503, {"error": "data.json 未就绪"})
            return
        params = parse_qs(parsed.query)
        target = params.get("system", [None])[0]

        # 按系统过滤最近事件中的完整性信息
        events = data.get("recent_events", [])
        if target:
            events = [e for e in events if e.get("system") == target]

        self._send_json(
            200,
            {
                "integrity_stats": data.get("integrity_stats", {}),
                "recent_integrity": [
                    {
                        "system": e.get("system"),
                        "event_type": e.get("event_type"),
                        "integrity": e.get("integrity"),
                        "integrity_score": e.get("integrity_score"),
                        "timestamp": e.get("timestamp"),
                    }
                    for e in events[:50]
                ],
            },
        )

    def _bridge(self):
        """延迟加载 QueryBridge，避免启动时依赖 DB 不存在"""
        import importlib.util

        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        if not hasattr(self, "_bridge_cache"):
            spec = importlib.util.spec_from_file_location(
                "query_bridge", Path(__file__).parent.parent.parent / "query_bridge.py"
            )
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            self._bridge_cache = mod.QueryBridge()
        return self._bridge_cache

    def _serve_unified_query(self, parsed):
        """统一查询入口 GET /api/query?name=X&params=..."""
        params = parse_qs(parsed.query)
        name = params.get("name", [None])[0]
        if not name:
            self._send_json(
                400,
                {
                    "error": "missing 'name' parameter",
                    "available": AVAILABLE_QUERIES,
                },
            )
            return
        try:
            bridge = self._bridge()
            system = params.get("system", [None])[0]
            since = float(params["since"][0]) if "since" in params else None
            limit = int(params.get("limit", [50])[0])

            if name == "token-breakdown":
                group_by = params.get("group_by", ["model"])[0]
                result = bridge.query_token_breakdown(system, since, group_by)
            elif name == "token-spike":
                w = int(params.get("window", [300])[0])
                t = float(params.get("threshold", [3.0])[0])
                result = bridge.query_token_spike(system, w, t)
            elif name == "tool-dangerous":
                result = bridge.query_tool_dangerous(system, since)
            elif name == "step-sequence":
                sid = params.get("session_id", [None])[0]
                result = bridge.query_step_sequence(system, sid, since, limit)
            elif name == "step-loop":
                window = int(params.get("window", [600])[0])
                result = bridge.query_step_loop(system, window)
            elif name == "memory-retrieve":
                result = bridge.query_memory_retrieve(system, since, limit)
            elif name == "agent-status":
                result = bridge.query_agent_status()
            elif name == "event-timeline":
                result = bridge.query_event_timeline(system, since, limit)
            elif name == "event-stats":
                result = self._get_event_stats(limit)
            elif name == "system-stats":
                result = self._get_system_stats(limit)
            else:
                self._send_json(
                    400,
                    {
                        "error": f"unknown query name: {name}",
                        "available": AVAILABLE_QUERIES,
                    },
                )
                return
            count = len(result) if isinstance(result, list) else 1
            self._send_json(200, {"query": name, "results": result, "count": count})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _serve_token_breakdown(self, parsed):
        """快捷端点 GET /api/token/breakdown"""
        params = parse_qs(parsed.query)
        try:
            bridge = self._bridge()
            system = params.get("system", [None])[0]
            since = float(params["since"][0]) if "since" in params else None
            group_by = params.get("group_by", ["model"])[0]
            result = bridge.query_token_breakdown(system, since, group_by)
            self._send_json(200, {"results": result, "count": len(result)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _serve_token_spike(self, parsed):
        """快捷端点 GET /api/token/spike"""
        params = parse_qs(parsed.query)
        try:
            bridge = self._bridge()
            system = params.get("system", [None])[0]
            window = int(params.get("window", [300])[0])
            threshold = float(params.get("threshold", [3.0])[0])
            result = bridge.query_token_spike(system, window, threshold)
            self._send_json(200, {"results": result, "count": len(result)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _serve_tool_audit(self, parsed):
        """快捷端点 GET /api/tool/audit"""
        params = parse_qs(parsed.query)
        try:
            bridge = self._bridge()
            system = params.get("system", [None])[0]
            since = float(params["since"][0]) if "since" in params else None
            result = bridge.query_tool_dangerous(system, since)
            self._send_json(200, {"results": result, "count": len(result)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _serve_agent_status(self):
        """快捷端点 GET /api/agent/status"""
        try:
            bridge = self._bridge()
            result = bridge.query_agent_status()
            self._send_json(200, {"results": result, "count": len(result)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _serve_event_timeline(self, parsed):
        """快捷端点 GET /api/events/timeline"""
        params = parse_qs(parsed.query)
        try:
            bridge = self._bridge()
            system = params.get("system", [None])[0]
            since = float(params["since"][0]) if "since" in params else None
            limit = int(params.get("limit", [200])[0])
            result = bridge.query_event_timeline(system, since, min(limit, 1000))
            self._send_json(200, {"results": result, "count": len(result)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})

    def _get_event_stats(self, limit=50):
        """事件类型统计（供统一入口复用）"""
        bridge = self._bridge()
        sql = "SELECT event_type, COUNT(*) as cnt FROM events GROUP BY event_type ORDER BY cnt DESC LIMIT ?"
        rows = bridge._conn.execute(sql, (limit,)).fetchall()
        return [{"event_type": r[0], "count": r[1]} for r in rows]

    def _get_system_stats(self, limit=50):
        """系统事件统计（供统一入口复用）"""
        bridge = self._bridge()
        sql = "SELECT system, COUNT(*) as cnt FROM events GROUP BY system ORDER BY cnt DESC LIMIT ?"
        rows = bridge._conn.execute(sql, (limit,)).fetchall()
        return [{"system": r[0], "count": r[1]} for r in rows if _is_known(r[0])]

    def _send_json(self, status_code, data):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _serve_file(self, filepath, mime):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.end_headers()
        self.wfile.write(filepath.read_bytes())

    def log_message(self, format, *args):
        # P3-2: 记录 error 级别日志
        if args and len(args) >= 2:
            status_code = args[1]
            try:
                if int(status_code) >= 400:
                    sys.stderr.write(
                        f"[WEB-ERR] {self.address_string()} - {format % args}\n"
                    )
                    sys.stderr.flush()
            except (ValueError, IndexError):
                pass


def _check_port(host: str, port: int) -> bool:
    """检查端口是否可用"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _run_server(host: str, port: int, token: str = None, open_browser: bool = True):
    """P1-4: 运行 HTTP 服务（阻塞调用，多线程）"""
    WEB_DIR.mkdir(parents=True, exist_ok=True)

    exporter = _get_exporter()

    def _run_export_loop(interval_sec=30):
        """后台定时导出，每次重新加载以确保代码变更生效"""
        while True:
            time.sleep(interval_sec)
            try:
                mod = _get_exporter()
                mod.__spec__.loader.exec_module(mod)
                mod.export()
            except Exception as e:
                sys.stderr.write(f"[WEB-EXP] export failed: {e}\n")
                sys.stderr.flush()

    threading.Thread(target=_run_export_loop, args=(30,), daemon=True).start()
    # 立即跑一次初始导出
    try:
        exporter.export()
    except Exception as e:
        sys.stderr.write(f"[WEB-EXP] initial export failed: {e}\n")
        sys.stderr.flush()

    server = ThreadingHTTPServer((host, port), MingjingHandler)
    if token:
        MingjingHandler.auth_token = token
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"
    print(f"目镜服务启动: {url}")
    if open_browser:
        threading.Thread(
            target=lambda: (time.sleep(0.5), webbrowser.open(url)), daemon=True
        ).start()

    def _handle_signal(signum, frame):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        server.serve_forever()
    finally:
        PID_FILE.unlink(missing_ok=True)


def serve(
    host: str = "127.0.0.1",
    port: int = 18088,
    token: str = None,
    open_browser: bool = True,
):
    """前台模式"""
    if not _check_port(host, port):
        print(f"错误: 端口 {port} 已被占用")
        sys.exit(1)
    _run_server(host, port, token, open_browser)


def serve_daemon(host: str = "127.0.0.1", port: int = 8080, token: str = None):
    """后台模式（跨平台）"""
    if not _check_port(host, port):
        print(f"错误: 端口 {port} 已被占用")
        sys.exit(1)

    cmd = [
        sys.executable,
        __file__,
        "--host",
        host,
        "--port",
        str(port),
        "--no-browser",
    ]
    if token:
        cmd.extend(["--token", token])
    proc = subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    time.sleep(0.5)
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid))
    display_host = host if host != "0.0.0.0" else "127.0.0.1"
    print(f"目镜服务已启动: http://{display_host}:{port} (PID={proc.pid})")
    print(f"停止: python server.py --stop")


def cmd_stop():
    """停止后台服务"""
    if not PID_FILE.exists():
        print("目镜服务未运行")
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        for _ in range(20):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            try:
                os.kill(pid, signal.SIGKILL)
                time.sleep(0.5)
            except OSError:
                pass
        PID_FILE.unlink(missing_ok=True)
        print(f"目镜服务已停止 (PID={pid})")
    except ProcessLookupError:
        print("目镜服务进程已不存在")
        PID_FILE.unlink(missing_ok=True)
    except Exception as e:
        print(f"停止失败: {e}")
        PID_FILE.unlink(missing_ok=True)


def cmd_status():
    """查看后台服务状态"""
    if not PID_FILE.exists():
        print("目镜服务: 未启动")
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)
        host = os.getenv("MING_WEB_HOST", "127.0.0.1")
        display_host = host if host != "0.0.0.0" else "127.0.0.1"
        print(f"目镜服务: 运行中 (PID={pid})")
        print(f"访问地址: http://{display_host}:{os.getenv('MING_WEB_PORT', '18088')}")
    except (ValueError, OSError):
        print("目镜服务: 已停止 (PID 文件残留)")
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host", type=str, default="127.0.0.1", help="绑定地址 (默认 127.0.0.1)"
    )
    parser.add_argument("--port", type=int, default=18088)
    parser.add_argument("--token", type=str, default=None, help="API 认证令牌（可选）")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--daemon", action="store_true", help="后台运行")
    parser.add_argument("--stop", action="store_true", help="停止后台服务")
    parser.add_argument("--status", action="store_true", help="查看服务状态")
    args = parser.parse_args()

    if args.stop:
        cmd_stop()
    elif args.status:
        cmd_status()
    elif args.daemon:
        serve_daemon(host=args.host, port=args.port, token=args.token)
    else:
        serve(
            host=args.host,
            port=args.port,
            token=args.token,
            open_browser=not args.no_browser,
        )
