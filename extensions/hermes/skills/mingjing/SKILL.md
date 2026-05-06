---
name: mingjing
description: 乾坤镜 (Ming) — AI Agent 体检中心。零侵入观测 LLM 调用、工具执行、记忆检索与 Agent 编排。157 条诊断规则（Hermes 可诊断 ~89），含 4 级健康评估、忽略/归档/复位操作、逐实例报告。当用户询问系统健康、性能问题、诊断报告时触发此 skill。
metadata: {"hermes": {"emoji": "🔮", "category": "diagnostics"}}
---

# 乾坤镜 Ming — AI Agent 诊断折射阵列

乾坤镜是 LIT 1.4 的轻量折射阵列，通过热轨 JSONL + SQLite 持久层为 Agent 框架提供无侵入可观测性。含 157 条诊断规则，覆盖 SYS/AGT/TLT/MDL/PRB/DQT 七层。

> **定位**：乾坤镜不是独立诊断中台，而是"折射阵列"——它不直接分析系统，而是采集 Agent 运行时的观测数据，通过规则引擎折射出潜在的故障模式。

## 架构

```
Hermes Agent ──▶ mingjing-probe 插件 ──▶ 热轨 JSONL ──▶ Archiver ──▶ SQLite (ming.db)
                                                                                    │
                                                                     LIT Lite (诊断引擎)
                                                                                    │
                                                                     ming CLI (用户查询入口)
```

### 数据流

1. **采集**：mingjing-probe 通过 Hermes Hook 系统注册 9 个生命周期回调，静默采集事件
2. **写入**：事件写入 `~/.ming/hot/*.jsonl`（热轨），这是唯一写入通道
3. **归档**：Archiver 进程持续轮询热轨，将事件批量写入 `~/.ming/ming.db`（SQLite）
4. **诊断**：LIT Lite 诊断引擎读取数据库中的事件，与 157 条疾病规则比对
5. **查询**：用户通过 `ming` CLI 获取诊断结果

## 安装与启用

### 前置条件

- **Python 3.10+**（乾坤镜 Standalone 模式零第三方依赖，仅标准库）

### Step 1: 获取 ming-run 运行包

ming 以独立运行包 `ming-run/` 发布，解压后包含完整后端和探针适配层：

```
~/ming-run/
├── ming                    # 统一入口（start/stop/triage/dx/web 等）
├── ming-run                # opencode 包装器探针
├── src/                    # 后端源代码
├── config/                 # diseases.yaml 规则定义
└── filters/                # 规则过滤器
```

### Step 2: 安装 mingjing-probe 插件到 Hermes

将 `ming-run/src/adapters/hermes_plugin/` 下的插件文件复制到 Hermes 插件目录：

```bash
mkdir -p ~/.hermes/plugins/mingjing-probe/
cp ~/ming-run/src/adapters/hermes_plugin/* ~/.hermes/plugins/mingjing-probe/

# 验证文件完整（应有 8 个文件）
ls ~/.hermes/plugins/mingjing-probe/
```

### Step 3: 启用插件

```bash
hermes plugins enable mingjing-probe
```

或在 `~/.hermes/config.yaml` 中手动添加：

```yaml
plugins:
  enabled:
    - mingjing-probe
```

### Step 4: 设置环境变量

```bash
export MING_HOME=~/.ming
export MING_SYSTEM_NAME=hermes-agent
export MING_MODE=white
```

建议写入 `~/.bashrc` 或 `~/.zshrc`。

### Step 5: 启动 Archiver（归档器）

```bash
cd ~/ming-run && ./ming start
```

前台运行观察日志，确认无报错后 Ctrl-C，改用 --daemon 后台启动：

```bash
cd ~/ming-run && ./ming start --daemon
```

### Step 6: 验证

```bash
# 确认插件已启用
hermes plugins list

# 启动 Hermes 对话，进行 1-2 轮
hermes chat

# 检查热轨
ls -la ~/.ming/hot/
# 应看到 hermes-agent_*.jsonl

# 检查 Archiver 心跳
cat ~/.ming/.archiver_heartbeat
# 时间戳应接近当前时间
```

