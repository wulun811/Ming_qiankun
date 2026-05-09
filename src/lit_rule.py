# lit_rule.py —— 0.11.9m 诊断规则引擎
# 职责：SQL 安全执行、YAML 解析、列提取、参数构建、分诊加载
import re
import json
import time
import os
from pathlib import Path
from archiver_compress import resolve_payload

TRIAGE_SNAPSHOT = Path.home() / ".ming" / "triage_snapshot.json"
DISEASES_YAML = Path(__file__).parent.parent / "config" / "diseases.yaml"
TRIAGE_MAX_AGE = 86400

SQL_BLACKLIST = re.compile(
    r"\b(JOIN|UNION|INTERSECT|EXCEPT|PRAGMA|INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|ATTACH|DETACH)\b",
    re.IGNORECASE,
)
SQL_COMMENT = re.compile(r"/\*.*?\*/|--", re.DOTALL)
SQL_SELECT_PREFIX = re.compile(r"^\s*SELECT\s+", re.IGNORECASE)

SEVERITY_WINDOW = {
    "P0": 300,
    "P1": 1800,
    "P2": 3600,
    "META": 300,
}

DEADLINE_RULES = {"AGT-049", "MEM-074"}

_last_triage_ts = 0.0

# ---- diseases.yaml 缓存 ----
_diseases_cache = None
_diseases_cache_mtime = 0.0


def set_incremental_since(ts: float):
    global _last_triage_ts
    _last_triage_ts = max(0.0, min(ts, time.time()))


def validate_sql(sql: str) -> tuple:
    if not SQL_SELECT_PREFIX.match(sql):
        return False, "SQL 必须以 SELECT 开头"
    if SQL_BLACKLIST.search(sql):
        return False, "SQL 包含禁止关键字（JOIN/UNION/PRAGMA/写操作）"
    if SQL_COMMENT.search(sql):
        return False, "SQL 不允许包含注释（/* */ 或 --）"
    return True, "ok"


def execute_safe_sql(conn, sql: str, params: tuple = (), timeout: int = 3) -> list:
    ok, msg = validate_sql(sql)
    if not ok:
        return [], msg
    conn.execute(f"PRAGMA busy_timeout={timeout * 1000}")
    try:
        return conn.execute(sql, params).fetchall(), "ok"
    except Exception as e:
        return [], str(e)


def load_triage_snapshot() -> dict:
    if not TRIAGE_SNAPSHOT.exists():
        return None
    try:
        data = json.loads(TRIAGE_SNAPSHOT.read_text())
        age = time.time() - data.get("generated_at", 0)
        if age > TRIAGE_MAX_AGE:
            return None
        return data.get("rule_status", {})
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def load_diseases_yaml() -> list:
    """零第三方依赖，使用内置 YAML 解析器（带 mtime 缓存）"""
    global _diseases_cache, _diseases_cache_mtime
    if not DISEASES_YAML.exists():
        return None
    try:
        mtime = DISEASES_YAML.stat().st_mtime
        if _diseases_cache is not None and mtime == _diseases_cache_mtime:
            return _diseases_cache
        result = _parse_yaml_simple()
        if result is not None:
            _diseases_cache = result
            _diseases_cache_mtime = mtime
        return result
    except Exception:
        return None


def _parse_yaml_simple() -> list:
    try:
        content = DISEASES_YAML.read_text(encoding="utf-8")
        diseases = []
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
                if current and "id" in current:
                    diseases.append(current)
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
        if current and "id" in current:
            diseases.append(current)
        return diseases
    except Exception:
        return None


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
    if val.startswith("{") and val.endswith("}"):
        try:
            return json.loads(val, parse_float=float)
        except json.JSONDecodeError:
            pass
    return val


def _extract_columns(sql: str) -> list:
    select_match = re.search(r"SELECT\s+(.+?)\s+FROM", sql, re.IGNORECASE | re.DOTALL)
    if not select_match:
        return []
    select_clause = select_match.group(1)
    parts = []
    depth = 0
    current = ""
    for char in select_clause:
        if char == "(":
            depth += 1
            current += char
        elif char == ")":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    columns = []
    for part in parts:
        part = part.strip()
        as_match = re.search(r"\s+as\s+(\w+)", part, re.IGNORECASE)
        if as_match:
            columns.append(as_match.group(1))
        else:
            dot_match = re.search(r"(\w+(?:\.\w+)?)$", part)
            if dot_match:
                columns.append(dot_match.group(1))
            else:
                columns.append(part.split(".")[-1].split("(")[-1].rstrip(")"))
    return columns


def _build_params(rule: dict, count: int) -> tuple:
    severity = rule.get("severity", "P2")
    window = SEVERITY_WINDOW.get(severity, 3600)
    now = time.time()
    rule_id = rule.get("id", "")

    since_env = os.environ.get("MING_DIAGNOSIS_SINCE")
    until_env = os.environ.get("MING_DIAGNOSIS_UNTIL")

    if since_env and until_env:
        since = float(since_env)
        until = float(until_env)
        if count == 1:
            return (since,)
        elif count == 2:
            return (since, until)
        return ()

    effective_since = max(_last_triage_ts, now - window)

    if count == 0:
        return ()
    elif count == 1:
        if rule_id in DEADLINE_RULES:
            return (now,)
        return (effective_since,)
    elif count == 2:
        if rule_id in DEADLINE_RULES:
            return (now - window, now)
        return (effective_since, effective_since)
    return ()


def _row_to_evidence(row, columns: list, rule: dict) -> dict:
    ev = {"summary": rule.get("description", ""), "key_fields": {}}
    for i, col in enumerate(columns):
        if i < len(row) and row[i] is not None:
            ev["key_fields"][col] = row[i]
            if col in ("system",):
                ev["system"] = row[i]
            if col in ("id", "event_id"):
                ev["event_id"] = row[i]
    return ev


def resolve_env_sql(sql: str, rule: dict, env_profile: dict) -> str:
    if "{env." not in sql:
        return sql
    th_all = rule.get("env_thresholds", {})
    if not th_all:
        return sql
    runtime = env_profile.get("runtime", "baremetal")
    th = th_all.get(runtime, th_all.get("baremetal", {}))
    for k, v in th.items():
        sql = sql.replace("{env." + k + "}", str(v))
    return sql


def resolve_per_system(rule: dict, system_name: str) -> dict:
    """Check per_system overrides for a given system.
    Returns {"enabled": True/False, "confidence_multiplier": float} with defaults."""
    result = {"enabled": True, "confidence_multiplier": 1.0}
    per = rule.get("per_system", {})
    if not per:
        return result
    cfg = None
    for key, val in per.items():
        if system_name == key or system_name.startswith(key + "_"):
            cfg = val
            break
    if cfg is None:
        return result
    if cfg.get("enabled") is False:
        result["enabled"] = False
    cm = cfg.get("confidence_multiplier")
    if cm is not None:
        result["confidence_multiplier"] = float(cm)
    return result


def _load_env_profile(conn) -> dict:
    try:
        row = conn.execute(
            "SELECT e.payload, e.storage_tier, b.payload_blob "
            "FROM events e LEFT JOIN events_blob b ON e.id = b.event_id "
            "WHERE e.event_type='platform_profile' "
            "ORDER BY e.timestamp DESC LIMIT 1"
        ).fetchone()
        if row:
            pl = resolve_payload(row)
            if pl:
                return pl
    except Exception:
        pass
    return {}
