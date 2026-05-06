# cli_ops.py —— 0.11.9m 运维/技能/配置/前端命令
# 职责：cmd_skill / cmd_config / cmd_web
import json, shutil, subprocess, sys
from pathlib import Path


def cmd_skill(args):
    registry = Path.home() / ".ming" / "plugins"
    if args.action == "install":
        if not args.name or ".." in args.name or "/" in args.name:
            print("错误: 无效的技能名称")
            sys.exit(1)
        dest = registry / args.name
        dest.mkdir(parents=True, exist_ok=True)
        if hasattr(args, "path") and args.path:
            src = Path(args.path).resolve()
            if not str(src).startswith(str(Path.home())):
                print("错误: 源文件必须在用户目录下")
                sys.exit(1)
            shutil.copy(str(src), dest / f"{args.name}.skill.yaml")
        print("已安装技能: {}".format(args.name))
    elif args.action == "list":
        for p in registry.glob("*/"):
            hb = p / ".plugin_heartbeat"
            status = "active" if hb.exists() else "inactive"
            print(f"  {p.name} [{status}]")
    elif args.action == "logs":
        log_dir = registry / args.name / "out"
        if not log_dir.exists():
            print("无日志记录")
            return
        files = sorted(log_dir.glob("*.jsonl"))
        tail = args.tail if hasattr(args, "tail") else 50
        for f in files[-3:]:
            lines = f.read_text(encoding="utf-8").strip().splitlines()
            for line in lines[-tail:]:
                print(line)


def cmd_config(args):
    """配置管理：show / validate"""
    sys.path.insert(0, str(Path(__file__).parent))
    from config_loader import load_config, format_config_show, validate

    if args.action == "show":
        config, source, _ = load_config()
        if getattr(args, "json", False):
            out = {"config": config, "source": source}
            print(json.dumps(out, indent=2, ensure_ascii=False))
        else:
            print(format_config_show(config, source))
    elif args.action == "validate":
        config, _, errors = load_config()
        if errors:
            print("校验失败：")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        else:
            print("校验通过")


def cmd_web(args):
    """前端插件命令"""
    server_py = Path(__file__).parent / "plugins" / "web_dashboard" / "server.py"
    exporter_py = (
        Path(__file__).parent / "plugins" / "web_dashboard" / "web_exporter.py"
    )
    if args.action == "export":
        if exporter_py.exists():
            subprocess.run([sys.executable, str(exporter_py)])
            print("data.json exported")
        else:
            print("web_exporter.py not found")
    elif args.action == "serve":
        if server_py.exists():
            cmd = [
                sys.executable,
                str(server_py),
                "--port",
                str(getattr(args, "port", 18088)),
            ]
            if getattr(args, "host", None):
                cmd.extend(["--host", args.host])
            if getattr(args, "token", None):
                cmd.extend(["--token", args.token])
            if getattr(args, "no_browser", False):
                cmd.append("--no-browser")
            subprocess.run(cmd)
        else:
            print("server.py not found")


def cmd_service(args):
    service_name = "ming-opencode"
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    unit_path = unit_dir / f"{service_name}.service"
    daemon_path = Path(__file__).parent / "adapters" / "daemon_opencode.py"

    unit_content = f"""[Unit]
Description=Mingjing OpenCode Daemon Probe
Documentation=https://github.com/cjg007/mingjing
After=network.target

[Service]
Type=simple
WorkingDirectory={daemon_path.parent.parent}
ExecStart={sys.executable} -m adapters.daemon_opencode
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""

    if args.action == "install":
        if not daemon_path.exists():
            print(f"错误: 找不到守护进程脚本 {daemon_path}")
            sys.exit(1)
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(unit_content, encoding="utf-8")
        print(f"  单元文件: {unit_path}")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", service_name], check=True
        )
        print(f"  服务 {service_name} 已安装并启动")

    elif args.action == "remove":
        subprocess.run(
            ["systemctl", "--user", "stop", service_name],
            capture_output=True,
        )
        subprocess.run(
            ["systemctl", "--user", "disable", service_name],
            capture_output=True,
        )
        if unit_path.exists():
            unit_path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        print(f"  服务 {service_name} 已移除")

    elif args.action == "status":
        result = subprocess.run(
            ["systemctl", "--user", "status", service_name],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 or result.returncode == 3:
            print(result.stdout)
            if result.stderr:
                print(result.stderr)
        else:
            print(result.stdout)
            if result.stderr:
                print(result.stderr)
        if not unit_path.exists():
            print(f"\n  ⚠ 单元文件不存在 ({unit_path})")
        elif result.returncode != 0:
            print(f"\n  提示：运行 'ming service install' 安装服务")


def cmd_hermes_install(args):
    """一键安装乾坤镜插件到 Hermes Agent"""
    src_dir = Path(__file__).parent.parent / "extensions" / "hermes"
    if not (src_dir / "plugin.yaml").exists():
        print("错误: 找不到 hermes 插件，请确保乾坤镜安装完整")
        sys.exit(1)

    plugin_dest = Path.home() / ".hermes" / "plugins" / "mingjing-probe"
    skill_src = src_dir / "skills" / "mingjing" / "SKILL.md"
    skill_dest = Path.home() / ".hermes" / "skills" / "mingjing"

    print("乾坤镜 Hermes 插件一键安装")
    print(f"  源: {src_dir}")
    print(f"  插件目标: {plugin_dest}")
    print(f"  技能目标: {skill_dest}")
    print()

    # 1. 复制插件文件
    plugin_dest.mkdir(parents=True, exist_ok=True)
    for f in src_dir.glob("*.py"):
        content = f.read_text(encoding="utf-8")
        (plugin_dest / f.name).write_text(content, encoding="utf-8")
    for f in ["plugin.yaml", "after-install.md"]:
        fp = src_dir / f
        if fp.exists():
            shutil.copy(str(fp), str(plugin_dest / f))
    # 清理 pycache
    pycache = plugin_dest / "__pycache__"
    if pycache.exists():
        shutil.rmtree(pycache)
    print("  ✓ 插件文件已部署")

    # 2. 复制技能文件到 ~/.hermes/skills/
    skill_dest.mkdir(parents=True, exist_ok=True)
    if skill_src.exists():
        shutil.copy(str(skill_src), str(skill_dest / "SKILL.md"))
        print("  ✓ 技能文件已部署")

    # 3. 启用插件
    try:
        result = subprocess.run(
            ["hermes", "plugins", "enable", "mingjing-probe"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            print("  ✓ 插件已启用")
        else:
            print(f"  ⚠ 插件启用失败: {result.stderr.strip()}")
    except FileNotFoundError:
        print(
            "  ⚠ 未找到 hermes 命令，请手动运行: hermes plugins enable mingjing-probe"
        )
    except subprocess.TimeoutExpired:
        print("  ⚠ hermes 命令超时，请手动运行: hermes plugins enable mingjing-probe")

    print()
    print("安装完成！重启 Hermes 后乾坤镜探针自动生效。")
    print("环境变量（可选）:")
    print("  MING_HOME=~/.ming")
    print("  MING_SYSTEM_NAME=hermes-agent")
    print("  MING_MODE=white")
