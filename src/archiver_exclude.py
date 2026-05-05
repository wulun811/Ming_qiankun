import json
from pathlib import Path


def _load_excluded():
    p = Path.home() / ".ming" / "excluded_systems.json"
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text())
        return set(data.get("systems", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_excluded(systems: set):
    p = Path.home() / ".ming" / "excluded_systems.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"systems": sorted(systems)}, ensure_ascii=False))


def is_system_excluded(system: str) -> bool:
    # 主要来源：~/.ming/.paused/{system}（探针端文件信号）
    paused_file = Path.home() / ".ming" / ".paused" / system
    if paused_file.exists():
        return True
    # 兜底来源：旧版 excluded_systems.json
    return system in _load_excluded()
