---
name: mingjing-opencode
description: 乾坤镜 (Ming) — OpenCode 探针适配器。通过轮询 opencode.db 自动发射 llm_invoke、tool_call、error 事件，为 OpenCode 提供无侵入可观测性。
metadata: {"opencode": {"emoji": "🔮", "category": "diagnostics"}}
---

# 乾坤镜 OpenCode 适配器

## 架构

```
opencode (node) ──▶ opencode.db
                         │
              daemon_opencode.py (轮询)
                         │
              热轨 JSONL → Archiver → SQLite ming.db
                                          │
                                     Web 面板 / CLI
```

探针守护进程（`daemon_opencode.py`）每 2 秒轮询 `opencode.db`，提取新事件写入热轨。归档器异步消费热轨，写入 SQLite。

## 启动守护探针

```bash
# 推荐：后台守护（自动轮询 opencode.db）
nohup python3 src/adapters/daemon_opencode.py &

# 或通过 ming CLI（统一入口）
python3 src/ming.py start
```

探针注册后，Web 面板上会看到 `opencode (probe)` 条目（~21MB RSS）。

## 启动归档器 + Web 面板

```bash
# 归档器（后台守护）
cd ~/Ming_qiankun && MING_MODE=standalone python3 src/ming.py start --daemon

# Web 面板（端口 18088）
python3 src/plugins/web_dashboard/server.py --daemon --host 0.0.0.0 --port 18088
```

访问 `http://localhost:18088` 查看实时数据。
Web 面板右上角 `EN` / `中文` 按钮可切换语言（localStorage 持久化）。CLI 设置环境变量 `MING_LANG=en` 可切换为英文输出。

## 健康检查

```bash
# 查看探针心跳文件
ls -la ~/.ming/hot/ | grep opencode

# 检查归档器是否摄入
python3 src/cli_health.py

# 查看系统状态
python3 src/ming.py status
```

## 开关机自启

```bash
# 注册 systemd 服务（归档器 + Web 面板）
python3 src/ming.py service install

# 启动
sudo systemctl start ming-archiver ming-web

# 查看状态
sudo systemctl status ming-archiver ming-web
```

## 升级乾坤镜

```bash
# 一键升级到最新版（自动 pip install --upgrade + 重启）
python3 src/ming.py upgrade

# 或通过 CLI
ming upgrade
```

`ming upgrade` 自动升级后端代码并重启服务。探针守护进程（`daemon_opencode.py`）需手动重启：

```bash
pkill -f daemon_opencode.py
nohup python3 src/adapters/daemon_opencode.py &
```

## 停止服务

```bash
# 停止归档器 + Web 面板
python3 src/ming.py stop
python3 src/plugins/web_dashboard/server.py --stop

# 停止探针守护
pkill -f daemon_opencode.py
```

## 触发诊断

探针正常运行后，运行以下命令查看包含 OpenCode 的诊断报告：

```bash
python3 src/cli_report.py
```
