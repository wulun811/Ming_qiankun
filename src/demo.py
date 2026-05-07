"""乾坤镜 Mingjing — Demo: emit a test event.

Usage:
  python -m mingjing demo
"""

import json
import os
import time


def emit_test_event():
    """Emit a single test event to the hot-rail."""

    hot_dir = os.path.expanduser("~/.ming/hot")
    os.makedirs(hot_dir, exist_ok=True)

    hot_file = os.path.join(hot_dir, "demo.jsonl")

    event = {
        "topic": "llm_invoke",
        "system": "demo",
        "mode": "white",
        "timestamp": time.time(),
        "payload": json.dumps(
            {
                "layer_agent": {
                    "step_id": "test",
                    "session_id": "s1",
                    "agent_name": "demo",
                },
                "layer_llm": {
                    "model": "gpt-4",
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "latency_ms": 230,
                    "cache_hit": False,
                },
                "layer_network": {"target_host": "api.openai.com", "status_code": 200},
            }
        ),
    }

    line = json.dumps(event, ensure_ascii=False) + "\n"
    with open(hot_file, "a") as f:
        f.write(line)

    print(f"✓ Test event emitted to {hot_file}")
    print("  Open Web dashboard at http://localhost:18088")
    print("  Or run: ming dx list")
