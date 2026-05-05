---
name: mingjing
description: 乾坤镜 (Mingjing) — AI Agent 诊断折射阵列。零侵入观测 LLM 调用、工具执行、记忆检索与 Agent 编排。66 条实时诊断规则，覆盖 SYS/AGT/TLT/NET/MDL/PRB/DQT 七层。
metadata: {"openclaw": {"emoji": "🔮", "requires": {"config": ["plugins.entries.mingjing-probe.enabled"]}}}
---

# 乾坤镜 Mingjing 探针

乾坤镜是 LIT 1.4 的轻量折射阵列，通过热轨 JSONL + SQLite 持久层为 Agent 框架提供无侵入可观测性。含 157 条诊断规则（排除 BIZ 业务层和 NET 网络层后覆盖率达 81%）。

## 前置依赖

- **Node.js** ≥ v22（OpenClaw 运行时）
- **Python 3** + `pyyaml`（数据库 Triage 和 Web 面板依赖）

```bash
pip3 install pyyaml
```

## 安装与启用

### 1. 获取 0qiankun 后端

```bash
git clone https://github.com/wulun811/Ming_qiankun.git ~/Ming_qiankun
cd ~/Ming_qiankun && pip3 install -r requirements.txt
```

后端提供归档器、Triage 引擎和 Web 面板。

### 2. 安装探针插件

将 OpenClaw 探针插件复制到扩展路径：

```bash
cp -r ~/Ming_qiankun/mingjing-probe/openclaw ~/.openclaw/extensions/mingjing-probe
```

或直接放在 OpenClaw 源码的 `dist/extensions/` 下：

```bash
OPENCLAW_DIR=$(dirname $(dirname $(which openclaw)))/lib/node_modules/openclaw
cp -r ~/Ming_qiankun/mingjing-probe/openclaw $OPENCLAW_DIR/dist/extensions/mingjing-probe
```

### 3. 启用探针插件

在 `~/.openclaw/openclaw.json` 中启用：

```json
{
  "plugins": {
    "entries": {
      "mingjing-probe": {
        "enabled": true
      }
    }
  }
}
```

或通过 CLI：

```bash
openclaw config set plugins.entries.mingjing-probe.enabled true
```

### 4. 重启 Gateway

```bash
pkill -9 -f openclaw-gateway
openclaw gateway
```

> **注意**：探针懒加载，初次启动需约 2-4 分钟（Gateway 加载其他插件依赖）。启动日志中看到 `[mingjing-probe] Probe runtime started` 即可使用。

### 5. 启动归档器 + Web 面板

```bash
# 归档器（后台守护，持续处理热轨事件写入 SQLite）
cd ~/Ming_qiankun && MING_MODE=standalone python3 src/ming.py start --daemon

# Web 面板（端口 18088，自动刷新 + 局域网可访问）
cd ~/Ming_qiankun && python3 src/plugins/web_dashboard/server.py --daemon --host 0.0.0.0 --port 18088 --no-browser
```

  访问 `http://localhost:18088` 或 `http://<本机IP>:18088` 查看实时数据。

> **⚡ 资源特征**：乾坤镜归档器和 Web 面板是独立守护进程，**0 LLM 调用、0 网络出站、常驻内存约 30MB**。探针随 OpenClaw 自动启停（插件机制），但归档器和 Web 面板**不会随 OpenClaw 自动启动**——需要你手动启动或设为开机自启。

## ⚠️ 首次安装注意事项

**乾坤镜是被动观测工具，无法操控你的 OpenClaw。** 事件全部由你正常使用 OpenClaw 时产生，探针只是旁路采集。

### 安装后你应该做什么

1. **确认各服务已启动**（Gateway、归档器、Web 面板）
2. **正常使用 OpenClaw 5~10 分钟**—— 发几轮对话、让 Agent 读写文件或执行命令
3. **打开 Web 面板** → 分诊覆盖率卡片会自动显示当前可诊断的病症数

### 为什么刚装好时诊断数很少？

