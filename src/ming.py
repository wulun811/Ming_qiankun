#!/usr/bin/env python3
# ming.py —— 0.11.9m 双模式统一入口
# 用途：通过环境变量 MING_MODE 切换 Standalone/Cluster 模式
# 用法:
#   MING_MODE=standalone python ming.py start
#   MING_MODE=cluster python ming.py start
#   python ming.py status
import os
import sys
import time
import json
import signal
import subprocess
from pathlib import Path

MODE = os.getenv("MING_MODE", "standalone").lower()
PID_FILE = Path.home() / ".ming" / f"{MODE}.pid"
LOG_FILE = Path.home() / ".ming" / f"{MODE}.log"

_archiver = None
_pool = None


def _daemonize():
    """P0-7: 双 fork 守护进程化，父进程立即退出"""
    # 第一次 fork
    pid = os.fork()
    if pid > 0:
        # 父进程打印 PID 后退出
        print(f"[{MODE}] 守护进程已启动 (PID={pid})")
        print(f"[{MODE}] 停止: python3 src/ming.py stop")
        print(f"[{MODE}] 状态: python3 src/ming.py status")
        sys.exit(0)

    # 子进程成为 session leader
    os.setsid()

    # 第二次 fork 防止获取终端
    pid = os.fork()
    if pid > 0:
        sys.exit(0)

    # 重定向标准流
    sys.stdout.flush()
    sys.stderr.flush()
    devnull = open(os.devnull, "r")
    log = open(str(LOG_FILE), "a")
    os.dup2(devnull.fileno(), sys.stdin.fileno())
    os.dup2(log.fileno(), sys.stdout.fileno())
    os.dup2(log.fileno(), sys.stderr.fileno())
    devnull.close()
    log.close()


def _log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [{MODE}] {msg}")
    try:
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _graceful_shutdown(signum=None, frame=None):
    global _archiver, _pool
    _log("收到停止信号，正在关闭...")
    if _archiver:
        _archiver.stop()
    if _pool:
        _pool.close_all()
    PID_FILE.unlink(missing_ok=True)
    _log("已停止")
    sys.exit(0)


def _kill_orphans():
    """扫描 /proc 并清理所有残留的 ming.py 归档器进程（零依赖）"""
    current_pid = os.getpid()
    killed = 0
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            pid = int(entry)
            if pid == current_pid:
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = (
                    f.read().decode("utf-8", errors="replace").replace("\x00", " ")
                )
        except (OSError, ProcessLookupError):
            continue
        if "ming.py" in cmdline and "start" in cmdline:
            try:
                os.kill(pid, signal.SIGTERM)
                killed += 1
            except (ProcessLookupError, PermissionError):
                pass
    return killed


