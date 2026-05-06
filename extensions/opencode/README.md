# 乾坤镜 OpenCode 适配器

通过轮询 `opencode.db` 读取会话数据，自动发射热轨事件。**包装器模式** — 作为 opencode CLI 的包装进程运行。

## 使用方法

```bash
# 方式一：用乾坤镜启动 opencode（推荐）
python src/adapters/probe_opencode_wrapper.py opencode

# 方式二：opencode 已在运行，单独启动守护进程
python src/adapters/daemon_opencode.py --daemon

# 方式三：回溯历史数据
python src/adapters/backfill_opencode.py
```

## 事件类型

| 事件 | 来源 | 说明 |
|------|------|------|
| `llm_invoke` | message/part 表 | LLM 调用与响应 |
| `tool_call` | part 表 | 工具调用与结果 |
| `error` | part 表 | 工具调用异常 |

## 原理

1. 读取 `~/.local/share/opencode/opencode.db`
2. 轮询 `message` 和 `part` 表的新增记录（基于游标）
3. 转换为标准 JSONL 事件写入热轨目录 `~/.ming/hot/`
4. 由乾坤镜归档器自动摄入

## 升级兼容性

OpenCode 适配器通过**只读 DB 轮询**工作，不依赖 OpenCode 运行时 API。

**升级 OpenCode 后：检查探针是否仍在工作。**

升级 OpenCode 版本可能改变 `opencode.db` 的数据库 schema（表结构或字段名）。探针会在启动时自动检测表结构，如发现不兼容会输出警告到 stderr：

```
[MING-WARN] opencode DB table 'message' missing columns: ...
```

验证方法：

1. 启动 OpenCode 后运行一次包装器：`python src/adapters/probe_opencode_wrapper.py opencode --version`
2. 检查 stderr 是否有 schema 警告
3. 检查热轨目录 `~/.ming/hot/` 是否有新文件
4. 确认归档器能正常消费：`python -m mingjing health`

**当前测试版本**：OpenCode v1.3.13。如升级后 schema 变更，请到 GitHub 提交 Issue 报告新版 schema。

## 零第三方依赖

纯 Python 标准库实现，不需要 pip 安装任何额外包。probe 代码已随主包 `mingjing` 发布。