## 首次使用须知

**乾坤镜是被动观测工具**，不主动产生数据。事件由你正常使用 Hermes Agent 时产生，插件只是旁路采集。

### 安装后应该做什么

1. 确认 Hermes 和 Archiver 已启动
2.正常使用 Hermes 5-10 分钟——与 Agent 对话，触发工具调用
3. 运行 `./ming triage run` 查看诊断覆盖率

### 为什么刚装好时诊断数很少？

| 事件类型 | 什么时候才会产生 |
|---------|-----------------|
| `llm_invoke` | LLM API 调用完成时 |
| `tool_call` | Agent 调用工具时 |
| `session_start/end` | 会话生命周期事件 |
| `error` | 发生异常时 |
| `__health__`、`__touch__` | 探针心跳（30s 间隔，无需操作） |

首次装好只有心跳类事件，诊断数通常在 **15-25 条**。正常对话后逐步涨到 **50-69 条**。

### 不需要你做的事

- 不需要手动触发探针
- 不需要修改 Hermes 代码
- 不需要配置任何规则文件
- **正常使用即可，其他全自动**

## 诊断规则覆盖层

| 层 | 缩写 | Hermes 可诊断数 | 说明 |
|----|------|----------------|------|
| Tool | TLT | 15/15 | 工具执行（耗时/错误/输出/输入去重） |
| Model | MDL | 10/10 | 模型调用（Token/延迟/输出/步骤比） |
| Agent | AGT | ~20 | Agent 编排（步骤边界/死循环/耗时/错误恢复） |
| System | SYS | ~20 | 系统资源（内存/FD/线程/IO/磁盘/swap） |
| Probe | PRB | 7/8 | 探针自身健康 |
| Data Quality | DQT | 2/6 | 数据质量 |
| Network | NET | ~5 | 部分 HTTP 层指标可用 |
| Memory | MEM | - | 框架限制，不可修复 |
| Business | BIZ | - | 需业务系统主动埋点 |

## ming CLI 命令参考

`./ming` 是乾坤镜统一入口，所有命令在 `~/ming-run/` 目录下运行：

```bash
cd ~/ming-run
./ming <command> [args]
```

### 日常最常用

| 命令 | 用途 |
|------|------|
| `./ming report` | **体检中心报告**：逐实例健康（4 级）+ 疾病列表 + 分诊覆盖率 + 可用操作 |
| `./ming report --days 7` | 近一周综合报告（自动感知忽略/归档/复位状态） |
| `./ming report --json` | JSON 格式（含 instances 数组，每实例健康/疾病/P0-P2） |
| `./ming status` | 查看归档器运行状态 + 实例健康一览 |
| `./ming upgrade` | **一键升级乾坤镜到最新版并自动重启** |
| `./ming instance-list` | 紧凑表格：实例、探针、健康、P0/P1/P2、复位/归档标记 |

### 疾病操作（体检中心）🆕 v0.11.7

| 命令 | 用途 |
|------|------|
| `./ming ignore <fault_id> -s <system>` | 暂忽略某疾病（24h 内不展示，仍记录） |
| `./ming archive-disease <fault_id> -s <system>` | 永久归档某疾病（不计入健康评估） |
| `./ming restore <fault_id> -s <system>` | 取消忽略/取消归档 |
| `./ming reset <system>` | 健康复位某实例（标记健康，新病出现自动取消） |
| `./ming reset-status [system]` | 查看健康复位状态 |

### 诊断查询（dx 子命令）

`./ming dx` 支持预定义查询，通过参数指定查询名：

| 命令 | 用途 |
|------|------|
| `./ming dx recent_p0` | 最近 P0 级诊断 |
| `./ming dx recent_p1` | 最近 P1 级诊断 |
| `./ming dx event_stats` | 事件类型统计 |
| `./ming dx system_stats` | 系统维度统计 |
| `./ming dx unconfirmed` | 待确认的诊断 |

加 `--limit N` 控制返回条数（默认 20），加 `--json` 输出 JSON：

```bash
./ming dx recent_p0 --limit 10 --json
```

