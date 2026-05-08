# ming_probe_langchain —— 乾坤镜 LangChain 自动探针
# pip install 即自动激活，零代码改动
# 支持：
#   - 自动上报所有 LangChain 调用事件到乾坤镜
#   - get_health() 直接查询乾坤镜健康状态
#   - MingHealthTool 作为 LangChain Tool，让 Agent 自主查健康

from .probe_langchain import init_langchain_probe
from .health import get_health, get_health_text

try:
    from .health import MingHealthTool
except ImportError:
    MingHealthTool = None

__all__ = ["init_langchain_probe", "get_health", "get_health_text", "MingHealthTool"]
__version__ = "0.11.12.post8"
