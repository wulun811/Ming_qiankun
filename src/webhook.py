# webhook.py —— 0.11.9m 标准化推送
# 职责：把诊断结果推出去，不是告警引擎，只是"通知通道"
# 依赖：标准库 only（urllib, json, pathlib）

import json, urllib.request, time
from pathlib import Path

class WebhookPusher:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path or Path.home() / ".ming" / "webhook.yaml")
        self.config = self._load_config()

    def _load_config(self):
        if not self.config_path.exists():
            return {"alert_levels": ["P0", "P1"], "urls": []}
        try:
            text = self.config_path.read_text(encoding="utf-8")
            # 简易 YAML 解析
            result = {}
            stack = [(result, -1)]
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    continue
                indent = len(line) - len(line.lstrip())
                if ':' not in stripped:
                    continue
                key, _, val = stripped.partition(':')
                key = key.strip()
                val = val.strip()
                while len(stack) > 1 and indent <= stack[-1][1]:
                    stack.pop()
                parent = stack[-1][0]
                if val:
                    if val.startswith('[') and val.endswith(']'):
                        items = [x.strip().strip('"').strip("'") for x in val[1:-1].split(',') if x.strip()]
                        parent[key] = items
                    elif val.startswith('"') and val.endswith('"'):
                        parent[key] = val[1:-1]
                    else:
                        parent[key] = val
                else:
                    new_dict = {}
                    parent[key] = new_dict
                    stack.append((new_dict, indent))
            return result
        except Exception:
            return {"alert_levels": ["P0", "P1"], "urls": []}

    def push(self, diagnosis: dict):
        severity = diagnosis.get("severity", "P3")
        if severity not in self.config.get("alert_levels", ["P0", "P1"]):
            return
        payload = json.dumps({
            "msg_type": "diagnosis",
            "severity": severity,
            "system": diagnosis.get("system", "unknown"),
            "diagnosis_name": diagnosis.get("diagnosis_name", "unknown"),
            "confidence": diagnosis.get("confidence", 0),
            "evidence_quality": diagnosis.get("evidence_quality", 0),
            "inference_chain": diagnosis.get("inference_chain", ""),
            "timestamp": time.time()
        }, ensure_ascii=False).encode()
        for url in self.config.get("urls", []):
            try:
                req = urllib.request.Request(
                    url, data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass

    def push_raw(self, msg: dict):
        for url in self.config.get("urls", []):
            try:
                req = urllib.request.Request(
                    url, data=json.dumps(msg, ensure_ascii=False).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                pass
