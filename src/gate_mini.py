# gate_mini.py —— v0.8.1 迷你守门人：决策门控
# DEPRECATED: This module is deprecated since v0.9.2.
# LIT 1.4 handles gate control instead. Will be removed in v1.0.

import warnings
warnings.warn(
    "gate_mini.py is deprecated since v0.9.2. LIT 1.4 handles gate control instead. Will be removed in v1.0.",
    DeprecationWarning,
    stacklevel=2
)

import sqlite3
from pathlib import Path
from typing import Optional

SAFE_KEYS = {
    "temperature", "max_tokens", "timeout_ms",
    "retry_count", "context_window_limit", "batch_size",
    "log_level", "flush_interval"
}

class GateMini:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path.home() / ".ming" / "ming.db"
        self.db_path = Path(db_path)

    def evaluate(self, diagnosis: dict, suggestion: dict) -> str:
        if diagnosis.get("integrity") != "full":
            return "human_review"

        action_type = suggestion.get("type", "")
        if action_type != "config_patch":
            return "human_review"

        config_keys = suggestion.get("config_keys", [])
        if not all(k in SAFE_KEYS for k in config_keys):
            return "human_review"

        severity = diagnosis.get("severity", "")
        if severity in ("P0", "P1"):
            return "emergency"

        if self.check_constitution(suggestion):
            return "emergency"

        return "auto_pr"

    def check_constitution(self, action: dict) -> bool:
        forbidden_keys = {"model_name", "system_prompt", "permission_matrix"}
        config_keys = set(action.get("config_keys", []))
        return bool(config_keys & forbidden_keys)
