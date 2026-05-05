# 乾坤镜 Mingjing

> **AI Agent 诊断折射阵列** — 零侵入观测 LLM 调用、工具执行、记忆检索与 Agent 编排。
>
> [🌏 English](./README_en.md) | [📖 完整手册](updocs/)

![乾坤镜 LOGO](updocs/image/logo.png)

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-BUSL--1.1-green.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.11.9m-green.svg)](https://github.com/wulun811/Ming_qiankun/releases)

**乾坤镜是 LIT 1.4 的轻量折射阵列，不是独立诊断中台。** 它通过热轨 JSONL 文件 + SQLite/MySQL 持久层，为 LangChain、LlamaIndex、CrewAI、OpenHands、AutoGPT 等 Agent 框架提供无侵入的可观测性（部分适配器尚在适配中）。

---

📖 完整手册：请参阅 [updocs/](updocs/) 目录下的完整文档。

## 5 分钟上手

### 1. 克隆

```bash
git clone https://github.com/wulun811/Ming_qiankun.git
cd Ming_qiankun
```

### 2. 运行（零依赖）

```bash
# Standalone 模式 — 纯 Python 标准库，零第三方依赖
MING_MODE=standalone python src/ming.py start
```

### 3. 发射测试事件

> **注意**：以下命令必须在 `Ming_qiankun` 项目根目录执行。`sys.path.insert(0, 'src')` 是为让 Python 能找到探针模块。

```bash
# 打开另一个终端，确保在项目根目录
python -c "
import sys; sys.path.insert(0, 'src')
from probe_uni import ProbeUni
p = ProbeUni(system='demo', mode='white')
p.emit('llm_invoke', {
    'layer_agent': {'step_id': 'test', 'session_id': 's1', 'agent_name': 'demo'},
    'layer_llm': {'model': 'gpt-4', 'input_tokens': 100, 'output_tokens': 50, 'latency_ms': 230, 'cache_hit': False},
    'layer_network': {'target_host': 'api.openai.com', 'status_code': 200}
})
print('Event emitted!')
"
```

### 4. 查看诊断

> **两个入口**：`src/ming.py` 负责服务管理（启动/停止归档器和 Web），`src/cli.py` 负责查询诊断。你也可以用 `python src/cli.py --help` 查看所有命令。

```bash
python src/cli.py dx list
```

### 5. 查看 Web 目镜

```bash
# 启动 Web 服务（自动导出 + 自动刷新，绑定 127.0.0.1:18088）
python src/ming.py web start

# 局域网访问（绑定 0.0.0.0）
python src/ming.py web start --port 18088
# 然后在 server 启动时手动指定 --host 0.0.0.0：
python src/plugins/web_dashboard/server.py --daemon --host 0.0.0.0 --port 18088 --no-browser

# 浏览器打开 http://localhost:18088 或 http://<本机IP>:18088
```

**自动更新**：Web 服务内置每 30 秒自动导出 data.json + 前端自动刷新，无需手动执行 export 命令。

**局域网访问**：
| 场景 | 命令 | 浏览器地址 |
|------|------|-----------|
| 本机仅 | `python src/plugins/web_dashboard/server.py --daemon --port 18088` | `http://localhost:18088` |
| 局域网开放 | `python src/plugins/web_dashboard/server.py --daemon --host 0.0.0.0 --port 18088 --no-browser` | `http://<本机IP>:18088` |
| 加认证 | `python src/plugins/web_dashboard/server.py --daemon --host 0.0.0.0 --port 18088 --no-browser --token your-secret` | `http://<本机IP>:18088/?token=your-secret` |

---

## Docker 一键运行

```bash
docker build -t ming .
docker run -d --name ming -p 18088:18088 ming
# 打开 http://localhost:18088
```

---

## 架构概览

```
┌─────────────┐     JSONL hot files      ┌──────────────┐
│  Probes     │ ───────────────────────► │  Archiver    │
│  (无状态)    │                          │  (唯一写入者) │
└─────────────┘                          └──────┬───────┘
       │                                        │
       │  LangChain / LlamaIndex                │ SQLite / MySQL
       │  CrewAI / OpenHands                    ▼
       │  AutoGPT / Hermes / MCP         ┌──────────────┐
       │                                 │  Query       │
       └────────────────────────────────►│  Bridge      │
                                         └──────┬───────┘
                                                │
                                          ┌─────▼─────┐
                                          │  Web UI   │
                                          │  / MCP    │
                                          └───────────┘
```

### 核心模块

| 模块 | 行数 | 职责 |
|------|------|------|
| `probe_uni.py` | ~245 | 无状态热轨写入器，零数据库依赖 |
| `archiver.py` | ~465 | 唯一写入者，顺序扫描 hot → SQLite |
| `cli.py` | ~259 | 命令行：dx/health/skill/query/status/web |
| `watchdog.py` | ~163 | 进程守护 + 磁盘告警 |
| `query_bridge.py` | ~99 | 只读查询接口 |

### 适配器（官方参考实现）

| 适配器 | 语言 | 事件类型 |
|--------|------|----------|
| LangChain | Python | llm_invoke, tool_call, memory_retrieve (待适配) |
| LlamaIndex | Python | llm_invoke, memory_retrieve, agent_step (待适配) |
| CrewAI | Python | agent_step, llm_invoke (待适配) |
| OpenHands | Python | agent_step, llm_invoke, tool_call (待适配) |
| AutoGPT | Python | agent_step, llm_invoke (待适配) |
| Hermes | Python | llm_invoke, tool_call, memory_retrieve |
| MCP | Python | tool_call, memory_retrieve, llm_invoke |

> 所有适配器采用 **monkey-patch + 零第三方依赖** 设计。未安装目标框架时静默降级，不影响主流程。使用第三方框架的适配器需额外安装对应框架。（标记"待适配"的为社区贡献方向，欢迎 PR。）

---

## 双模式

| 模式 | 环境变量 | 持久层 | 依赖 |
|------|----------|--------|------|
| **Standalone** | `MING_MODE=standalone`（默认） | SQLite | 纯标准库 |
| **Cluster** | `MING_MODE=cluster` | MySQL | pymysql |

```bash
# Standalone（默认）
python src/ming.py start

# Cluster
MING_MODE=cluster \
  WQ_DB_HOST=127.0.0.1 \
  WQ_DB_USER=root \
  WQ_DB_PASSWORD=secret \
  WQ_DB_NAME=ming \
  python src/ming.py start
```

---

## 性能调参

Standalone 模式下，归档器默认处理 **1000 事件/秒**。通过环境变量可灵活调整：

| 环境变量 | 默认值 | 说明 | 典型场景 |
|----------|--------|------|----------|
| `WQ_ARCHIVER_BATCH_SIZE` | `1000` | 每次扫描的热轨文件数上限 | 高密度写入：`5000` |
| `WQ_ARCHIVER_FLUSH_SEC` | `1.0` | 扫描间隔（秒） | 低延迟要求：`0.5` |
| `WQ_ARCHIVER_VACUUM_HOURS` | `24` | VACUUM 间隔（小时） | 磁盘紧张时：`6` |

### 场景推荐

```bash
# 场景 1: 默认（大多数用户，1000 events/s）
MING_MODE=standalone python src/ming.py start

# 场景 2: 高吞吐（写入密集，~5000 events/s）
WQ_ARCHIVER_BATCH_SIZE=5000 \
WQ_ARCHIVER_FLUSH_SEC=0.5 \
MING_MODE=standalone python src/ming.py start

# 场景 3: 省电模式（低负载设备，~200 events/s）
WQ_ARCHIVER_BATCH_SIZE=200 \
WQ_ARCHIVER_FLUSH_SEC=5.0 \
MING_MODE=standalone python src/ming.py start
```

> **提示**：调整参数后需重启归档器生效。前端 Config 面板可查看当前配置值（只读）。

---

## 运行测试

```bash
# 全量测试
python -m pytest tests/ -v

# 仅适配器 Mock 测试
python -m pytest tests/test_probe_*_mock.py -v
```

当前状态：**389 passed, 2 skipped, 0 failed**

---

## 代码量预算

乾坤镜采用分类预算制，配置见 [`config/budget.json`](config/budget.json)：

| 分类 | 预算 | 说明 |
|------|------|------|
| core_base | 1,500 行 | 探针 + 归档器 + CLI + 查询 |
| cluster_extension | 600 行 | Cluster 扩展（可选） |
| plugin_layer | 1,000 行 | 插件层（可替换） |
| adapter_layer | 1,200 行 | 适配器 + 基类 |
| test_layer | 无上限 | 测试代码单独统计 |

---

## 目录结构

```
├── src/                    # 源代码
│   ├── ming.py       # 统一入口
│   ├── probe_uni.py        # 探针
│   ├── archiver.py         # 归档器
│   ├── cli.py              # CLI
│   ├── adapters/           # 适配器
│   └── plugins/            # 插件
├── tests/                  # 测试
├── config/                 # 配置
│   └── budget.json         # 代码量预算
├── updocs/                 # 文档（架构+探针+诊断+数据+接口+运维+测试）
└── .github/workflows/      # CI/CD
```

---

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

### 铁律（违反即 P0）

- **Standalone 零第三方依赖**：仅 Python 标准库
- **Cluster 唯一额外依赖**：仅 `pymysql`
- **探针纯粹**：`probe_uni.py` 不 import sqlite3/pymysql
- **唯一写入者**：只有归档器可写入持久层
- **只读对外**：查询模块使用只读连接

---

## 许可

Business Source License 1.1 — 全球年收入低于 10 万美元的公司和个人可免费生产使用。非生产用途（开发、测试、评估）无收入限制。详见 [LICENSE](LICENSE) 文件。

---

## 作者

- **陈正 (Chenzheng)** · [@wulun811](https://github.com/wulun811)
- 官方邮箱：zhulong007ai@163.com
- 灵感始于：**诛仙协议 / THEOCLAST Protocol (TCL)**

---

**乾坤镜 v0.11.9m — 为社区而生。**
