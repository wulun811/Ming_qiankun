# fingerprint.py —— 0.11.9m 指纹生成与去重
# 职责：结构化指纹、相似度匹配、时间窗口去重
# 依赖：hashlib, json

import hashlib
import json

class Fingerprint:
    @staticmethod
    def generate(event: dict) -> str:
        system = event.get("system", "")
        event_type = event.get("event_type", "")
        payload = event.get("payload", {})
        payload_keys = ",".join(sorted(payload.keys()))
        exit_code = str(payload.get("exit_code", ""))
        signal = str(payload.get("signal", ""))

        raw = f"{system}|{event_type}|{payload_keys}|{exit_code}|{signal}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def match(event: dict, history_events: list, threshold: float = 0.8) -> list:
        fp = Fingerprint.generate(event)
        results = []
        for hist in history_events:
            hist_fp = Fingerprint.generate(hist)
            similarity = 1.0 if fp == hist_fp else 0.0
            if similarity >= threshold:
                results.append({"event": hist, "similarity": similarity})
        return results

    @staticmethod
    def deduplicate(events: list, window_sec: int = 10) -> list:
        seen = {}
        result = []
        for event in events:
            fp = Fingerprint.generate(event)
            ts = event.get("timestamp", 0)
            if fp in seen and (ts - seen[fp]) < window_sec:
                continue
            seen[fp] = ts
            result.append(event)
        return result