| 事件类型 | 什么时候才会产生 |
|---------|-----------------|
| `llm_output` | Agent 输出文本时 |
| `tool_call`、`error` | Agent 调用工具（bash/read/write）时 |
| `agent_step_start/finish` | Agent 完整走完一轮步骤 |
| `platform_snapshot` | 探针心跳（30s，无需操作） |
| `__health__`、`__touch__` | 探针心跳（30s，无需操作） |

**首次装好只有心跳类事件，诊断数通常在 15~25 条。正常使用后逐步涨到 50~66 条。**

### 不需要你做的事

- ❌ 不需要手动触发探针
- ❌ 不需要修改 OpenClaw 代码
- ❌ 不需要配置任何规则文件
- ✅ 只需要正常使用 OpenClaw，其他全自动

## 架构

![OpenClaw 演示截图](./image/openclawyanshi.png)

![Web 目镜截图](./image/yanshi.jpg)

```
OpenClaw Agent ──▶ 探针 (probe-runtime.js) ──▶ 热轨 JSONL ──▶ 归档器 ──▶ SQLite
                                                                              │
                                                                        Web 面板
```

### 探针捕获的事件类型（12 种）

| 事件类型 | 触发条件 | 包含数据 |
|---------|---------|---------|
| `llm_invoke` | LLM 调用完成 | model, provider, input/output tokens, latency_ms, finish_reason |
| `llm_output` | LLM 输出文本 | output_text, output_text_hash |
| `tool_call` | 工具调用/执行 | tool_name, tool_input_hash, tool_status, execution_ms |
| `agent_event` | Agent 运行时事件 | run_id, session_id, stream, raw data |
| `agent_step_start` | Agent 步骤开始 | step_id, step_status, decision_summary, role |
| `agent_step_finish` | Agent 步骤结束 | step_id, step_status, latency_ms, role |
| `session_event` | 会话事件 | update_type, content_length |
| `platform_snapshot` | 系统级心跳 | vm_rss_kb, vm_size_kb, fd_count, threads, disk_free_mb, cpu_percent, io_read_bytes, io_write_bytes |
| `error` | 诊断异常 | error_type, error_msg |
| `__health__` | 探针自检（跟随心跳） | emit_success_count, emit_drop_count, buffer_queue_size, integrity, disk_free_mb |
| `__touch__` | 心跳（30s） | pid |
| `__register__` | 探针注册 | pid, mode, schema_version |

## OTEL 通道（可选增强）

> 乾坤镜默认通过 Hooks 通道（探针插件）采集数据，**无需额外配置即可使用**。OTEL 通道是可选增强，提供更丰富的字段（trace_id、span_id），适合需要全链路追踪的场景。

### 乾坤镜侧：启动 Bridge

```bash
python src/otel_bridge.py
```

Bridge 监听 `127.0.0.1:4319`，纯标准库零依赖，常驻内存可忽略。

### OpenClaw 侧：配置环境变量

