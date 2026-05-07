# probes.py —— 已知探针配置
# 职责：统一管理 KNOWN_PROBES 集合，避免三处硬编码
# 依赖：标准库 only

KNOWN_PROBES = {"tusunsun", "langchain", "openclaw", "mingjing", "opencode", "hermes"}


def is_known_probe(system_name: str) -> bool:
    """判断是否为已知探针（支持 _ 和 - 前缀匹配）"""
    return any(
        system_name == name
        or system_name.startswith(name + "_")
        or system_name.startswith(name + "-")
        for name in KNOWN_PROBES
    )
