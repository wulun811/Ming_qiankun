# cluster_archiver_daemon.py —— v0.11.9m Cluster 归档器守护进程入口
# 用法: python cluster_archiver_daemon.py [--foreground]
# 环境变量: WQ_MYSQL_HOST, WQ_MYSQL_PORT, WQ_MYSQL_USER, WQ_MYSQL_DATABASE,
#           WQ_PROJECT_ID, MING_HOT_DIR
#           密码通过 credential_vault.get_secret("mysql.password") 或 MING_SECRET_MYSQL_PASSWORD 环境变量提供

import os
import sys
import time
import signal
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.cluster_pool import SimplePool
from src.cluster_archiver import ClusterArchiver
from src.credential_vault import get_secret

_archiver = None
_pool = None


def _graceful_shutdown(signum=None, frame=None):
    global _archiver, _pool
    if _archiver:
        _archiver.stop()
    if _pool:
        _pool.close_all()
    sys.exit(0)


def main():
    global _archiver, _pool
    foreground = "--foreground" in sys.argv
    project_id = os.getenv("WQ_PROJECT_ID", "default")

    _pool = SimplePool(
        host=os.getenv("WQ_MYSQL_HOST", "localhost"),
        port=int(os.getenv("WQ_MYSQL_PORT", "3306")),
        user=os.getenv("WQ_MYSQL_USER", "wq"),
        password=get_secret("mysql.password") or None,
        database=os.getenv("WQ_MYSQL_DATABASE", "ming"),
        max_conn=int(os.getenv("WQ_MYSQL_POOL_SIZE", "5")),
    )

    _archiver = ClusterArchiver(_pool, project_id=project_id)
    _archiver.start_daemon()

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    if foreground:
        print("Cluster 归档器前台运行，按 Ctrl+C 停止")

    try:
        while True:
            time.sleep(_archiver.flush_interval)
    except KeyboardInterrupt:
        _graceful_shutdown()


if __name__ == "__main__":
    main()