在启动 OpenClaw 前设置：

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4319
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
```

或在 `~/.openclaw/openclaw.json` 中配置对应环境变量。

### 双通道对比

| 通道 | 数据源 | 字段丰富度 | 置信度上限 | 需要额外配置 |
|------|--------|-----------|-----------|-------------|
| Hooks（默认） | probe-runtime.js | 基础 | 1.0 | 否 |
| OTEL（可选） | OpenClaw 原生 OTEL SDK | 丰富（含 trace_id/span_id） | 0.85 | 是 |

> 两条通道并行工作，数据都写入热轨 JSONL。OTEL 通道因 Span-to-Event 映射有损，诊断置信度 capped 到 0.85。

## 分诊器 (Triage)

分诊器自动扫描 SQLite 数据库中的事件字段，与 157 条诊断规则逐一比对，输出每条规则的可用状态。

### 规则状态

| 状态 | 含义 |
|------|------|
| **ready** | 所需事件类型和字段均已在数据库中，可以进行诊断 |
| **blocked** | 缺少事件类型或字段（正常使用后会逐步解锁） |

### 如何使用

**方式一：Web 面板（无需任何操作）**

打开后分诊覆盖率卡片自动显示当前可诊断数，按层（SYS、AGT、TLT 等）分类展示。归档器每处理一批事件会自动刷新面板数据。

**方式二：CLI 手动运行**

```bash
cd ~/Ming_qiankun
python3 src/cli.py triage run
```

此命令会：
1. 扫描 `~/.ming/ming.db` 中所有事件
2. 提取所有已有字段
3. 比对 `config/diseases.yaml` 中的 157 条规则
4. 输出每条规则的 ready/blocked 状态和缺失原因

### 诊断规则覆盖层

| 层 | 缩写 | 可诊断数 | 说明 |
|----|------|---------|------|
| System | SYS | 10 | 系统资源（内存/IO/线程/FD/磁盘） |
| Probe | PRB | 7 | 探针自身健康（发射率/缓冲/完整性） |
| Agent | AGT | 12 | Agent 编排（步骤/决策/角色/错误恢复） |
| Data Quality | DQT | 2 | 数据质量（异常类型/完整性） |
| Model | MDL | 10 | 模型调用（Token/延迟/输出/频率） |
| Network | NET | 4 | 网络层（当前不可达，需 hook HTTP） |
| Tool | TLT | 10 | 工具执行（耗时/hash/错误/输出） |

> **注意**：分诊器本身不影响规则。规则是否触发取决于数据库中的**实际数据**，分诊只告诉你"能做多少种检查"，不代替你做检查。

## 配置项

在 `~/.openclaw/openclaw.json` 的 `plugins.entries.mingjing-probe` 下：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `hotPath` | `~/.ming/hot` | 热轨 JSONL 目录 |
| `heartbeatIntervalMs` | `30000` | 心跳间隔（毫秒） |
| `bufferSize` | `100` | 事件缓冲大小（批写阈值） |

## 数据目录

| 路径 | 内容 |
|------|------|
| `~/.ming/hot/` | 热轨 JSONL 文件（待归档） |
| `~/.ming/ming.db` | SQLite 数据库（已归档事件） |
| `~/.ming/web/data.json` | Web 面板数据 |
| `~/.ming/.archiver_heartbeat` | 归档器心跳时间戳 |

## 日常使用

### Web 面板（推荐）

打开 `http://localhost:18088`，核心功能：

| 区域 | 用途 |
|------|------|
| **系统卡片** | 各系统事件数、最近事件时间 |
| **分诊覆盖率** | 当前可诊断病症数 / 总数，按层分类 |
| **哈希链健康度** | 事件完整性检测 |
| **事件瀑布** | 实时事件流，按类型/完整性/关键词过滤 |
| **事件详情** | 点击事件查看完整 JSON payload |
| **Self-Health** | 探针自身健康指标（发射成功率、缓冲队列、完整性） |

面板每 15 秒自动刷新，也可手动点击刷新按钮。

### CLI 使用（OpenClaw 用户首选）

所有命令需在 `~/Ming_qiankun` 目录下运行：

```bash
cd ~/Ming_qiankun
python3 src/cli.py <command>
```

#### 日常最常用

| 命令 | 用途 |
|------|------|
| `ming report` | **一键综合报告**：系统状态 + 事件统计 + 最近诊断 + 分诊覆盖率 |
| `ming report --days 7` | 近一周综合报告 |
| `ming report --json` | JSON 格式，方便脚本解析 |
| `ming status` | 快速看系统是否在线 |
| `ming dx list --days 1` | 近 24 小时诊断列表 |
| `ming dx list --days 7 --status pending` | 近一周未处理的诊断 |

#### 诊断操作

```bash
# 列出诊断（按严重度、系统筛选）
ming dx list --days 1 --system openclaw
ming dx list --days 7 --min-confidence 0.7
ming dx list --days 1 --severity P0

# 查看某条诊断详情
ming dx show --id dx_xxxxxxxxxxxx

# 导出诊断（JSON）
ming dx export --days 7 --json

# 确认诊断（标记已处理）
ming dx confirm --id dx_xxxxxxxxxxxx --verdict accepted --user admin
```

#### 分诊与健康

```bash
# 重新扫描分诊覆盖率
ming triage run

# 查看分诊快照
ming triage report

# 探针健康检查
ming health

# 系统自检
ming self-check
```

