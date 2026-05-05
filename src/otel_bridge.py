# otel_bridge.py —— v0.11.9m OTEL/JSON Receiver
# 职责：接收 OTLP/JSON Span，翻译为乾坤镜事件写入热轨
# 依赖：纯 Python 标准库，零第三方依赖
# 启动：python src/otel_bridge.py

import json, os, time, threading, signal, sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

try:
    import fcntl

    _HAS_FCNTL = True
except ImportError:
    _HAS_FCNTL = False

HOT_DIR = os.path.expanduser(os.getenv("MING_HOT_DIR", "~/.ming/hot"))
OTEL_PORT = int(os.getenv("MING_OTEL_PORT", "4319"))
PID = os.getpid()
_seq = 0
_seq_lock = threading.Lock()


def unwrap(v):
    if not isinstance(v, dict):
        return v
    for k in ("stringValue", "bytesValue"):
        if k in v:
            return v[k]
    for k in ("intValue", "doubleValue"):
        if k in v:
            return int(v[k]) if k == "intValue" else float(v[k])
    if "boolValue" in v:
        return v["boolValue"]
    if "arrayValue" in v:
        return [unwrap(x) for x in v.get("arrayValue", {}).get("values", [])]
    if "kvlistValue" in v:
        return {
            x["key"]: unwrap(x["value"])
            for x in v.get("kvlistValue", {}).get("values", [])
        }
    return v


def extract_attrs(raw):
    return {a["key"]: unwrap(a["value"]) for a in raw} if raw else {}


def map_span(span, r_attrs):
    attrs = extract_attrs(span.get("attributes", []))
    name, sc = span.get("name", ""), span.get("status", {}).get("code", 0)
    s, e = (
        int(span.get("startTimeUnixNano", "0")),
        int(span.get("endTimeUnixNano", "0")),
    )
    dur = round((e - s) / 1e6, 2) if e and s else 0
    sys_name = r_attrs.get("service.name", "otel_unknown")
    p = {
        "layer_agent": {
            "trace_id": span.get("traceId", "")[:16],
            "span_id": span.get("spanId", "")[:8],
            "agent_name": sys_name,
        },
        "_ingest_channel": "otel",
    }
    et = None
    if attrs.get("gen_ai.system") or "llm" in name.lower() or "chat" in name.lower():
        et = "llm_invoke"
        p["layer_llm"] = {
            "model": attrs.get("gen_ai.response.model"),
            "input_tokens": attrs.get("gen_ai.usage.input_tokens"),
            "output_tokens": attrs.get("gen_ai.usage.output_tokens"),
            "latency_ms": dur,
            "finish_reason": attrs.get("gen_ai.response.finish_reason"),
            "temperature": attrs.get("gen_ai.request.temperature"),
        }
    elif attrs.get("tool.name") or "tool" in name.lower():
        et = "tool_call"
        p["layer_tool"] = {
            "tool_name": attrs.get("tool.name", name),
            "tool_status": "success" if sc == 1 else "fail",
            "execution_ms": dur,
            "tool_args": attrs.get("tool.input"),
        }
    elif (
        attrs.get("db.system") or "memory" in name.lower() or "retriev" in name.lower()
    ):
        et = "memory_retrieve"
        p["layer_memory"] = {
            "memory_store": attrs.get("db.system"),
            "results_count": attrs.get("db.response.returned_rows"),
            "latency_ms": dur,
        }
    elif sc == 2:
        et = "error"
        p["error_type"] = attrs.get("error.type", "unknown")
        p["error_msg"] = attrs.get("exception.message", "")
        p["stack_trace"] = attrs.get("exception.stacktrace", "")
    if et is None:
        return None
    if "http.status_code" in attrs or "server.address" in attrs:
        p["layer_network"] = {
            "status_code": attrs.get("http.status_code"),
            "target_host": attrs.get("server.address"),
        }
    return {
        "system": sys_name,
        "mode": "black",
        "event_type": et,
        "payload": p,
        "timestamp": time.time(),
        "monotonic_ms": time.monotonic() * 1000,
        "_pid": PID,
        "_schema_version": "0.11.9m",
        "_source": "otel_bridge",
    }


def write_event(ev):
    os.makedirs(HOT_DIR, exist_ok=True)
    global _seq
    with _seq_lock:
        _seq += 1
        seq = _seq
    ts = time.strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(HOT_DIR, f"otel_bridge_{ts}_{PID}_{seq:06d}.jsonl")
    try:
        if _HAS_FCNTL:
            with open(filepath, "a", encoding="utf-8") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
                fcntl.flock(f, fcntl.LOCK_UN)
        else:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass


def emit_meta(event_type, payload):
    ev = {
        "system": "otel_bridge",
        "mode": "black",
        "event_type": event_type,
        "payload": payload,
        "timestamp": time.time(),
        "monotonic_ms": time.monotonic() * 1000,
        "_pid": PID,
        "_schema_version": "0.11.9m",
        "_source": "otel_bridge",
    }
    write_event(ev)


OTEL_AUTH_TOKEN = os.getenv("MING_OTEL_TOKEN") or None


class OTLPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if OTEL_AUTH_TOKEN:
            auth = self.headers.get("Authorization", "")
            if not (auth == f"Bearer {OTEL_AUTH_TOKEN}" or auth == OTEL_AUTH_TOKEN):
                self.send_error(401, "Unauthorized")
                return
        if self.path != "/v1/traces":
            self.send_error(404)
            return
        ct = self.headers.get("Content-Type", "")
        if "protobuf" in ct:
            self.send_response(415)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"JSON only")
            return
        try:
            data = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
            )
        except Exception:
            self.send_error(400, "JSON only")
            return
        for rs in data.get("resourceSpans", []):
            ra = extract_attrs(rs.get("resource", {}).get("attributes", []))
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    ev = map_span(sp, ra)
                    if ev:
                        write_event(ev)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"partialSuccess":{}}')

    def log_message(self, *a):
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    bind_host = os.getenv("MING_OTEL_BIND", "127.0.0.1")
    emit_meta("__register__", {"pid": PID, "mode": "black", "port": OTEL_PORT})
    server = ThreadedHTTPServer((bind_host, OTEL_PORT), OTLPHandler)
    print(f"[otel_bridge] OTLP/JSON on :{OTEL_PORT} → {HOT_DIR}")

    def shutdown(*_):
        emit_meta("__health__", {"pid": PID})
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()
