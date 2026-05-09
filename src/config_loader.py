# config_loader.py —— v0.11.9m 配置加载器
# 职责：config.json 加载 + 注释预处理 + 校验 + 环境变量合并
# 纯标准库，零第三方依赖
import json, os
from pathlib import Path

DEFAULTS = {
    "version": "0.11.9m",
    "mode": "standalone",
    "hot_dir": "~/.ming/hot",
    "db_path": "~/.ming/ming.db",
    "archiver": {"batch_size": 1000, "flush_sec": 1.0, "vacuum_hours": 24},
    "watchdog": {"heartbeat_sec": 5, "restart_max": 3},
    "otel": {
        "enabled": True,
        "port": 4319,
        "exporter_endpoint": "http://localhost:4318/v1/logs",
        "export_limit": 100,
    },
    "privacy": {"default_mode": "black", "retention": {"by_system": {}}},
    "push": {"enabled": False, "host": "localhost", "port": 9002, "path": "/ming/push"},
    "logging": {"level": "info", "file": "~/.ming/ming.log"},
}

ENV_MAP = {
    "MING_MODE": "mode",
    "MING_HOT_DIR": "hot_dir",
    "MING_DB_PATH": "db_path",
    "WQ_ARCHIVER_BATCH_SIZE": ("archiver", "batch_size"),
    "WQ_ARCHIVER_FLUSH_SEC": ("archiver", "flush_sec"),
    "WQ_ARCHIVER_VACUUM_HOURS": ("archiver", "vacuum_hours"),
    "WQ_WATCHDOG_HEARTBEAT_SEC": ("watchdog", "heartbeat_sec"),
    "MING_OTEL_PORT": ("otel", "port"),
    "MING_OTEL_EXPORTER_ENDPOINT": ("otel", "exporter_endpoint"),
    "MING_LOG_LEVEL": ("logging", "level"),
}

RANGES = {
    "archiver.batch_size": (100, 5000),
    "archiver.flush_sec": (0.5, 10),
    "otel.port": (1024, 65535),
    "watchdog.heartbeat_sec": (1, 60),
}


def _strip_comments(text):
    """删除 JSON 中的 // 和 /* */ 注释（跳过字符串内的内容）"""
    result = []
    i = 0
    in_string = False
    escape = False
    while i < len(text):
        ch = text[i]
        if escape:
            result.append(ch)
            escape = False
            i += 1
            continue
        if in_string:
            result.append(ch)
            if ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < len(text):
            next_ch = text[i + 1]
            if next_ch == "/":
                # line comment: skip to end of line
                nl = text.find("\n", i)
                if nl == -1:
                    break
                i = nl
                continue
            elif next_ch == "*":
                # block comment: skip to */
                end = text.find("*/", i + 2)
                if end == -1:
                    break
                i = end + 2
                continue
        result.append(ch)
        i += 1
    return "".join(result)


def _find_config():
    """查找配置文件路径"""
    env_path = os.getenv("MING_CONFIG_FILE")
    if env_path and Path(env_path).exists():
        return Path(env_path), "env"
    user = Path.home() / ".ming" / "config.json"
    if user.exists():
        return user, "user"
    sys = Path("/etc/ming/config.json")  # 系统级配置（仅 Linux），可配合包管理使用
    if sys.exists():
        return sys, "system"
    return None, "default"


def _deep_merge(base, override):
    """深度合并字典"""
    result = base.copy()
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _get_nested(d, path):
    """获取嵌套字典值，支持字符串路径如 'archiver.batch_size'"""
    if isinstance(path, str):
        path = path.split(".")
    for key in path:
        if isinstance(d, dict):
            d = d.get(key)
        else:
            return None
    return d


def _set_nested(d, path, value):
    """设置嵌套字典值，支持字符串路径如 'archiver.batch_size'"""
    if isinstance(path, str):
        keys = path.split(".")
    else:
        keys = path if isinstance(path, tuple) else [path]
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value


def load_config():
    """加载配置：环境变量 > config.json > 默认值"""
    config_path, source = _find_config()
    file_config = {}
    if config_path:
        try:
            text = config_path.read_text(encoding="utf-8")
            cleaned = _strip_comments(text)
            file_config = json.loads(cleaned)
            print(f"[config] 加载 {config_path}")
        except json.JSONDecodeError as e:
            print(f"[config] 解析失败: {e}，使用默认值")
        except OSError:
            print(f"[config] 读取失败: {config_path}")
    # 合并默认值
    config = _deep_merge(DEFAULTS, file_config)
    # 环境变量覆盖
    overrides = {}
    for env_var, config_path in ENV_MAP.items():
        val = os.getenv(env_var)
        if val is not None:
            # 类型转换
            default_val = _get_nested(DEFAULTS, config_path)
            if isinstance(default_val, bool):
                val = val.lower() in ("true", "1", "yes")
            elif isinstance(default_val, (int, float)):
                try:
                    val = float(val) if "." in val else int(val)
                except (ValueError, TypeError):
                    print(f"[config] 环境变量 {env_var}={val!r} 非数字，忽略")
                    continue
            _set_nested(overrides, config_path, val)
            print(f"[config] 环境变量 {env_var} 覆盖 {config_path}")
    config = _deep_merge(config, overrides)
    # 校验
    errors = validate(config)
    for err in errors:
        print(f"[config] 警告: {err}")
    # 路径展开
    for key in ("hot_dir", "db_path"):
        val = _get_nested(config, key)
        if val and val.startswith("~"):
            _set_nested(config, key, str(Path(val).expanduser()))
    return config, source, errors


def validate(config):
    """校验配置值范围"""
    errors = []
    for path, (min_val, max_val) in RANGES.items():
        val = _get_nested(config, path)
        if val is not None:
            if not isinstance(val, (int, float)):
                errors.append(f"{path}={val!r} 类型错误，应为数字")
                continue
            if not (min_val <= val <= max_val):
                errors.append(f"{path}={val} 超出范围 [{min_val}-{max_val}]")
    mode = config.get("mode")
    if mode and mode not in ("standalone", "cluster"):
        errors.append(f"mode={mode} 无效，应为 standalone 或 cluster")
    return errors


def format_config_show(config, source):
    """格式化 config show 输出"""
    lines = [f"当前配置（来源: {source}）："]

    def _flatten(d, prefix=""):
        for k, v in d.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict):
                _flatten(v, path)
            else:
                lines.append(f"  {path}: {v}")

    _flatten(config)
    return "\n".join(lines)


def main():
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "show":
        config, source, _ = load_config()
        print(format_config_show(config, source))
    elif len(sys.argv) > 1 and sys.argv[1] == "validate":
        config, _, errors = load_config()
        if errors:
            print("校验失败：")
            for e in errors:
                print(f"  - {e}")
        else:
            print("校验通过")
    else:
        print("用法: python config_loader.py [show|validate]")


if __name__ == "__main__":
    main()
