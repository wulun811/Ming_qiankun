# archiver_daemon.py —— 守护进程入口
# 用法: python archiver_daemon.py [--foreground]
# 特性：自动监控归档器线程健康，线程死亡后自动重启（限流 10 次/60 秒）

import sys, time, signal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from archiver import Archiver


def main():
    foreground = "--foreground" in sys.argv

    archiver = Archiver()
    archiver.start_daemon()

    # 自动监控归档器线程健康，线程死亡后自动重启
    restart_count = 0
    max_restarts = 10
    restart_cooldown = 60
    last_restart = 0

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

            # 检查归档器线程是否死亡（连续错误达上限后会退出）
            if not archiver._alive or (
                archiver._daemon_thread and not archiver._daemon_thread.is_alive()
            ):
                now = time.time()
                if now - last_restart < restart_cooldown:
                    restart_count += 1
                    if restart_count >= max_restarts:
                        if foreground:
                            print("归档器重启次数过多，退出")
                        sys.exit(1)
                else:
                    restart_count = 1
                last_restart = now
                if foreground:
                    print(f"归档器线程已死亡，正在重启... (第{restart_count}次)")
                archiver._alive = True
                archiver.start_daemon()

    except KeyboardInterrupt:
        archiver.stop()
        print("归档器已停止")


if __name__ == "__main__":
    main()
