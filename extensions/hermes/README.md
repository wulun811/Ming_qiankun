# Hermes Agent 增强包 — mingjing-probe

乾坤镜 Hermes Agent 探针插件，通过 `ctx.register_hook()` 无侵入采集 Hermes 运行时事件。

## 安装

见 [after-install.md](after-install.md)。

## 升级兼容性

Hermes 基于官方 hooks 系统，探针使用**稳定 hook 接口**（`on_session_start`、`pre_llm_call`、`post_llm_call` 等）。Hermes 保证这些接口在版本升级时保持兼容。

**升级 Hermes 后：无需任何操作。**

如果探针在升级后不工作（极少见），请检查：

1. 确认插件仍在 `~/.hermes/plugins/mingjing-probe/` 目录下
2. 执行 `hermes plugins list` 检查 `mingjing-probe` 是否仍为 enabled
3. 检查热轨目录 `~/.ming/hot/` 是否有新的 `.jsonl` 文件
4. 如以上均正常，检查乾坤镜归档器是否运行：`python3 -m mingjing health`

## 目录说明

- `__init__.py` — 插件入口，注册 hook
- `plugin.yaml` — Hermes 插件声明文件
- `probe_uni.py` — 探针运行时
- `_python_base.py` / `_payload_builders.py` / `_extractors.py` — 基类和载荷构建器
- `skills/` — Hermes skill 定义
