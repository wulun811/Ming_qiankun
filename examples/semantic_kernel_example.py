# Semantic Kernel + 乾坤镜集成示例
# 
# 使用方式：
#   1. 安装 Semantic Kernel: pip install 'semantic-kernel>=1.40.0,<1.42.0'
#   2. 运行此示例: python examples/semantic_kernel_example.py
#   3. 查看热轨: cat ~/.ming/hot/semantic_kernel_*.jsonl

import os
import sys

# 添加 src 到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

def main():
    # 1. 初始化 Semantic Kernel
    import semantic_kernel as sk
    from semantic_kernel.connectors.ai.open_ai import OpenAIChatCompletion

    # 创建 Kernel
    kernel = sk.Kernel()

    # 添加 OpenAI 服务（需要 API key）
    # kernel.add_service(
    #     OpenAIChatCompletion(
    #         service_id="openai",
    #         ai_model_id="gpt-4",
    #         api_key=os.getenv("OPENAI_API_KEY"),
    #     )
    # )

    # 2. 初始化乾坤镜探针
    from adapters.probe_semantic_kernel import init_mingjing_probe
    tool_filter = init_mingjing_probe()

    # 添加 Tool Filter
    kernel.add_filter("function_invocation", tool_filter.tool_invocation_filter)

    print("Semantic Kernel + 乾坤镜集成示例")
    print("=" * 50)
    print()
    print("乾坤镜探针已初始化，将监控以下事件：")
    print("  - LLM 调用 (llm_invoke) - 通过 monkey-patch")
    print("  - 工具调用 (tool_call) - 通过 Filter")
    print()
    print("数据将写入: ~/.ming/hot/semantic_kernel_*.jsonl")
    print()
    print("查看实时数据：")
    print("  tail -f ~/.ming/hot/semantic_kernel_*.jsonl")
    print()
    
    # 3. 示例：创建一个简单的函数
    # from semantic_kernel.functions import kernel_function
    #
    # class MathPlugin:
    #     @kernel_function(description="Adds two numbers")
    #     def add(self, a: float, b: float) -> float:
    #         return a + b
    #
    # kernel.add_plugin(MathPlugin(), "math")
    #
    # # 运行函数
    # result = kernel.invoke(plugin_name="math", function_name="add", a=1, b=2)
    # print(f"Result: {result}")
    
    print("示例完成！")


if __name__ == "__main__":
    main()
