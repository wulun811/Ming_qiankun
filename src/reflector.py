# reflector.py —— v0.8.1 多维度评分汇总：事件级诊断聚合
# DEPRECATED: This module is deprecated since v0.9.2.
# Use lit_lite.py plugin instead. Will be removed in v1.0.

import warnings

warnings.warn(
    "reflector.py is deprecated since v0.9.2. Use lit_lite.py plugin instead. Will be removed in v1.0.",
    DeprecationWarning,
    stacklevel=2,
)

import sqlite3
import json
from pathlib import Path
from typing import Optional


class Reflector:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path.home() / ".ming" / "ming.db"
        self.db_path = Path(db_path)

    def aggregate(
        self, scores: dict, event: dict = None, matched_rules: list = None
    ) -> dict:
        sec = scores.get("sec", 0)
        pro = scores.get("pro", 0)
        con = scores.get("con", 0)

        if sec > 0.8:
            integrity = "emergency"
            action = "alert"
        elif pro > 0.85 and con < 0.4:
            integrity = "full"
            action = "suggest"
        elif pro > 0.6 and con > 0.6:
            integrity = "degraded"
            action = "human_review"
        else:
            integrity = "emergency"
            action = "alert"

        root_cause = self._infer_root_cause(scores, event)
        solution = self._infer_solution(scores, event)

        return {
            "integrity": integrity,
            "action": action,
            "confidence": max(scores.values()) if scores else 0.0,
            "root_cause": root_cause,
            "solution": solution,
            "source_rules": matched_rules or [],
            "scores": scores,
            "event_type": event.get("event_type", "") if event else "",
            "event_id": event.get("id") if event else None,
        }

    def _infer_root_cause(self, scores: dict, event: dict) -> str:
        if not scores:
            return "unknown"
        max_dim = max(scores, key=scores.get)
        dim_names = {
            "pro": "正方可行性",
            "con": "反方挑战",
            "sec": "安全风险",
            "eco": "成本影响",
            "hist": "历史模式",
            "rev": "回归风险",
        }
        return f"{dim_names.get(max_dim, max_dim)}信号最强 ({scores[max_dim]:.2f})"

    def _infer_solution(self, scores: dict, event: dict) -> str:
        if scores.get("sec", 0) > 0.8:
            return "立即阻断风险源，触发安全审计"
        if scores.get("pro", 0) > 0.85:
            return "建议采纳当前方案，持续监控"
        if scores.get("con", 0) > 0.6:
            return "需人工复核正反分歧点"
        return "信息不足，建议补充诊断数据"

    def compute_drift(self, window: int = 50) -> float:
        if not self.db_path.exists():
            return 0.0
        try:
            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            cur.execute(
                "SELECT llm_score, human_score FROM calibration_log "
                "WHERE llm_score IS NOT NULL AND human_score IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (window,),
            )
            rows = cur.fetchall()
            conn.close()
            if not rows:
                return 0.0
            drifts = [llm - human for llm, human in rows]
            return sum(drifts) / len(drifts)
        except Exception:
            return 0.0

    def get_calibration_bias(self, window: int = 50) -> float:
        return self.compute_drift(window)
