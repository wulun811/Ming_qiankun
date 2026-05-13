#!/bin/bash
# 乾坤镜适配器版本兼容性测试脚本
# 用法：bash tests/test_version_compat.sh [adapter]
# 示例：bash tests/test_version_compat.sh agentscope

set -e

ADAPTER=$1

if [ "$ADAPTER" = "agentscope" ]; then
    echo "=== AgentScope 版本兼容性测试 ==="
    VERSIONS=("1.0.18" "1.0.19.post1")
    PACKAGE="agentscope"
    TEST_FILE="tests/test_probe_agentscope_mock.py"
elif [ "$ADAPTER" = "semantic-kernel" ]; then
    echo "=== Semantic Kernel 版本兼容性测试 ==="
    VERSIONS=("1.40.0" "1.41.3")
    PACKAGE="semantic-kernel"
    TEST_FILE="tests/test_probe_semantic_kernel_mock.py"
else
    echo "用法：bash tests/test_version_compat.sh [agentscope|semantic-kernel]"
    exit 1
fi

# 记录原始版本
ORIGINAL_VERSION=$(pip show $PACKAGE 2>/dev/null | grep Version | awk '{print $2}' || echo "none")

for v in "${VERSIONS[@]}"; do
    echo ""
    echo "--- 测试 $PACKAGE v$v ---"
    pip install $PACKAGE==$v --quiet
    
    if [ -f "$TEST_FILE" ]; then
        python -m pytest $TEST_FILE -v --tb=short
        echo "✅ $PACKAGE v$v 测试通过"
    else
        echo "⚠️  测试文件不存在：$TEST_FILE"
        echo "跳过测试（适配器尚未开发）"
    fi
done

# 恢复原始版本（如果存在）
if [ "$ORIGINAL_VERSION" != "none" ]; then
    echo ""
    echo "--- 恢复原始版本 $ORIGINAL_VERSION ---"
    pip install $PACKAGE==$ORIGINAL_VERSION --quiet
fi

echo ""
echo "=== 测试完成 ==="