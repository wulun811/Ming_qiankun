# archiver_daemon.py —— 守护进程入口
# 用法: python archiver_daemon.py [--foreground]

import sys, time, signal
from archiver import Archiver


def main():
    foreground = "--foreground" in sys.argv

    archiver = Archiver()
    archiver.start_daemon()

    def handle_signal(signum, frame):
        archiver.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    if foreground:
        print("归档器前台运行，按 Ctrl+C 停止")

    try:
        while True:
            time.sleep(archiver.FLUSH_INTERVAL)
    except KeyboardInterrupt:
        archiver.stop()
        print("归档器已停止")


if __name__ == "__main__":
    main()