def _try_acquire_pid_lock() -> bool:
    """原子方式检查并创建 PID 文件，防止多实例同时启动"""
    for _ in range(3):
        try:
            PID_FILE.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(PID_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            try:
                os.write(fd, str(os.getpid()).encode())
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            existing = PID_FILE.read_text().strip()
            try:
                pid = int(existing)
                os.kill(pid, 0)
                _log(f"实例已在运行 (PID={pid})")
                return False
            except (ValueError, ProcessLookupError, OSError):
                PID_FILE.unlink(missing_ok=True)
        except OSError as e:
            _log(f"PID 文件创建失败: {e}")
            return False
    _log("PID 锁竞争失败（多次重试后仍无法获取）")
    return False


def start_standalone():
    """Standalone 模式：启动 SQLite 归档器"""
    global _archiver
    from archiver import Archiver

    _log("启动 Standalone 模式...")

    # P0-22: PID 锁必须在初始化之前获取，防止竞态
    if not _try_acquire_pid_lock():
        sys.exit(1)

    # 加载配置（config_loader）
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from config_loader import load_config

        config, source, errors = load_config()
        if errors:
            _log(f"配置警告: {', '.join(errors)}")
    except Exception as e:
        _log(f"配置加载失败: {e}，使用默认值")

    # 自动迁移（migrate）
    try:
        from migrations.migrate import run_migrations

        db_path = os.getenv("MING_DB_PATH", str(Path.home() / ".ming" / "ming.db"))
        skip = os.getenv("MING_MIGRATE_AUTO", "true").lower() != "true"
        success, msg = run_migrations(db_path, skip=skip)
        if not success:
            _log(f"迁移失败: {msg}，尝试继续启动")
    except Exception as e:
        _log(f"迁移异常: {e}，尝试继续启动")

    _archiver = Archiver()
    _archiver.start_daemon()
    _log(f"Archiver 已启动，PID={os.getpid()}")
    # P2-7: 启动操作指引
    _log("后续操作:")
    _log(
        "  发送事件: from probe_uni import ProbeUni; ProbeUni('demo').emit('test', {})"
    )
    _log("  查看诊断: python3 src/cli.py dx list")
    _log("  运行分诊: python3 src/cli.py triage run")
    _log("  Web 面板: python3 src/cli.py web serve")
    _log("  停止服务: python3 src/ming.py stop")

    # P1-8: 主进程监控归档器健康
    restart_count = 0
    max_restarts = 10
    restart_cooldown = 60
    last_restart = 0

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    try:
        while True:
            time.sleep(1)
            if not _archiver._alive:
                now = time.time()
                if now - last_restart < restart_cooldown:
                    restart_count += 1
                    if restart_count >= max_restarts:
                        _log("归档器重启次数过多，退出")
                        break
                else:
                    restart_count = 1
                last_restart = now
                _log(f"归档器已停止，正在重启... (第{restart_count}次)")
                _archiver._alive = True
                _archiver.start_daemon()
    except KeyboardInterrupt:
        _graceful_shutdown()


def start_cluster():
    """Cluster 模式：启动 MySQL 集群归档器"""
    global _archiver, _pool
    try:
        from cluster_archiver import ClusterArchiver
        from cluster_pool import SimplePool
    except ImportError as e:
        _log(f"Cluster 模式依赖缺失: {e}")
        _log("请安装 pymysql: pip install pymysql")
        sys.exit(1)

    host = os.getenv("WQ_DB_HOST", "127.0.0.1")
    port = int(os.getenv("WQ_DB_PORT", "3306"))
    user = os.getenv("WQ_DB_USER", "root")
    from credential_vault import get_secret

    password = get_secret("mysql.password") or None
    database = os.getenv("WQ_DB_NAME", "ming")
    project = os.getenv("WQ_PROJECT_ID", "default")

    _log(f"启动 Cluster 模式: {host}:{port}/{database} (project={project})")

    # 加载配置（config_loader）
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from config_loader import load_config

        config, source, errors = load_config()
        if errors:
            _log(f"配置警告: {', '.join(errors)}")
    except Exception as e:
        _log(f"配置加载失败: {e}，使用默认值")

    if not _try_acquire_pid_lock():
        sys.exit(1)

    try:
        _pool = SimplePool(
            host=host, port=port, user=user, password=password, database=database
        )
    except Exception as e:
        _log(f"数据库连接失败: {e}")
        sys.exit(1)

    _archiver = ClusterArchiver(pool=_pool, project_id=project)
    _archiver.start_daemon()
    _log(f"ClusterArchiver 已启动，PID={os.getpid()}")

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        _graceful_shutdown()


def cmd_start():
    """启动命令"""
    daemon_mode = "--daemon" in sys.argv

    killed = _kill_orphans()
    if killed:
        _log(f"清理了 {killed} 个残留归档器进程")

    time.sleep(0.5)

    if daemon_mode:
        _daemonize()

    if MODE == "cluster":
        start_cluster()
    else:
        start_standalone()


def cmd_stop():
    """停止命令"""
    if not PID_FILE.exists():
        _log(f"未找到 {MODE} PID 文件")
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        _log(f"正在停止 {MODE} (PID={pid})...")
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            _log("警告: 进程未在 15 秒内退出，强制终止")
            try:
                os.kill(pid, signal.SIGKILL)
                time.sleep(1)
            except OSError:
                pass
        PID_FILE.unlink(missing_ok=True)
        _log("已停止")
    except ProcessLookupError:
        _log("进程已不存在")
        PID_FILE.unlink(missing_ok=True)
    except Exception as e:
        _log(f"停止失败: {e}")
        PID_FILE.unlink(missing_ok=True)

    killed = _kill_orphans()
    if killed:
        _log(f"额外清理了 {killed} 个残留进程")


def cmd_restart():
    """P3-3: 重启命令"""
    _log(f"正在重启 {MODE}...")
    cmd_stop()
    time.sleep(1)
    cmd_start()


def cmd_status():
    """状态命令"""
    print(f"模式: {MODE}")
    print(f"PID 文件: {PID_FILE}")
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            running = True
            try:
                os.kill(pid, 0)
            except OSError:
                running = False
            print(f"PID: {pid} ({'运行中' if running else '已停止'})")
        except ValueError:
            print("PID 文件损坏")
    else:
        print("状态: 未启动")

    if MODE == "cluster":
        print(
            f"数据库: {os.getenv('WQ_DB_HOST', '127.0.0.1')}:{os.getenv('WQ_DB_PORT', '3306')}/{os.getenv('WQ_DB_NAME', 'ming')}"
        )
        print(f"项目: {os.getenv('WQ_PROJECT_ID', 'default')}")


def cmd_web_start(args):
    """启动 Web 目镜服务"""
    port = getattr(args, "port", 18088) or 18088
    server_py = Path(__file__).parent / "plugins" / "web_dashboard" / "server.py"
    exporter_py = (
        Path(__file__).parent / "plugins" / "web_dashboard" / "web_exporter.py"
    )

    if not server_py.exists():
        _log("server.py 未找到")
        return

    # 先跑 exporter 生成 data.json（DB 不存在则跳过）
    if exporter_py.exists():
        try:
            import subprocess

            subprocess.run(
                [sys.executable, str(exporter_py)], capture_output=True, timeout=30
            )
        except Exception:
            pass

    # 启动 daemon 模式
    cmd = [sys.executable, str(server_py), "--daemon", "--port", str(port)]
    subprocess.run(cmd)


def cmd_web_stop(args):
    """停止 Web 目镜服务"""
    server_py = Path(__file__).parent / "plugins" / "web_dashboard" / "server.py"
    cmd = [sys.executable, str(server_py), "--stop"]
    import subprocess

    subprocess.run(cmd)


def cmd_web_status(args):
    """查看 Web 目镜服务状态"""
    server_py = Path(__file__).parent / "plugins" / "web_dashboard" / "server.py"
    cmd = [sys.executable, str(server_py), "--status"]
    import subprocess

    subprocess.run(cmd)


def cmd_show_systemd_services():
    """生成 systemd service 文件内容"""
    home = str(Path.home())
    src = str(Path(__file__).parent)
    python = sys.executable

    archiver_service = f"""[Unit]
Description=乾坤镜 Archiver ({MODE})
After=network.target

[Service]
Type=simple
User={os.getenv("USER", "")}
Group={os.getenv("USER", "")}
Environment=MING_MODE={MODE}
WorkingDirectory={src}
ExecStart={python} {src}/ming.py start
ExecStop={python} {src}/ming.py stop
Restart=always
RestartSec=10
StandardOutput=append:{home}/.ming/standalone.log
StandardError=append:{home}/.ming/standalone.log

[Install]
WantedBy=multi-user.target
"""

    web_service = f"""[Unit]
Description=乾坤镜 Web Dashboard
After=ming-archiver.service
Wants=ming-archiver.service

[Service]
Type=simple
User={os.getenv("USER", "")}
Group={os.getenv("USER", "")}
WorkingDirectory={src}/plugins/web_dashboard
ExecStart={python} {src}/plugins/web_dashboard/server.py --daemon --port 18088 --no-browser
ExecStop={python} {src}/plugins/web_dashboard/server.py --stop
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    return archiver_service.strip(), web_service.strip()


def cmd_install_service():
    """注册 systemd 服务"""
    try:
        systemctl = subprocess.run(
            ["which", "systemctl"], capture_output=True, text=True
        ).stdout.strip()
        if not systemctl:
            _log("systemctl 未找到，无法注册服务")
            return
    except Exception:
        _log("无法检测 systemctl")
        return

    archiver_svc, web_svc = cmd_show_systemd_services()

    services_dir = Path("/etc/systemd/system")
    archiver_file = services_dir / "ming-archiver.service"
    web_file = services_dir / "ming-web.service"

    try:
        import subprocess

        subprocess.run(
            ["sudo", "tee", str(archiver_file)],
            input=archiver_svc.encode(),
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _log(f"已写入: {archiver_file}")

        subprocess.run(
            ["sudo", "tee", str(web_file)],
            input=web_svc.encode(),
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _log(f"已写入: {web_file}")

        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _log("已重载 systemd")

        subprocess.run(
            [
                "sudo",
                "systemctl",
                "enable",
                "ming-archiver.service",
                "ming-web.service",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _log("已启用开机自启")

        _log("")
        _log("用法:")
        _log("  sudo systemctl start ming-archiver ming-web    # 启动")
        _log("  sudo systemctl stop ming-archiver ming-web     # 停止")
        _log("  sudo systemctl status ming-archiver ming-web   # 状态")
    except Exception as e:
        _log(f"注册失败: {e}")


def cmd_uninstall_service():
    """卸载 systemd 服务"""
    try:
        import subprocess

        services_dir = Path("/etc/systemd/system")

        subprocess.run(
            [
                "sudo",
                "systemctl",
                "disable",
                "--now",
                "ming-archiver.service",
                "ming-web.service",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        (services_dir / "ming-archiver.service").unlink(missing_ok=True)
        (services_dir / "ming-web.service").unlink(missing_ok=True)

        subprocess.run(
            ["sudo", "systemctl", "daemon-reload"],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        _log("已卸载 systemd 服务")
    except Exception as e:
        _log(f"卸载失败: {e}")


def cmd_main():
    """主入口"""
    if len(sys.argv) < 2:
        print("用法: python3 src/ming.py [start|stop|status|restart|web|service]")
        print("      python3 src/ming.py start [--daemon]  # --daemon 后台运行不阻塞")
        print("      python3 src/ming.py service install    # 注册 systemd 开机自启")
        print(f"当前模式: {MODE} (设置 MING_MODE 环境变量切换)")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd in ("--help", "-h"):
        print("用法: python3 src/ming.py [start|stop|status|restart|web|service]")
        print("      python3 src/ming.py start [--daemon]")
        print("      python3 src/ming.py service install")
        print("      python3 src/ming.py web start [--port PORT]")
        print(f"当前模式: {MODE} (设置 MING_MODE 环境变量切换)")
        sys.exit(0)
    if cmd in ("--version", "-v"):
        print("ming 0.11.9m")
        sys.exit(0)
    if cmd == "start":
        cmd_start()
    elif cmd == "stop":
        cmd_stop()
    elif cmd == "restart":
        cmd_restart()
    elif cmd == "status":
        cmd_status()
    elif cmd == "web":
        import argparse

        web_parser = argparse.ArgumentParser()
        web_parser.add_argument("action", choices=["start", "stop", "status"])
        web_parser.add_argument("--port", type=int, default=18088)
        web_args = web_parser.parse_args(sys.argv[2:])
        {"start": cmd_web_start, "stop": cmd_web_stop, "status": cmd_web_status}[
            web_args.action
        ](web_args)
    elif cmd == "service":
        import argparse

        svc_parser = argparse.ArgumentParser()
        svc_parser.add_argument("action", choices=["install", "uninstall"])
        svc_args = svc_parser.parse_args(sys.argv[2:])
        if svc_args.action == "install":
            cmd_install_service()
        elif svc_args.action == "uninstall":
            cmd_uninstall_service()
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    cmd_main()
