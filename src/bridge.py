# bridge.py —— 0.11.9m CLI + HTTP 混合传输层
# 职责：接收 reflector 诊断输出 → 路由到 LIT 1.4 中央
# 零侵入，零外部依赖（使用 urllib 标准库）
# 修复：HTTP 失败时只写文件缓冲，不再双重消费 stdout+file

import os, sys, json, time
from pathlib import Path
from typing import Dict, Any, List

BRIDGE_MODE = os.getenv("BRIDGE_MODE", "auto")  # auto | http | cli
LIT_ENDPOINT = os.getenv("LIT_ENDPOINT", "")  # 中央 LIT 1.4 HTTP 接口
LIT_TIMEOUT = int(os.getenv("LIT_TIMEOUT", "5"))
BRIDGE_BUFFER = os.getenv("BRIDGE_BUFFER", "./bridge_buffer.jsonl")
BATCH_SIZE = int(os.getenv("BRIDGE_BATCH_SIZE", "10"))
BATCH_FLUSH_SEC = int(os.getenv("BATCH_FLUSH_SEC", "30"))


class Bridge:
    """CLI + HTTP 混合传输桥"""

    def __init__(self):
        self._buffer: List[Dict[str, Any]] = []
        self._last_flush = time.time()
        self._buffer_path = Path(BRIDGE_BUFFER)
        self._buffer_path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, diagnosis: Dict[str, Any]):
        """单条诊断入口"""
        if BRIDGE_MODE == "http":
            self._http_post(diagnosis)
        elif BRIDGE_MODE == "cli":
            self._cli_out(diagnosis)
            self._buffer_to_file(diagnosis)
        else:  # auto
            self._auto_route(diagnosis)

    def batch_emit(self, diagnoses: List[Dict[str, Any]]):
        """批量入口，减少 HTTP 往返"""
        if not diagnoses:
            return
        if BRIDGE_MODE in ("http", "auto") and LIT_ENDPOINT:
            try:
                self._http_post({"batch": diagnoses, "count": len(diagnoses)})
                return
            except Exception:
                pass
        # 降级：逐条 CLI + 文件缓冲
        for d in diagnoses:
            self._cli_out(d)
            self._buffer_to_file(d)

    def flush(self):
        """强制刷出当前缓冲（进程退出前调用）"""
        if self._buffer:
            self.batch_emit(self._buffer)
            self._buffer = []

    def _auto_route(self, diagnosis: Dict[str, Any]):
        """auto 模式：HTTP 优先，失败降级只写本地缓冲（不再双写 stdout+file）"""
        if not LIT_ENDPOINT:
            self._cli_out(diagnosis)
            self._buffer_to_file(diagnosis)
            return
        try:
            self._http_post(diagnosis)
        except Exception:
            # HTTP 失败：只写文件缓冲，避免下游同时采集 stdout 导致重复消费
            self._buffer_to_file(diagnosis)

    def _http_post(self, payload: Any):
        """HTTP POST 到 LIT 1.4 中央，使用标准 urllib"""
        import urllib.request

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            LIT_ENDPOINT,
            data=data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=LIT_TIMEOUT) as resp:
            if resp.status not in (200, 202, 204):
                raise RuntimeError(f"LIT 1.4 返回 HTTP {resp.status}")

    def _cli_out(self, diagnosis: Dict[str, Any]):
        """CLI 模式：stdout + flush，供 systemd/Filebeat/Fluentd 采集"""
        enriched = {
            **diagnosis,
            "_bridge_ts": time.time(),
            "_bridge_mode": "cli_fallback" if BRIDGE_MODE == "auto" else "cli",
        }
        print(json.dumps(enriched, ensure_ascii=False), flush=True)

    def _buffer_to_file(self, diagnosis: Dict[str, Any]):
        """最终降级：追加到本地 JSONL，外部 agent 定期读取并清理"""
        with open(self._buffer_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(diagnosis, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    # 管道模式：reflector 输出通过管道传给 bridge
    # 用法：python reflector.py --project proj01 | python bridge.py
    if not sys.stdin.isatty():
        bridge = Bridge()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                diag = json.loads(line)
                bridge.emit(diag)
            except json.JSONDecodeError:
                continue
        bridge.flush()
    else:
        # 独立测试模式
        test = {
            "integrity": "degraded",
            "action": "alert",
            "confidence": 0.91,
            "root_cause": "latency_spike",
            "solution": "scale_up",
            "source_rules": ["R01", "R03"],
            "project_id": "demo",
            "timestamp": time.time(),
        }
        Bridge().emit(test)
