# CONTRIBUTING.md — 乾坤镜贡献指南

## 快速开始

```bash
git clone https://github.com/wulun811/Ming_qiankun.git
cd Ming_qiankun
python -m pytest tests/ -v
python scripts/code_review.py --src src --docs docs --tests tests
```

## 代码量预算

乾坤镜采用**分类预算制**，配置在 [`config/budget.json`](config/budget.json)。每个分类有总行数上限，超出会导致 CI 失败。

| 分类 | 预算 | 说明 |
|------|------|------|
| core_base | 1,500 行 | 探针 + 归档器 + CLI + 查询 |
| cluster_extension | 600 行 | Cluster 扩展 |
| plugin_layer | 1,000 行 | 插件层 |
| adapter_layer | 1,200 行 | 适配器 + 基类 |

**单个文件 max_lines** 硬编码在 `scripts/code_review.py` 的 `CODE_LIMITS` 字典中，这是代码审查规则，不是运行时配置。

## 铁律（违反即 P0，PR 会被拒绝）

| 铁律 | 说明 |
|------|------|
| Standalone 零第三方依赖 | 仅 Python 标准库 |
| Cluster 唯一额外依赖 | 仅 `pymysql` |
| 探针纯粹 | `probe_uni.py` 不 import sqlite3/pymysql |
| 唯一写入者 | 只有归档器可写入持久层 |
| 只读对外 | 查询模块使用只读连接 |

## 提交 PR 前检查清单

- [ ] `python -m pytest tests/ -v` 全绿
- [ ] `python scripts/code_review.py --src src --docs docs --tests tests` 无 P0/P1
- [ ] 新增功能有对应测试
- [ ] 更新相关文档

## Git 提交规范

```
<type>(<scope>): <subject>

type: feat | fix | docs | test | refactor | chore
scope: probe | archiver | adapter | docs | test | ci
```

## 适配器开发

适配器位于 `src/adapters/`，遵循统一模式：

1. 从 `_python_base.py` 导入 `make_adapter_init` 和 `instrumented`
2. 定义 `_monkey_patch(probe)` 函数，用 try/except ImportError 包裹
3. 导出 `init_*_probe = make_adapter_init(_monkey_patch, "system_name")`

新增适配器需添加 Mock 测试（参考 `tests/test_probe_*_mock.py`）。
