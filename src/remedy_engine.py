# remedy_engine.py —— v0.11.9m 处方引擎（规划中 🚧）
# 职责：加载 remedies.yaml + 场景匹配 + 模板渲染，零执行权限
#
# 状态：骨架已搭，待 LIT 事件订阅完成后接通
#   - ✓ 加载 config/remedies.yaml 的 tiered 处方
#   - ✓ 场景化覆盖匹配（environment/severity）
#   - ✓ 模板渲染（{{ system }} 等变量替换）
#   - ~ 输出格式（当前为 JSON list，待定）
#   - ✗ LIT 回调集成（触发自动执行）
#
# 数据流：config/remedies.yaml（项目模板）→ 用户复制到 ~/.ming/remedies/
#         → remedy_engine.py 加载 → LIT 诊断匹配 → prescribe() 输出
# 纯标准库，零第三方依赖（PyYAML 可选）
import json, os
from pathlib import Path

REMEDIES_DIR = Path(
    os.getenv("MING_REMEDIES_DIR", str(Path.home() / ".ming" / "remedies"))
)
SCENE = os.getenv("MING_SCENE", "default")


def _yaml_load(path):
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or []
    except ImportError:
        return _simple_yaml(path)


def _simple_yaml(path):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    result = []
    stack = [(0, result)]
    for line in lines:
        s = line.rstrip()
        if not s or s.lstrip().startswith("#"):
            continue
        indent, content = len(line) - len(s), s.lstrip()
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()
        _, container = stack[-1]
        if content.startswith("- "):
            item = content[2:].strip()
            if ":" in item:
                d = {}
                (container if isinstance(container, list) else []).append(d)
                k, _, v = item.partition(":")
                k = k.strip()
                v = v.strip().strip("'\"")
                if v:
                    d[k] = _pv(v)
                stack.append((indent + 2, d))
            elif isinstance(container, list):
                container.append(_pv(item))
        elif ":" in content:
            k, _, v = content.partition(":")
            k = k.strip()
            v = v.strip().strip("'\"")
            if v and isinstance(container, dict):
                container[k] = _pv(v)
            elif isinstance(container, dict):
                nc = {}
                container[k] = nc
                stack.append((indent + 2, nc))
    return result


def _pv(v):
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    if v in ("null", "~"):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def load_remedies():
    """加载 ~/.ming/remedies/ 下所有 .yaml 处方（config/remedies.yaml 为用户模板）"""
    registry = {}
    if not REMEDIES_DIR.exists():
        return registry
    for f in sorted(REMEDIES_DIR.glob("**/*.yaml")):
        try:
            for r in _yaml_load(f) or []:
                if not isinstance(r, dict) or "rule_id" not in r:
                    continue
                rid, scene = (
                    r["rule_id"],
                    r.get("applies_when", {}).get("environment", "default"),
                )
                if rid not in registry or scene == SCENE:
                    registry[rid] = r
        except Exception:
            continue
    return registry


def _render(obj, ctx):
    if isinstance(obj, str):
        r = obj
        for k, v in ctx.items():
            r = r.replace("{{ " + k + " }}", str(v)).replace("{{" + k + "}}", str(v))
        return r
    elif isinstance(obj, dict):
        return {k: _render(v, ctx) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_render(i, ctx) for i in obj]
    return obj


def prescribe(diagnosis, remedies):
    rid = diagnosis.get("rule_id") or diagnosis.get("fault_id")
    if not rid:
        return None
    conf, sev = diagnosis.get("confidence", 0), diagnosis.get("severity", "P2")
    remedy = remedies.get(rid)
    if not remedy:
        return None
    tiers = remedy.get("tiers", {})
    if sev == "P0" and conf >= 0.9:
        tier, tn = tiers.get("emergency"), "emergency"
    elif sev == "P0" and conf >= 0.7:
        tier, tn = tiers.get("immediate"), "immediate"
    elif sev in ("P1", "P2") or conf >= 0.5:
        tier = tiers.get("short_term") or tiers.get("long_term")
        tn = "short_term" if tiers.get("short_term") else "long_term"
    else:
        tier, tn = None, None
    if not tier:
        return None
    ctx = {"system": diagnosis.get("system", "unknown")}
    payload = diagnosis.get("payload", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    if isinstance(payload, dict):
        for k in ("step_id", "agent_id", "tool_name"):
            if k in payload:
                ctx[k] = payload[k]
    actions = [_render(a, ctx) for a in tier.get("actions", [])]
    for a in actions:
        if isinstance(a, dict):
            a["dry_run"] = True
    return {
        "diagnosis": {
            "rule_id": rid,
            "severity": sev,
            "confidence": conf,
            "system": diagnosis.get("system"),
        },
        "prescription": {
            "tier": tn,
            "summary": tier.get("summary", ""),
            "rationale": tier.get("rationale", ""),
            "actions": actions,
            "requires_human_confirm": any(
                isinstance(a, dict) and a.get("human_confirm") for a in actions
            ),
            "dry_run_only": True,
        },
    }


def format_prescription_cli(rx):
    if not rx:
        return "暂无修复建议"
    p = rx["prescription"]
    lines = [f"\n 处方（{p['tier']} tier）："]
    for i, act in enumerate(p["actions"], 1):
        if isinstance(act, dict):
            lines.append(f"   {i}. [{act.get('type', '?')}] {act.get('target', '')}")
            if act.get("dry_run"):
                lines.append("      [DRY-RUN] 生成操作文件，请审阅后执行")
            if act.get("human_confirm"):
                lines.append("      [需人工确认]")
        else:
            lines.append(f"   {i}. {act}")
    if p.get("rationale"):
        lines.append(f"\n 依据: {p['rationale']}")
    if p.get("requires_human_confirm"):
        lines.append("\n [!] 此处方需人工确认后执行")
    return "\n".join(lines)


def main():
    diag = json.loads(input())
    remedies = load_remedies()
    print(json.dumps(prescribe(diag, remedies), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
