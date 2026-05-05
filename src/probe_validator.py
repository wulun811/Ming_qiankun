# probe_validator.py —— 0.11.9m 乾坤镜探针协议合规校验器
# 职责：校验 JSONL 事件是否符合探针协议，分层校验（CORE / RECOMMENDED）
# 依赖：零第三方依赖，仅用标准库
# 用法：python probe_validator.py <jsonl_file>

import json
import sys

CORE_REQUIRED = {"system", "event_type", "payload", "timestamp"}
OFFICIAL_RECOMMENDED = {"mode", "monotonic_ms", "lamport", "_pid", "_schema_version"}

LAYER_REQUIREMENTS = {
    "llm_invoke": ["layer_agent", "layer_llm", "layer_network"],
    "tool_call": ["layer_agent", "layer_tool", "layer_network"],
    "memory_retrieve": ["layer_agent", "layer_memory"],
    "agent_step": ["layer_agent"],
    "error": ["layer_agent"],
}

SYSTEM_EVENTS = {
    "__register__",
    "__touch__",
    "__health__",
    "__expect__",
    "__fulfill__",
    "__chain_reboot__",
}


def validate_event(event, prev_event=None):
    errors = []
    warnings = []

    # 1. 核心必填
    missing_core = CORE_REQUIRED - set(event.keys())
    if missing_core:
        errors.append(f"Missing core fields: {missing_core}")

    # 2. 官方推荐
    missing_rec = OFFICIAL_RECOMMENDED - set(event.keys())
    if missing_rec:
        warnings.append(f"Missing recommended fields: {missing_rec}")

    # 3. Layer 校验（error 事件允许 _incomplete 豁免）
    etype = event.get("event_type", "")
    if etype not in SYSTEM_EVENTS:
        payload = event.get("payload", {})
        required_layers = LAYER_REQUIREMENTS.get(etype, [])
        for layer in required_layers:
            if layer not in payload:
                if (
                    etype == "error"
                    and payload.get("_incomplete")
                    and layer in payload["_incomplete"]
                ):
                    warnings.append(f"error event missing {layer} (fallback allowed)")
                else:
                    errors.append(f"Missing required layer: {layer}")

    # 4. monotonic_ms 单调性（同一文件内）
    if prev_event and "monotonic_ms" in event and "monotonic_ms" in prev_event:
        if event["monotonic_ms"] < prev_event["monotonic_ms"]:
            errors.append(
                f"monotonic_ms decreased: {prev_event['monotonic_ms']} -> {event['monotonic_ms']}"
            )

    # 5. black 模式标记
    if event.get("mode") == "black":
        if event.get("payload", {}).get("_base_integrity") != 0.6:
            warnings.append("black mode event missing _base_integrity=0.6")

    # 6. __health__ 格式校验
    if etype == "__health__":
        payload = event.get("payload", {})
        if "disk_free_mb" not in payload:
            warnings.append("__health__ missing disk_free_mb")

    return (len(errors) == 0, errors, warnings)


def validate_file(filepath):
    total = 0
    passed = 0
    failed = 0
    warned = 0
    prev_event = None

    try:
        with open(filepath, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                total += 1
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"Line {i}: INVALID JSON - {e}")
                    failed += 1
                    continue

                is_valid, errors, warnings = validate_event(event, prev_event)
                if errors:
                    print(f"Line {i}: ERRORS: {'; '.join(errors)}")
                    failed += 1
                elif warnings:
                    print(f"Line {i}: WARNINGS: {'; '.join(warnings)}")
                    warned += 1
                else:
                    passed += 1

                if is_valid:
                    prev_event = event
    except FileNotFoundError:
        print(f"File not found: {filepath}")
        sys.exit(1)

    print(f"\n--- Summary ---")
    print(f"Total events: {total}")
    print(f"Passed: {passed}")
    print(f"Warnings: {warned}")
    print(f"Failed: {failed}")
    return failed == 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python probe_validator.py <jsonl_file>")
        sys.exit(1)
    ok = validate_file(sys.argv[1])
    sys.exit(0 if ok else 1)
