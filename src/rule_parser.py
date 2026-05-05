# rule_parser.py —— v0.8.1 内置规则条件解析器
# DEPRECATED: This module is deprecated since v0.9.2.
# Use Skill YAML + plugin_runner instead. Will be removed in v1.0.

import warnings

warnings.warn(
    "rule_parser.py is deprecated since v0.9.2. Use Skill YAML + plugin_runner instead. Will be removed in v1.0.",
    DeprecationWarning,
    stacklevel=2,
)

import re, json
from pathlib import Path
from typing import Callable, Any


def parse_condition(condition_str: str) -> Callable[[Any], bool]:
    """解析条件字符串，返回匹配函数"""

    m = re.match(r"^contains\('(.+?)'\)$", condition_str)
    if m:
        target = m.group(1)
        return lambda val: target in str(val)

    m = re.match(r"^contains_any\(\[(.+?)\]\)$", condition_str)
    if m:
        items = [item.strip().strip("'\"") for item in m.group(1).split(",")]
        return lambda val: any(item in str(val) for item in items)

    m = re.match(r"^regex\('(.+?)'\)$", condition_str)
    if m:
        pattern = re.compile(m.group(1))
        return lambda val: bool(pattern.search(str(val)))

    m = re.match(r"^range:([+-]?\d+\.?\d*)-([+-]?\d+\.?\d*)$", condition_str)
    if m:
        min_val, max_val = float(m.group(1)), float(m.group(2))
        return lambda val: min_val <= float(val) <= max_val

    m = re.match(r"^>\s*([+-]?\d+\.?\d*)$", condition_str)
    if m:
        threshold = float(m.group(1))
        return lambda val: float(val) > threshold

    m = re.match(r"^<\s*([+-]?\d+\.?\d*)$", condition_str)
    if m:
        threshold = float(m.group(1))
        return lambda val: float(val) < threshold

    return lambda val: str(val) == condition_str.strip("'\"")


def match_event(event: dict, rule: dict) -> bool:
    """检查事件是否匹配规则"""
    condition = rule.get("condition", {})

    for key, condition_str in condition.items():
        if "." in key:
            parts = key.split(".", 1)
            payload = event.get(parts[0])
            if not isinstance(payload, dict):
                return False
            val = payload.get(parts[1])
        else:
            val = event.get(key)

        if val is None:
            return False

        matcher = parse_condition(condition_str)
        if not matcher(val):
            return False

    return True


def load_rules(filters_dir: Path = None) -> list:
    """加载 filters 目录下所有 YAML 规则（使用内置解析器，无需 PyYAML）"""
    if filters_dir is None:
        filters_dir = Path(__file__).parent / "filters"

    rules = []
    for yaml_file in filters_dir.glob("*.yaml"):
        try:
            content = yaml_file.read_text(encoding="utf-8")
            file_rules = _parse_yaml_rules(content)
            rules.extend(file_rules)
        except Exception:
            continue

    return rules


def _parse_yaml_rules(content: str) -> list:
    """简易 YAML 规则解析器（仅支持 rules 列表结构，足够 v0.8 使用）"""
    rules = []
    current_rule = None
    in_condition = False
    condition_indent = 0
    in_payload_values = False
    payload_indent = 0

    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())

        # 检测规则开始
        if stripped.startswith("- id:"):
            if current_rule:
                rules.append(current_rule)
            current_rule = {"id": stripped.split(":", 1)[1].strip()}
            in_condition = False
            in_payload_values = False
            continue

        if current_rule is None:
            continue

        # 条件块开始
        if stripped == "condition:":
            in_condition = True
            in_payload_values = False
            current_rule["condition"] = {}
            condition_indent = indent + 2  # 子字段的缩进
            continue

        if stripped.startswith("payload_values:"):
            in_payload_values = True
            in_condition = False
            current_rule["payload_values"] = {}
            payload_indent = indent + 2
            continue

        # 如果缩进回到规则级别，退出条件块
        if in_condition and indent <= condition_indent - 2 and ":" in stripped:
            in_condition = False

        if in_payload_values and indent <= payload_indent - 2 and ":" in stripped:
            in_payload_values = False

        # 解析条件行
        if in_condition and ":" in stripped:
            key, _, val = stripped.partition(":")
            val = val.strip().strip("'\"")
            if val:
                current_rule["condition"][key.strip()] = val
            continue

        # 解析 payload_values 行（指纹库）
        if in_payload_values and ":" in stripped:
            key, _, val = stripped.partition(":")
            val = val.strip().strip("'\"")
            current_rule["payload_values"][key.strip()] = val
            continue

        # 其他规则字段
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip().strip("'\"")

            if key in ("confidence", "occurrence_count"):
                try:
                    current_rule[key] = float(val)
                except Exception:
                    pass
            elif key in ("verified",):
                current_rule[key] = val.lower() == "true"
            else:
                current_rule[key] = val

    if current_rule:
        rules.append(current_rule)

    return rules
