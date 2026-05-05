# mingjing-probe — Hermes Agent 安装指南

## 快速安装

### 方式 1: 手动安装（推荐开发期使用）

```bash
# 1. 创建插件目录
mkdir -p ~/.hermes/plugins/mingjing-probe/

# 2. 复制插件文件（从乾坤镜项目根目录执行）
cp extensions/hermes/* ~/.hermes/plugins/mingjing-probe/

# 3. 验证插件文件完整
ls ~/.hermes/plugins/mingjing-probe/
# 应看到: __init__.py  plugin.yaml  probe_uni.py  _python_base.py
#        _payload_builders.py  _extractors.py  after-install.md

# 4. 启用插件
hermes plugins enable mingjing-probe

# 5. 重启 Hermes
hermes restart
```

### 方式 2: 从 Git 仓库安装（发布后）

```bash
hermes plugins install https://github.com/mingjing-probe/hermes-plugin.git
hermes plugins enable mingjing-probe
hermes restart
```

## 环境变量配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MING_SYSTEM_NAME` | 被监测系统标识符 | `hermes-agent` |
| `MING_MODE` | 探针模式（white=白盒，black=黑盒） | `white` |
| `MING_HOT_DIR` | JSONL 热轨输出目录 | `~/.ming/hot` |
| `MING_HOME` | 乾坤镜数据目录 | `~/.ming` |
| `MING_CONTENT_MAX_LEN` | 内容捕获最大长度（0=不限制） | `2000` |

在 `.bashrc` 或 `.env` 中设置：
```bash
export MING_SYSTEM_NAME="my-hermes"
export MING_MODE="white"
```

## 验证安装

```bash
# 1. 确认插件已启用
hermes plugins list

# 2. 启动 Hermes 并进行一次对话
hermes chat

# 3. 检查 JSONL 输出
ls -la ~/.ming/hot/
# 应看到 hermes-agent_*.jsonl 文件

# 4. 查看最新事件
tail -1 ~/.ming/hot/hermes-agent_*.jsonl | python -m json.tool
```

## 事件类型

插件注册了 8 个 Hermes 生命周期 Hook，产生以下事件：

| Hook | 事件类型 | 说明 |
|------|----------|------|
| `on_session_start` | `session_start` | 会话开始（含 platform、model） |
| `pre_api_request` | `llm_invoke_start` | API 请求开始（含 request meta、tokens） |
| `pre_llm_call` | `llm_call_start` | LLM 调用开始 |
| `post_llm_call` | `llm_invoke` | LLM 调用完成（含 finish_reason、tokens、延迟） |
| `pre_tool_call` | `tool_call_start` | 工具调用开始 |
| `post_tool_call` | `tool_call` | 工具调用完成（含结果、执行时间、成功/失败） |
| `subagent_stop` | `subagent_stop` | 子 Agent 停止 |
| `on_session_end` | `session_end` | 会话结束（含 completed、interrupted 状态） |

## 日志位置

| 目录 | 内容 | 说明 |
|------|------|------|
| `~/.ming/hot/*.jsonl` | 乾坤镜热轨事件 | 本插件输出，供归档器消费 |
| `~/.hermes/logs/` | Hermes 内建日志 | 由 Hermes 自身管理 |

两个日志目录独立共存，互不影响。

## 卸载

```bash
hermes plugins disable mingjing-probe
hermes restart
# 可选：删除插件文件
rm -rf ~/.hermes/plugins/mingjing-probe/
```

## 与 Langfuse 插件共存

乾坤镜插件和 Langfuse 插件使用同一套 Hermes Hook 系统，可安全共存。
两者互不干扰，各自写入自己的输出：

- Langfuse → Langfuse 后端（云端 APM）
- 乾坤镜 → `~/.ming/hot/*.jsonl`（本地离线诊断）

## 故障排查

| 问题 | 排查 |
|------|------|
| 插件未加载 | 检查 `hermes plugins list` 是否显示 `mingjing-probe (enabled)` |
| 无 JSONL 文件 | 检查 `~/.ming/hot/` 目录权限、磁盘空间 |
| 事件不完整 | 查看 `_incomplete` 和 `_integrity_hint` 字段 |
| 探针自身错误 | 搜索 JSONL 中 `event_type == "error"` 且 `hook` 字段非空 |
