# AgentScope + 乾坤镜集成示例
# 
# 使用方式：
#   1. 安装 AgentScope: pip install 'agentscope>=1.0.18,<1.1.0'
#   2. 运行此示例: python examples/agentscope_example.py
#   3. 查看热轨: cat ~/.ming/hot/agentscope_*.jsonl

import os
import sys

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def main():
    # 1. 初始化 AgentScope
    import agentscope
    
    # 配置 AgentScope（需要 API key）
    # agentscope.init(
    #     model_configs=[
    #         {
    #             "model_type": "dashscope_chat",
    #             "model_name": "qwen-plus",
    #             "api_key": os.getenv("DASHSCOPE_API_KEY"),
    #         }
    #     ]
    # )
    
    # 2. 初始化乾坤镜探针
    from adapters.probe_agentscope import init_mingjing_tracer
    init_mingjing_tracer()
    
    print("AgentScope + 乾坤镜集成示例")
    print("=" * 50)
    print()
    print("乾坤镜探针已初始化，将监控以下事件：")
    print("  - LLM 调用 (llm_invoke)")
    print("  - 工具调用 (tool_call)")
    print("  - Agent 调用 (agent_invoke)")
    print()
    print("数据将写入: ~/.ming/hot/agentscope_*.jsonl")
    print()
    print("查看实时数据：")
    print("  tail -f ~/.ming/hot/agentscope_*.jsonl")
    print()
    
    # 3. 示例：创建一个简单的 Agent
    # from agentscope.agents import DialogAgent
    # 
    # agent = DialogAgent(
    #     name="assistant",
    #     model_config_name="qwen-plus",
    #     sys_prompt="You are a helpful assistant."
    # )
    # 
    # # 运行 Agent
    # response = agent("Hello, how are you?")
    # print(f"Agent response: {response}")
    
    print("示例完成！")


if __name__ == "__main__":
    main()
