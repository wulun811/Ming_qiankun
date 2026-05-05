# Hermes Agent 增强包 — mingjing-probe

乾坤镜 Hermes Agent 探针插件，通过 `ctx.register_hook()` 无侵入采集 Hermes 运行时事件。

## 安装

见 [after-install.md](after-install.md)。

## 目录说明

- `__init__.py` — 插件入口，注册 hook
- `plugin.yaml` — Hermes 插件声明文件
- `probe_uni.py` — 探针运行时
- `_python_base.py` / `_payload_builders.py` / `_extractors.py` — 基类和载荷构建器
- `skills/` — Hermes skill 定义