#### 事件与数据

```bash
# 原始 SQL 查询
ming query "SELECT event_type, COUNT(*) FROM events GROUP BY event_type"

# 查看归档文件
ming archive list

# 数据库清理
ming admin vacuum
```

### 报告示例输出

运行 `ming report` 的输出示例：

```
╔══════════════════════════════════════════╗
║  乾坤镜诊断报告  (近 24 小时)              ║
╚══════════════════════════════════════════╝

── 系统状态 ──
  归档器: ✓ 运行中  (心跳 1s 前)
  数据库: 41.8 MB
  热轨积压: 0 文件  |  冷轨: 439 文件

── 事件概览 (近 24 小时) ──
  总计: 6608 条事件
  系统: openclaw: 6608
  agent_event                   5564
  llm_output                     186
  tool_call                       59
  ...

── 诊断摘要 (近 24 小时) ──
  总计 37 条  |  P0: 0  P1: 15  P2: 20

── 分诊覆盖率 ──
   可诊断: 119/157 (76%)
  tool         [██████████] 13/13
  probe        [████████░░] 7/8
  agent        [███████░░░] 14/18
  ...
```

### 查看归档器状态

```bash
# 时间戳是否接近当前时间（活跃）
cat ~/.ming/.archiver_heartbeat

# 热轨是否有积压（文件越大 = 积压越多）
ls -lh ~/.ming/hot/
```

### 停止服务

```bash
cd ~/Ming_qiankun

# 停止归档器
python3 src/ming.py stop

# 停止 Web 面板
python3 src/plugins/web_dashboard/server.py --stop
```

## 开机自启（推荐）

强烈建议设为开机自启。如果不设，每次 OpenClaw 重启时你都需要手动执行第 5 步两条命令，否则归档器和 Web 面板不会在线。

> 归档器 + Web 面板常驻负担极轻（~30MB 内存、0 LLM、0 联网），设为系统服务无感知开销。

```bash
# 注册 systemd 服务
python3 src/ming.py service install

# 启动服务
sudo systemctl start ming-archiver ming-web

# 启用开机自启
sudo systemctl enable ming-archiver ming-web
```

## 故障排查

| 问题 | 检查方法 |
|------|---------|
| 探针未启用 | `openclaw config get plugins.entries.mingjing-probe.enabled` |
| Gateway 启动后 5 分钟仍无事件 | 插件懒加载，等 Gateway 日志出现 `[mingjing-probe] Probe runtime started` |
| Gateway 多次重启导致 `already running under systemd` | 用 `systemd-run --user --scope bash -c 'openclaw gateway'` 启动 |
| 热轨无文件 | `ls -la ~/.ming/hot/`（需先触发一次 Agent 会话让探针启动） |
| 归档器未运行 | `cat ~/.ming/.archiver_heartbeat`（时间戳是否接近当前时间） |
| Web 面板无数据 | `curl http://localhost:18088/api/data.json` |
| `platform_unreliable=true` | 正常，当前环境部分系统指标不可靠（非 Linux 宿主机） |
| 告警横幅反复弹出 | 点击右侧 × 关闭，同内容不再重复；内容变化会重新弹出 |

## 版本历史

| 版本 | 诊断规则数 | 关键改动 |
|------|----------|---------|
| v0.11.5 | 66 ready | role/error_type 字段、tool/assistant 流支持、系统指标 8 项 |
| v0.11.4 | 33 ready | __health__ 事件、archiver events 表同步 |

## 架构限制

以下事件类型和指标**无法**在当前 OpenClaw 环境下捕获，需额外系统支持：

- **NET 网络层**（tcp_connected_ms, dns_resolved, status_code, tls_handshake_ms）—— 需 hook HTTP fetch 层
- **BIZ 业务层**（guardrail, milestone, plan, intent_parse 等 10 种）—— OpenClaw 不产生业务事件
- **MEM 记忆层**（memory_retrieve, memory_store）—— Opencode 独占，需移植
- **MDL-115 Temperature** —— 当前模型不返回 temperature 字段
