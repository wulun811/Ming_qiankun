# ming-probe-langchain

乾坤镜（Mingjing）LangChain 自动探针 —— **pip install 即自动激活，零代码改动**。

```bash
pip install ming-probe-langchain
```

装完即生效，所有 LangChain 调用（LLM invoke、tool call、retriever、stream、batch）自动上报到乾坤镜。

---

## 功能

### 1. 自动上报

安装后无需任何 import 或配置，首次使用时自动激活：

```python
# 一切照常 —— 探针自动拦截
llm.invoke("你好")            # → 上报 llm_invoke 事件
tool.invoke("搜索天气")       # → 上报 tool_call 事件
retriever.invoke("文档")      # → 上报 memory_retrieve 事件
```

支持 11 类事件：`llm_invoke`、`llm_output`、`tool_call`、`memory_retrieve`、`agent_step`、`error`、`stream_token`、`chat_model_start`、`cache_hit`、`relevance_scores`、`chunk`。

### 2. 查询乾坤镜健康状态

```python
from ming_probe_langchain import get_health

report = get_health()
print(report["archiver_lag"]["desc"])  # "0.5 秒"
print(report["overall"])               # "ok" / "warn" / "no_archiver"
```

返回 6 项指标：

| 指标 | 说明 | 警告阈值 |
|------|------|----------|
| `archiver_lag` | 归档器延迟 | >5s |
| `wal_size` | WAL 文件大小 | >100MB |
| `hot_dir_pileup` | 热轨文件堆积 | >200文件 或 >50MB |
| `disk_free` | 磁盘剩余空间 | <500MB |
| `lit_lite` | 诊断引擎输出 | 无文件 = 未启用 |
| `mirror` | OTEL Bridge 状态 | 未运行 = 正常（非必需） |

也支持纯文本格式（适合 LLM 消费）：

```python
from ming_probe_langchain import get_health_text

print(get_health_text())
# 乾坤镜健康报告 — 总体状态: OK
#   ✓ archiver_lag: 0.5 秒 (ok) [阈值: <5s]
#   ✓ wal_size: 0.14 MB (ok) [阈值: <100MB]
#   ...
```

### 3. 给 LangChain Agent 查健康工具

把 `MingHealthTool` 加入你的工具列表，Agent 自主决定何时查健康：

```python
from langchain.agents import create_react_agent
from ming_probe_langchain import MingHealthTool

tools = [MingHealthTool()]
agent = create_react_agent(llm, tools)

# 当 Agent 发现异常或需要排查系统状态时，会自动调用
```

---

## 手动初始化（不依赖自动激活）

```python
from ming_probe_langchain import init_langchain_probe

probe = init_langchain_probe(system="my_app", mode="white")
```

可用参数：
- `system`：自定义系统名（默认 `langchain`），用于在乾坤镜中区分不同应用
- `mode`：`"white"`（默认，上报 11 类事件）或 `"black"`（仅通过 /proc 系统级观测，完整性降至 0.6）

### Session ID 传递

```python
llm.invoke("你好", config={"configurable": {"session_id": "user_123"}})
```

---

## 前置条件

需乾坤镜后端（归档器）在后台运行。两种方式：

```bash
# 方式 1：pip 安装完整乾坤镜（推荐）
pip install mingjing
ming start            # 启动归档器守护进程
ming web start        # 启动 Web 面板（可选，端口 18088）

# 方式 2：Docker
docker run -d --name ming -p 18088:18088 mingjing
```

没有归档器时，探针仍可正常发射事件（写入热轨文件），归档器启动后自动消费。

---

## 兼容性

| 环境 | 要求 |
|------|------|
| Python | >= 3.10 |
| langchain-core | >= 1.0（含 langchain >= 0.3） |
| 第三方依赖 | **零**（仅 Python 标准库） |

---

## 原理

```
LangChain 调用 → monkey-patched invoke() / BaseCallbackHandler
              → ProbeUni.emit()
              → ~/.ming/hot/*.jsonl（热轨）
              → 乾坤镜归档器消费 → SQLite / MySQL
```

PEP 302 import hook 机制，在 `langchain_core` 首次导入时自动注入。不需要改代码、不需要配 callback、不需要手动初始化。

---

## 许可

Business Source License 1.1 — 与乾坤镜主包一致。全球年收入低于 $100,000 USD 的公司和个人可免费生产使用。详见主仓库 [LICENSE](../../LICENSE) 文件。
