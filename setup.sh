#!/bin/bash
# 乾坤镜 Mingjing — 一键环境初始化
set -e

echo "🔮 乾坤镜 Mingjing — 环境初始化"

# 创建运行时目录
mkdir -p ~/.ming/{hot,cold,snapshots,reports,exports,plugins}
echo "✅ 运行时目录已创建: ~/.ming/"

# 检查 Python 版本
python3 -c "
import sys
v = sys.version_info
if v.major < 3 or (v.major == 3 and v.minor < 10):
    print('❌ 需要 Python 3.10+, 当前: {}.{}'.format(v.major, v.minor))
    sys.exit(1)
print('✅ Python {}.{} 合格'.format(v.major, v.minor))
"

# 可选：安装 pyyaml（Web 面板 export 需要）
if python3 -c "import yaml" 2>/dev/null; then
    echo "✅ pyyaml 已安装"
else
    echo "ℹ️  pyyaml 未安装（仅 Web Dashboard export 需要）"
    echo "   如需安装: pip3 install pyyaml"
fi

echo ""
echo "💡 启动归档器: MING_MODE=standalone python3 src/ming.py start"
echo "💡 启动 Web:    python3 src/ming.py web start"
echo "💡 查看诊断:    python3 src/cli.py dx list"
echo "💡 Web 面板:    http://localhost:18088"
echo ""
echo "🔮 初始化完成"
