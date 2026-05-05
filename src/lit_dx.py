# lit_dx.py —— 0.11.9m 诊断输出格式化
# 职责：write_dx() — 证据质量评分 + 诊断事件写入热轨 + 交叉验证标记
import json, time, hashlib
from pathlib import Path

HOT = Path.home() / ".ming" / "hot"


def write_dx(
    system,
    fault_id,
    name,
    confidence,
    severity,
    evidence,
    inference,
    status,
    cross_validated_by=None,
    original_confidence=None,
):
    if not evidence:
        evidence = [{"summary": "无详细证据", "key_fields": {}}]

    has_otel = any(
        ev.get("key_fields", {}).get("_source") == "otel_bridge"
        or ev.get("_source") == "otel_bridge"
        for ev in evidence
    )
    if has_otel:
        confidence = min(confidence, 0.85)

    network_keywords = ["网络", "分区", "延迟", "latency", "dns", "tcp", "连接"]
    if any(k in name for k in network_keywords):
        has_network = any(
            any(k.startswith("layer_network") for k in ev.get("key_fields", {}).keys())
            for ev in evidence
        )
        if not has_network:
            for ev in evidence:
                if "key_fields" not in ev or not ev["key_fields"]:
                    ev["key_fields"] = {}
                ev["key_fields"]["_semantic_note"] = (
                    "网络诊断但缺少layer_network字段，证据降级"
                )
            confidence = min(confidence, 0.5)
            quality_cap = 1
        else:
            quality_cap = 3
    else:
        quality_cap = 3

    for ev in evidence:
        if "key_fields" not in ev or not ev["key_fields"]:
            ev["key_fields"] = {"auto_populated": True}

    has_ids = sum(1 for ev in evidence if "event_id" in ev)
    has_fields = sum(len(ev.get("key_fields", {})) for ev in evidence)

    if has_ids >= 2 and has_fields >= 6 and quality_cap >= 3:
        quality = 3
    elif has_ids >= 1 and has_fields >= 3:
        quality = min(2, quality_cap)
    else:
        quality = min(1, quality_cap)

    diag = {
        "event_type": "__diagnosis__",
        "system": system,
        "mode": "white",
        "diagnosis_id": f"dx_{hashlib.sha256(json.dumps([system, name, time.time()], sort_keys=True).encode()).hexdigest()[:12]}",
        "timestamp": time.time(),
        "fault_id": fault_id,
        "diagnosis_name": name,
        "confidence": confidence,
        "severity": severity,
        "evidence": evidence,
        "evidence_hash": hashlib.sha256(
            json.dumps(evidence, sort_keys=True).encode()
        ).hexdigest()[:16],
        "evidence_quality": quality,
        "inference_chain": inference,
        "plugin_name": "lit_lite",
        "plugin_version": "0.11.9m",
        "status": status,
    }

    if cross_validated_by:
        diag["cross_validated_by"] = cross_validated_by
    if original_confidence is not None:
        diag["original_confidence"] = original_confidence

    ts_ms = int(time.time() * 1000)
    HOT.mkdir(parents=True, exist_ok=True)
    with open(HOT / f"lit_lite_{ts_ms}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(diag, ensure_ascii=False) + "\n")