### 分诊与自检

| 命令 | 用途 |
|------|------|
| `./ming triage run` | 运行分诊（扫描规则覆盖） |
| `./ming triage run --json` | 分诊结果（JSON，含 rule_status 和 suggestions） |
| `./ming triage run --no-diagnose --json` | 仅扫描，跳过诊断生成 |
| `./ming triage status` | 分诊快照状态 |
| `./ming triage report` | 分诊报告（含各层规则详情） |
| `./ming self-check` | 系统自检（退出码: 0=正常 1=警告 2=严重） |

### 数据管理

| 命令 | 用途 |
|------|------|
| `./ming admin vacuum` | 压缩数据库 |
| `./ming admin forget --session-id <id> --confirm` | 删除某会话数据 |
| `./ming archive list` | 查看冷轨归档文件 |
| `./ming archive verify --file <file>` | 验证归档哈希 |
| `./ming verify` | 校验哈希链完整性 |

### Web 面板

```bash
./ming web serve              # 启动 Web UI（端口 18088）
./ming web serve --port 9090  # 自定义端口
./ming web export             # 导出数据
```

访问 `http://localhost:18088` 查看实时数据。

## JSON 输出格式

### `./ming triage run --json`

```json
{
  "snapshot": {
    "summary": { "total": 157, "ready": 69, "degraded": 8, "blocked": 80 },
    "rule_status": {
      "MDL-101": { "status": "ready", "reason": "llm_invoke 有 latency_ms/tokens" },
      "MEM-305": { "status": "blocked", "reason": "缺少 memory_retrieve 事件类型" }
    },
    "suggestions": [
      { "field": "memory_latency_ms", "disease_count": 5 }
    ]
  },
  "diagnoses_generated": true
}
```

### `./ming report --json`

```json
{
  "generated_at": 1714550400.0,
  "period_days": 1,
  "global_health": "sub_healthy",
  "global_health_label": "亚健康",
  "total_instances": 3,
  "healthy_count": 1,
  "unhealthy_count": 2,
  "active_faults": 5,
  "p0": 0, "p1": 2, "p2": 3,
  "dismissed": 0,
  "archived": 0,
  "probe": { "online": 2, "offline": 1, "never": 0 },
  "archiver_alive": true,
  "archiver_age_sec": 1.2,
  "db_size_mb": 41.8,
  "hot_files": 2,
  "cold_files": 156,
  "triage": {
    "ready": 89, "total": 157,
    "layers": { "mdl": {"ready": 10}, "tlt": {"ready": 15} }
  },
  "instances": [
    {
      "name": "hermes-agent",
      "health": "sub_healthy",
      "health_label": "亚健康",
      "p0": 0, "p1": 2, "p2": 1,
      "active": [
        { "severity": "P1", "fault_id": "MDL-101", "name": "LLM 调用延迟偏高", "occurrence_count": 8 }
      ],
      "dismissed_count": 0,
      "archived_count": 0,
      "probe_age_seconds": 3300,
      "reset": false
    }
  ],
  "per_system_triage": {
    "hermes-agent": { "ready": 89, "total": 157 }
  }
}
```

## 事件类型参考（Hermes 产生）

| 事件类型 | 触发条件 | 包含关键数据 |
|---------|---------|-------------|
| `session_start` | 会话开始 | session_id, model, platform |
| `session_end` | 会话结束 | session_id, completed, interrupted |
| `llm_invoke_start` | API 请求开始 | task_id, model, provider, base_url |
| `llm_invoke` | API 请求完成 | api_duration, finish_reason, usage |
| `llm_call_start` | LLM 调用开始 | session_id, user_message |
| `tool_call_start` | 工具开始 | tool_name, tool_call_id |
| `tool_call` | 工具完成 | tool_name, result, duration_ms, success |
| `subagent_stop` | 子 Agent 停止 | 生命周期事件 |

## 诊断严重度

| 级别 | 含义 | 响应 |
|------|------|------|
| **P0** | 系统级阻塞（归档器离线、数据损坏） | 立即处理 |
| **P1** | 性能退化（LLM 延迟高、工具频繁失败） | 尽快处理 |
| **P2** | 质量警告（输出不稳定、数据不完整） | 关注即可 |

