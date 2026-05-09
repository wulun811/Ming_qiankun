# probes.py —— 已知探针管理（三层来源 + 模块级缓存）
# 职责：is_known_probe() 统一判断，加载顺序：用户配置 → 内置默认 → 自动发现（显式调用）
# 依赖：标准库 only
# 设计：is_known_probe() 零 I/O，启动时一次性加载 frozenset 缓存

import json
from pathlib import Path

_CONFIG_PATH = Path.home() / ".ming" / "known_probes.json"
_INTERNAL_SYSTEMS = frozenset({"__admin__", "__self_health__", "__host__", "unknown"})

KNOWN_PROBES_DEFAULT = frozenset(
    {
        "tusunsun",
        "langchain",
        "openclaw",
        "mingjing",
        "opencode",
        "hermes",
    }
)


def _load_user_probes():
    """从 ~/.ming/known_probes.json 加载用户自定义列表"""
    try:
        with open(_CONFIG_PATH) as f:
            data = json.load(f)
        if isinstance(data, list):
            return {str(s) for s in data}
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        pass
    return set()


def _save_user_probes(probes):
    """写入 ~/.ming/known_probes.json"""
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CONFIG_PATH, "w") as f:
        json.dump(sorted(probes), f, ensure_ascii=False)


def _load_known_probes():
    """合并内置 + 用户配置，返回 frozenset"""
    return frozenset(KNOWN_PROBES_DEFAULT | _load_user_probes())


# ── 模块级缓存（import 时一次性加载）──
_KNOWN = _load_known_probes()


def _refresh_cache():
    """管理 API 调用后刷新缓存"""
    global _KNOWN
    _KNOWN = _load_known_probes()


def is_known_probe(system_name: str) -> bool:
    """判断是否为已知探针（支持 _ 和 - 前缀匹配，零 I/O）"""
    return any(
        system_name == name
        or system_name.startswith(name + "_")
        or system_name.startswith(name + "-")
        for name in _KNOWN
    )


# ── 管理 API（供 CLI 调用）──


def add_known_probe(name):
    """追加探针到用户配置，返回 True 表示新增"""
    user = _load_user_probes()
    if name in user:
        return False
    user.add(name)
    _save_user_probes(user)
    _refresh_cache()
    return True


def remove_known_probe(name):
    """从用户配置移除探针，返回 True 表示移除成功"""
    user = _load_user_probes()
    if name not in user:
        return False
    user.discard(name)
    _save_user_probes(user)
    _refresh_cache()
    return True


def list_known_probes(discovered=None):
    """返回分层探针列表，用于 CLI 展示

    Args:
        discovered: 可选，已有的自动发现结果。为 None 时自动扫描。
    """
    user = _load_user_probes()
    if discovered is None:
        discovered = auto_discover_probes()
    return {
        "default": sorted(KNOWN_PROBES_DEFAULT),
        "user": sorted(user),
        "auto_discovered": sorted(discovered),
        "merged": sorted(_KNOWN | discovered),
    }


def auto_discover_probes():
    """从归档库 + 热轨发现已接入过的探针（显式调用，非热路径）

    Returns:
        frozenset: 已发现的探针名称（不含内置/用户配置，不含内部系统）
    """
    discovered = set()

    # 方式 1: system_pid 表（归档器注册的探针）
    db = Path.home() / ".ming" / "ming.db"
    if db.exists():
        import sqlite3

        conn = None
        try:
            conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            conn.execute("PRAGMA busy_timeout=5000")
            rows = conn.execute(
                r"SELECT DISTINCT system FROM system_pid WHERE system NOT LIKE '\_\_%' ESCAPE '\'"
            ).fetchall()
            discovered.update(r[0] for r in rows)
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

    # 方式 2: 热轨文件前缀（兜底，system_pid 尚未写入时）
    hot = Path.home() / ".ming" / "hot"
    if hot.exists():
        try:
            for f in hot.glob("*_*.jsonl"):
                stem = f.stem
                # 取第一个 _ 之前的部分作为探针名
                idx = stem.find("_")
                if idx > 0:
                    discovered.add(stem[:idx])
        except OSError:
            pass

    # 排除内部系统
    discovered -= _INTERNAL_SYSTEMS
    return frozenset(discovered)
