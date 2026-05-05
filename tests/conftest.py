import sys
from pathlib import Path
from unittest.mock import patch

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 全局 patch atexit.register，避免 ProbeUni 多实例创建时
# 退出阶段 atexit 链式 fcntl 文件锁竞争导致进程不退出
_atexit_guard = patch("atexit.register", lambda f: None)
_atexit_guard.start()
