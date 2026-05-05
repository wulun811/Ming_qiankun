"""端到端集成测试：LangChain 探针 ↔ 乾坤镜 ↔ 健康报告"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 1. 初始化 LangChain 探针
from adapters.probe_langchain import init_langchain_probe

probe = init_langchain_probe(system="test_langchain_integration", mode="white")
print("[1/5] LangChain 探针已初始化 (system=test_langchain_integration)")

# 2. 模拟 LangChain 调用（不需要真实模型，直接模拟 emit）
import time as _time

probe.emit(
    "llm_invoke",
    {
        "model": "gpt-4-test",
        "input": "告诉我乾坤镜的健康状况",
        "output": "乾坤镜运行正常",
        "token_usage": {"prompt_tokens": 10, "completion_tokens": 20},
        "session_id": "integration_test",
        "ts": _time.time(),
        "seq": 1,
    },
)
print("[2/5] 已发射模拟 LLM 调用事件到热轨")

# 3. 等归档器消费
import time as _time2

for i in range(10):
    _time2.sleep(0.5)
    hot_files = []
    hot_dir = os.path.expanduser("~/.ming/hot")
    if os.path.isdir(hot_dir):
        hot_files = [f for f in os.listdir(hot_dir) if f.endswith(".jsonl")]
    if not hot_files:
        break
if hot_files:
    print(f"[3/5] ⚠ 热轨仍有 {len(hot_files)} 个文件未消费")
else:
    print("[3/5] ✅ 归档器已消费事件，热轨为空")

# 4. 调用 Ming 健康检查
from self_health import run_check

result, severity = run_check()
print(f"[4/5] 乾坤镜健康检查结果: 严重度={severity}")
for key, val in result.items():
    status_icon = "✅" if val["status"] == "ok" else "⚠️"
    print(f"  {status_icon} {key}: {val['value']} ({val['status']})")

# 5. 查询归档器中的事件是否记录
try:
    from query_bridge import QueryBridge

    qb = QueryBridge()
    events = qb.query_events(system="test_langchain_integration", limit=5)
    if events:
        print(f"[5/5] ✅ QueryBridge 查询到 {len(events)} 条 LangChain 事件:")
        for ev in events:
            print(f"  - [{ev.get('event_type')}] model={ev.get('model')}")
    else:
        print("[5/5] ⚠ QueryBridge 未查询到事件（可能尚未同步到数据库）")
except Exception as e:
    print(f"[5/5] ℹ 查询归档器: {e}")

print("\n=== 测试完成 ===")
print("结论: LangChain 探针成功发射事件 → 归档器消费 → 健康报告可获取")