## 常见用户问题 → 对应命令

| 用户说 | 用 TerminalTool 执行 |
|--------|---------------------|
| "系统最近怎么样？" | `cd ~/ming-run && ./ming report --json` |
| "帮我诊断一下" | `cd ~/ming-run && ./ming triage run --json` |
| "有哪些实例？谁不健康？" | `cd ~/ming-run && ./ming instance-list` |
| "最近有严重问题吗？" | `cd ~/ming-run && ./ming dx recent_p0` |
| "API 为什么慢？" | `./ming report --json`，看 instances.active |
| "工具调用有问题吗？" | `./ming report --json`，看 instances.active 中 TLT 层 |
| "忽略这个疾病 TLT-301" | `cd ~/ming-run && ./ming ignore TLT-301 -s hermes-agent` |
| "永久归档这个疾病" | `cd ~/ming-run && ./ming archive-disease TLT-301 -s hermes-agent` |
| "取消忽略/归档" | `cd ~/ming-run && ./ming restore TLT-301 -s hermes-agent` |
| "复位健康基线" | `cd ~/ming-run && ./ming reset hermes-agent` |
| "现在能诊断哪些问题？" | `cd ~/ming-run && ./ming triage status --json` |
| "归档器在运行吗？" | `cd ~/ming-run && ./ming status` |

## 数据目录

| 路径 | 内容 |
|------|------|
| `~/.ming/hot/*.jsonl` | 热轨事件（待归档） |
| `~/.ming/ming.db` | SQLite 数据库 |
| `~/.ming/cold/*.archive` | 冷轨归档文件 |
| `~/.ming/dismissed_diseases.json` | 已忽略疾病（24h 自动过期） |
| `~/.ming/archived_diseases.json` | 已归档疾病（永久隐藏） |
| `~/.ming/health_resets.json` | 健康复位记录 |
| `~/.ming/triage_snapshot.json` | 最近分诊快照 |
| `~/.ming/.archiver_heartbeat` | Archiver 心跳时间戳 |

## 升级乾坤镜

```bash
cd ~/ming-run
./ming upgrade
```

自动执行 `pip install --upgrade mingjing` → 检测运行方式 → 自动重启。

## 停止服务

```bash
cd ~/ming-run
./ming stop
```

## 与 Langfuse 等观测插件共存

安全共存，各自独立输出：
- Langfuse → Langfuse 后端
- 乾坤镜 → `~/.ming/hot/*.jsonl`

## 故障排查

| 问题 | 检查方法 |
|------|---------|
| 插件未加载 | `hermes plugins list` |
| 无 JSONL 文件 | `ls -la ~/.ming/hot/`（先触发一次对话） |
| Archiver 未运行 | `cat ~/.ming/.archiver_heartbeat`（时间戳是否接近现在） |
| 诊断数很少 | 正常对话后运行 `./ming triage run` |
| 热轨积压 | `ls -lh ~/.ming/hot/`，重启 `./ming stop` 后再 `./ming start --daemon` |

## 版本

| 版本 | 关键信息 |
|------|---------|
 | v0.11.10 (当前) | 三级存储压缩（zlib, -27% DB 体积）+ ming upgrade 一键升级 + token 统计修复 |
 | v0.11.7 | 体检中心模式：4 级健康评估、忽略/归档/复位/恢复操作、逐实例报告、`instance-list` / `ignore` / `archive-disease` / `restore` / `reset` / `reset-status` 命令；~89 条 Hermes-ready（合成 agent_step + OS 采样 + deep extract） |
| v0.11.6 | 157 条规则，69 条 Hermes-ready |
| v0.11.5 | 66 ready，role/error_type 字段 |

## 架构限制

以下在当前 Hermes 环境下**无法**捕获：
- **NET 网络层**——需 hook HTTP 层
- **BIZ 业务层**——需业务系统主动埋点
- **MEM 记忆层**——Hermes 框架未暴露 Memory Hook，属框架限制，不可通过插件修复
