---
name: mingjing-tusunsun
description: 乾坤镜 (Ming) — 土行孙探针适配层。kunxiansuo-bundle 插件内置 probe_bridge，启动时自动加载，零配置。
metadata: {"tusunsun": {"emoji": "🔮", "category": "diagnostics"}}
---

# 乾坤镜 土行孙适配器

## 架构

```
tusunsun-gw (systemd --user)
  └─ kunxiansuo-bundle 插件
       ├─ bundle.init() → startProbeBridge(core)
       │    ├─ probe_node.init('tusunsun', 'white')
       │    ├─ subscribe llm.call/response, tool.execute/result 等事件
       │    └─ heartbeat: __touch__ 每 30s, __health__ 每 120s
       ├─ probe_node._flush() → ~/.ming/hot/tusunsun_{ts}_{pid}.jsonl
       └─ Archiver (独立进程) → 消费 → SQLite ming.db
```

## 启动时自动加载探针

tusunsun 以 `systemctl --user start tusunsun-gw` 启动时：

1. `cli.js` → `core.start()` → `pm.loadAll()`
2. `gateway` scene 包含 `kunxiansuo-bundle` 插件
3. `bundle.init()` 调用 `startProbeBridge(core)`
4. `probe_node.init('tusunsun', 'white')` 初始化探针
5. 开始写入 `~/.ming/hot/tusunsun_*.jsonl`

**无需额外配置，启动即自带探针。**

## 环境变量（可选）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MING_HOT_DIR` | `~/.ming/hot` | 热轨目录，自动创建 |
| `probe.system` (config key) | `tusunsun` | 实例名 |
| `probe.mode` (config key) | `white` | 模式: white/black |

可通过 `~/.tusunsun/config.json` 设置：
```json
{
  "probe": {
    "system": "tusunsun",
    "mode": "white"
  }
}
```

## 验证探针

```bash
# 1. 确认 probe_bridge 已启动
journalctl --user -u tusunsun-gw --no-pager | grep "Probe bridge started"

# 2. 检查热轨文件
ls -la ~/.ming/hot/ | grep tusunsun

# 3. 或查看冷轨（归档后的文件）
ls ~/.ming/cold/ | grep tusunsun | tail -5

# 4. 在 Mingjing Web 面板 (http://localhost:18088) 查看 tusunsun 实例
```

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 无 tusunsun 热轨文件 | kunxiansuo-bundle 未加载 | `systemctl --user restart tusunsun-gw` 后检查日志 |
| 探针写入数小时前停止 | tusunsun 空闲，无事件 | 这是正常的——探针只在有事件时才写入。__touch__ 每 30s 写入一条 |
| 归档器显示 tusunsun 离线 | 无事件超过 N 分钟 | 检查客户端是否连接到 tusunsun gateway 产生了对话 |
| Web 面板看不到 tusunsun | 未注册/白名单过滤 | Mingjing `KNOWN_PROBES` 包含 tusunsun，无需额外配置 |

## 升级后端（归档器 + Web 面板）

```bash
cd ~/Ming_qiankun
python3 src/ming.py upgrade
```

自动升级乾坤镜后端代码并重启。探针随 tusunsun 进程管理，不受后端升级影响。

## 停止探针

探针随 tusunsun 进程启停，无需单独操作：

```bash
systemctl --user stop tusunsun-gw
```
