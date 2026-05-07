# credential_vault.py —— v0.11.9m 凭据保险库
# 职责：统一凭据入口，零明文散落。纯标准库，零第三方依赖。
# 输入源（优先级高→低）：
#   1. MING_SECRET_<KEY> 环境变量
#   2. ~/.ming/.secrets 文件（K=V 格式，强制 chmod 600）
# 出口：get_secret("key") — 只有这一个合法渠道获取凭据
import os
from pathlib import Path

VAULT_FILE = Path.home() / ".ming" / ".secrets"

_loaded = False
_cache: dict[str, str] = {}


def _load_vault():
    global _loaded, _cache
    if _loaded:
        return
    _loaded = True

    if VAULT_FILE.exists():
        try:
            st = VAULT_FILE.stat()
            if st.st_mode & 0o077:
                VAULT_FILE.chmod(0o600)
            for line in VAULT_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    _cache[k.strip()] = v.strip()
        except Exception:
            pass


def get_secret(key: str, default: str = "") -> str:
    """
    统一凭据入口。查询优先级：MING_SECRET_<KEY> 环境变量 > ~/.ming/.secrets 文件。
    用法: get_secret("tusunsun.pushToken")
          get_secret("mysql.password", "root")
    """
    env_key = "MING_SECRET_" + key.upper().replace(".", "_")
    env_val = os.getenv(env_key)
    if env_val is not None:
        return env_val

    if not _loaded:
        _load_vault()

    return _cache.get(key, default)


def redact(value: str) -> str:
    """安全脱敏，只显示末 4 位。空值返回 '(empty)'。"""
    if not value:
        return "(empty)"
    if len(value) <= 4:
        return "***"
    return "***" + value[-4:]


def start_health_report() -> list[str]:
    """启动自检：报告凭据加载状态"""
    _load_vault()
    warnings = []
    loaded_keys = sorted(_cache.keys())

    if not _cache:
        warnings.append(
            f"[CRED-VAULT] 提示: {VAULT_FILE} 为空或不存在，凭据保险库未加载任何密钥。"
        )
    else:
        masked = {k: redact(v) for k, v in _cache.items()}
        warnings.append(f"[CRED-VAULT] 已加载 {len(loaded_keys)} 个密钥: {masked}")

    return warnings
