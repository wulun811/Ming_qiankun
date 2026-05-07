#!/usr/bin/env python3
"""pre_build_check.py — 发布前安全检查：扫描敏感信息泄露

扫描当前目录树中即将被打包的文件，检查：
1. PyPI API token (`pypi-` 前缀)
2. 其他已知敏感模式

用法：
  python3 scripts/pre_build_check.py          # 扫描当前目录
  python3 scripts/pre_build_check.py /path    # 扫描指定目录
  python3 scripts/pre_build_check.py --list   # 打印所有匹配文件
"""

import os
import re
import sys

SENSITIVE_PATTERNS = [
    (re.compile(r"pypi-[A-Za-z0-9_-]{20,}"), "PyPI API token"),
]

IGNORE_DIRS = {
    "__pycache__",
    ".git",
    ".venv",
    ".eggs",
    ".egg-info",
    "dist",
    "build",
    "node_modules",
    ".opencode",
    ".pytest_cache",
    ".ruff_cache",
}

EXCLUDE_DEFAULT = {
    "data.json",
    "triage_snapshot.json",
    "*.pyc",
}


def scan(root="."):
    issues = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        for fn in filenames:
            if any(
                __import__("fnmatch").fnmatch(fn, p)
                for p in EXCLUDE_DEFAULT
                if p.startswith("*.")
            ):
                continue
            if fn in EXCLUDE_DEFAULT:
                continue
            fpath = os.path.join(dirpath, fn)
            try:
                with open(fpath, "rb") as f:
                    content = f.read()
            except (OSError, PermissionError):
                continue
            text = content.decode("utf-8", errors="replace")
            for pattern, desc in SENSITIVE_PATTERNS:
                for m in pattern.finditer(text):
                    rel = os.path.relpath(fpath, root)
                    issues.append((rel, desc, m.start()))
    return issues


def main():
    root = (
        sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "."
    )
    show_all = "--list" in sys.argv

    issues = scan(root)
    if issues:
        print(f"❌ 发现 {len(issues)} 个敏感信息泄露:")
        for rel, desc, pos in sorted(set(issues)):
            print(f"   - {rel}:{pos}  ({desc})")
        sys.exit(1)
    else:
        print("✅ 未发现已知敏感信息泄露")
        sys.exit(0)


if __name__ == "__main__":
    main()
