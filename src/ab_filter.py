# ab_filter.py —— 0.11.9m A/B 过滤：方案对比筛选
# 职责：对比多个解决方案的历史效果，筛选最优方案
# 依赖：json, sqlite3

import sqlite3
from pathlib import Path
from typing import Optional


class ABFilter:
    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path.home() / ".ming" / "ming.db"
        self.db_path = Path(db_path)

    def compare_solutions(self, fault_pattern: str, solutions: list) -> list:
        if not self.db_path.exists():
            return []

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        results = []

        for solution in solutions:
            escaped = solution.replace("%", "\\%").replace("_", "\\_")
            cur.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = ? AND payload LIKE ? ESCAPE '\\'",
                (fault_pattern, f"%{escaped}%"),
            )
            sample_size = cur.fetchone()[0]

            success_rate = 0.0
            avg_recovery_time = 0.0

            if sample_size >= 5:
                escaped = solution.replace("%", "\\%").replace("_", "\\_")
                cur.execute(
                    "SELECT AVG(CASE WHEN payload LIKE '%resolved%' THEN 1 ELSE 0 END) "
                    "FROM events WHERE event_type = ? AND payload LIKE ? ESCAPE '\\'",
                    (fault_pattern, f"%{escaped}%"),
                )
                success_rate = cur.fetchone()[0] or 0.0

            results.append(
                {
                    "solution": solution,
                    "success_rate": success_rate,
                    "avg_recovery_time": avg_recovery_time,
                    "sample_size": sample_size,
                }
            )

        conn.close()
        return results

    def recommend(self, fault_pattern: str) -> Optional[dict]:
        solutions = self._get_solutions(fault_pattern)
        if not solutions:
            return None

        comparisons = self.compare_solutions(fault_pattern, solutions)
        valid = [c for c in comparisons if c["sample_size"] >= 5]
        if not valid:
            return (
                {
                    "solution": comparisons[0]["solution"],
                    "confidence": "insufficient_data",
                }
                if comparisons
                else None
            )

        best = max(valid, key=lambda x: x["success_rate"])
        return {"solution": best["solution"], "confidence": best["success_rate"]}

    def _get_solutions(self, fault_pattern: str) -> list:
        if not self.db_path.exists():
            return []
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT payload FROM events WHERE event_type = ? LIMIT 50",
            (fault_pattern,),
        )
        solutions = list(set(str(row[0]) for row in cur.fetchall()))
        conn.close()
        return solutions
