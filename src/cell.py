# cell.py —— v0.8.1 镜胞核心：6 维评分引擎
# DEPRECATED: This module is deprecated since v0.9.2.
# Use lit_lite.py plugin instead. Will be removed in v1.0.

import warnings

warnings.warn(
    "cell.py is deprecated since v0.9.2. Use lit_lite.py plugin instead. Will be removed in v1.0.",
    DeprecationWarning,
    stacklevel=2,
)

import json
from pathlib import Path
from typing import Optional
from .rule_parser import load_rules, match_event

DIMENSIONS = ["pro", "con", "sec", "eco", "hist", "rev"]
DIM_PREFIX = {"P": "pro", "C": "con", "S": "sec", "E": "eco", "H": "hist", "R": "rev"}


class Cell:
    def __init__(self, filters_dir: Optional[Path] = None):
        if filters_dir is None:
            filters_dir = Path(__file__).parent.parent / "filters"
        self._filters_dir = Path(filters_dir)
        self._rules = []
        self._load_time = 0
        self._reload()

    def _reload(self):
        self._rules = load_rules(self._filters_dir)
        self._load_time = (
            Path(self._filters_dir).stat().st_mtime if self._filters_dir.exists() else 0
        )

    def _check_reload(self):
        if self._filters_dir.exists():
            current_mtime = self._filters_dir.stat().st_mtime
            if current_mtime != self._load_time:
                self._reload()

    def score(self, event: dict) -> tuple:
        self._check_reload()
        scores = {dim: 0.0 for dim in DIMENSIONS}
        matched_rules = []

        for rule in self._rules:
            if match_event(event, rule):
                rule_id = rule.get("id", "")
                prefix = rule_id[0].upper() if rule_id else ""
                dim = DIM_PREFIX.get(prefix)
                if dim:
                    confidence = rule.get("confidence", 0.0)
                    scores[dim] = max(scores[dim], confidence)
                matched_rules.append(rule_id)

        return scores, matched_rules

    def score_batch(self, events: list) -> list:
        return [self.score(event) for event in events]
