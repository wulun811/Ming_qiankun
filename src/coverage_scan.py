# coverage_scan.py —— 0.11.9m 覆盖扫描：未埋点代码检测
# 职责：AST 分析业务代码，检测未埋点的异常处理路径和关键函数
# 依赖：ast, pathlib

import ast
from pathlib import Path

class CoverageScan:
    def __init__(self, source_dir: Path):
        self.source_dir = Path(source_dir)

    def scan(self) -> list:
        results = []
        for py_file in self.source_dir.rglob("*.py"):
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
                results.extend(self._analyze_file(py_file, tree))
            except Exception:
                continue
        return results

    def _analyze_file(self, filepath: Path, tree: ast.AST) -> list:
        results = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if not self._has_emit_call(node):
                    results.append({
                        "file": str(filepath),
                        "line": node.lineno,
                        "function": "except_block",
                        "risk_level": "high"
                    })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if any(kw in node.name.lower() for kw in ["critical", "important", "core"]):
                    if not self._has_emit_call(node):
                        results.append({
                            "file": str(filepath),
                            "line": node.lineno,
                            "function": node.name,
                            "risk_level": "medium"
                        })
        return results

    def _has_emit_call(self, node: ast.AST) -> bool:
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Attribute) and func.attr == "emit":
                    return True
                if isinstance(func, ast.Name) and func.id == "emit":
                    return True
        return False

    def suggest_probes(self) -> list:
        issues = self.scan()
        return [
            {"file": issue["file"], "line": issue["line"], "suggested_event_type": "exception_caught"}
            for issue in issues
        ]
