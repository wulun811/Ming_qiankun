# OpenClaw 增强包 — mingjing-probe

乾坤镜 OpenClaw 探针插件，通过 OpenClaw 官方 hooks 扩展点无侵入采集 Agent 运行时事件。

## 安装

1. 将 `extensions/openclaw/` 目录复制到 OpenClaw 扩展路径：

```bash
cp -r extensions/openclaw ~/.openclaw/extensions/mingjing-probe
```

2. 在 `~/.openclaw/openclaw.json` 中启用：

```json
{"plugins": {"entries": {"mingjing-probe": {"enabled": true}}}}
```

或通过 CLI：`openclaw config set plugins.entries.mingjing-probe.enabled true`

3. 重启 Gateway：`openclaw gateway`

> 探针懒加载，首次启动约 2-4 分钟出现 `[mingjing-probe] Probe runtime started`。

## 目录说明

- `index.js` — 插件入口
- `openclaw.plugin.json` — OpenClaw 插件声明
- `probe-runtime.js` — 探针运行时
- `skills/` — OpenClaw skill 定义
