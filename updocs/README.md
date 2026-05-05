# 乾坤镜 Mingjing v0.11.9-alpha

> **AI Agent 诊断折射阵列** — 零侵入观测 LLM 调用、工具执行、记忆检索与 Agent 编排  
> **乾坤镜是 LIT 1.4 的轻量折射阵列，不是独立诊断中台。**

---

## 一句话定位

乾坤镜是**轻量级可观测性基础设施**，为 LangChain、LlamaIndex、CrewAI、OpenHands、AutoGPT、Hermes、OpenClaw、Vercel AI SDK、土行孙等 AI Agent 框架提供**无侵入诊断能力**。

**核心约束**：0 LLM、0 入侵、0 写回、0 联网（Standalone 模式）、仅只读查询。

---

## 核心特性

| 特性 | 说明 |
|------|------|
| **零侵入** | monkey-patch 静默降级，不修改业务代码 |
| **零 LLM 调用** | 157 条确定性诊断规则（SQL/YAML），不调用任何大模型 |
| **零第三方依赖** | Standalone 模式仅 Python 标准库 |
| **超轻量** | RSS &lt;40<!--M=memory_mb-->MB，底座代码 &lt;2000<!--M=budget_base--> 行 |
| **自动诊断** | 10<!--M=always_on_rules--> 条 Always-On 实时告警 + 157<!--M=disease_rules--> 条分诊规则（Always-On 当前仅适配土行孙） |
| **跨框架统一** | 16+ 适配器，病症"只认字段不认系统" |
| **证据链审计** | SHA-256 哈希链 + 完整性评分 |
| **插件无上限** | 诊断/Web 目镜可通过 YAML 契约替换 |

---

## 快速开始

### 1. 克隆

```bash
git clone https://github.com/wulun811/Ming_qiankun.git
cd Ming_qiankun
```

### 2. 运行（零依赖）

```bash
MING_MODE=standalone python src/ming.py start
```

### 3. 发射测试事件

> **注意**：以下命令必须在项目根目录执行。

```bash
python -c "
import sys; sys.path.insert(0, 'src')
from probe_uni import ProbeUni
p = ProbeUni(system='demo', mode='white')
p.emit('llm_invoke', {
    'layer_agent': {'step_id': 'test', 'session_id': 's1'},
    'layer_llm': {'model': 'gpt-4', 'input_tokens': 100},
    'layer_network': {'target_host': 'api.openai.com'}
})
print('Event emitted!')
"
```

### 4. 查看诊断

> **两个入口**：`src/ming.py` 负责服务管理（启动/停止），`src/cli.py` 负责查询诊断。运行 `python src/cli.py --help` 查看所有命令。

```bash
python src/cli.py dx list
```

### 5. 打开 Web 目镜

```bash
python src/ming.py web start
# 浏览器访问 http://localhost:18088
```

---

## 性能基准

| 测试场景 | 事件数 | 归档成功率 | RSS 内存 |
|---------|--------|-----------|---------|
| 15min × 1000/s | 882K | 99.9% | 18MB |
| 3min × 5000/s | 884K | 100% | 18MB |
| 75s × 2000/s（15 万） | 150K | 100% | **38MB** |

> **常驻内存 <40MB**，归档器 + Web 面板约 30MB，0 LLM 调用，0 网络出站。

---

## 文档索引

| 文档 | 说明 |
|------|------|
| [00_快速上手.md](./00_快速上手.md) | 5 分钟快速体验完整链路 |
| [01_架构设计.md](./01_架构设计.md) | 设计理念、核心铁律、双模式架构 |
| [02_探针与适配器.md](./02_探针与适配器.md) | 探针系统、16+ 适配器生态 |
| [03_诊断系统.md](./03_诊断系统.md) | lit_lite 插件、157 条规则、Always-On |
| [04_数据模型.md](./04_数据模型.md) | SQLite 表结构、哈希链、完整性 |
| [05_接口规范.md](./05_接口规范.md) | CLI、Web API、统一查询入口 |
| [06_运维手册.md](./06_运维手册.md) | 环境变量、部署、故障排查 |
| [07_测试体系.md](./07_测试体系.md) | 性能基准、389 项测试、冒烟测试 |
| [08_乾坤镜OpenClaw用户指南.md](./08_乾坤镜OpenClaw用户指南.md) | OpenClaw 框架集成指南 |

---

## 许可证

**Business Source License 1.1**

- 全球年收入低于 10 万美元的公司和个人可免费生产使用
- 非生产用途（开发、测试、评估）无收入限制
- 详见 [LICENSE](LICENSE) 文件

---

**乾坤镜 v0.11.9-alpha — 纯数据底座 + 插件执行器 + 三层解耦。底座轻量，生态无上限。**