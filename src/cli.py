#!/usr/bin/env python3
# cli.py —— v0.11.14.post1 乾坤镜命令行入口（路由核心）
# 职责：命令分发给各子模块
# 拆分：cli_report / cli_triage / cli_health / cli_admin / cli_archive / cli_ops
import argparse, sqlite3, json, time, sys
from i18n import _
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

DB = Path.home() / ".ming" / "ming.db"

from archiver_util import diagnoses_query

from cli_report import cmd_report
from cli_triage import cmd_triage
from cli_health import cmd_health, cmd_self_check, cmd_status
from cli_admin import cmd_admin, cmd_exclude, cmd_probe, cmd_known_probes
from cli_archive import cmd_archive, cmd_verify
from cli_ops import cmd_skill, cmd_config, cmd_web, cmd_hermes_install, cmd_service
from cli_check_adapters import cmd_check_adapters

try:
    from cli_replay import cmd_replay
except ModuleNotFoundError:
    cmd_replay = None
try:
    from cli_disease import (
        cmd_ignore,
        cmd_archive_disease,
        cmd_restore,
        cmd_reset,
        cmd_reset_status,
        cmd_instance_list,
    )
except ModuleNotFoundError:
    cmd_ignore = cmd_archive_disease = cmd_restore = cmd_reset = cmd_reset_status = (
        cmd_instance_list
    ) = None

_PREDEFINED_QUERIES = {
    "recent_p0": (
        "diagnosis_id, system, fault_id, diagnosis_name, "
        "confidence, severity, created_at",
        "severity = 'P0'",
        "ORDER BY created_at DESC LIMIT ?",
    ),
    "recent_p1": (
        "diagnosis_id, system, fault_id, diagnosis_name, "
        "confidence, severity, created_at",
        "severity = 'P1'",
        "ORDER BY created_at DESC LIMIT ?",
    ),
    "unconfirmed": (
        "diagnosis_id, system, fault_id, diagnosis_name, "
        "confidence, severity, status, created_at",
        "status = 'pending'",
        "ORDER BY created_at DESC LIMIT ?",
    ),
}

_PREDEFINED_NON_DIAG = {
    "event_stats": (
        "SELECT event_type, COUNT(*) as cnt FROM events "
        "GROUP BY event_type ORDER BY cnt DESC LIMIT ?"
    ),
    "system_stats": (
        "SELECT system, COUNT(*) as cnt FROM events "
        "GROUP BY system ORDER BY cnt DESC LIMIT ?"
    ),
}


def cmd_dx(args):
    if not DB.exists():
        print(_("数据库不存在: %s") % DB)
        return
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        query_name = args.query_name
        limit = args.limit if args.limit is not None else 20

        if query_name in _PREDEFINED_QUERIES:
            columns, where, order_limit = _PREDEFINED_QUERIES[query_name]
            try:
                rows = diagnoses_query(conn, columns, where, (limit,), order_limit)
                if not rows:
                    severity = query_name.replace("recent_", "").upper()
                    if severity in ("P0", "P1"):
                        print(_("近 %d 条无 %s 诊断记录") % (limit, severity))
                    elif query_name == "unconfirmed":
                        print(_("无待确认的诊断"))
                    return
                for r in rows:
                    d = dict(r) if hasattr(r, "keys") else dict(zip(r.keys(), r))
                    if "created_at" in d:
                        d["created_at"] = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(d["created_at"])
                        )
                    print(json.dumps(d, ensure_ascii=False, default=str))
            except Exception as e:
                print(_("查询出错: %s") % e)
        elif query_name in _PREDEFINED_NON_DIAG:
            try:
                rows = conn.execute(
                    _PREDEFINED_NON_DIAG[query_name], (limit,)
                ).fetchall()
                if not rows:
                    print(_("无事件数据"))
                    return
                for r in rows:
                    d = dict(r)
                    print(json.dumps(d, ensure_ascii=False, default=str))
            except Exception as e:
                print(_("查询出错: %s") % e)
        else:
            print(_("未知查询: %s") % query_name)
            available = list(_PREDEFINED_QUERIES.keys()) + list(
                _PREDEFINED_NON_DIAG.keys()
            )
            print(_("可用查询: %s") % ", ".join(available))
    finally:
        conn.close()


def cmd_query(args):
    """运行预定义查询（v0.11.9m 扩展支持 QueryBridge）"""
    name = args.query_name
    limit = args.limit if args.limit is not None else 20

    _BRIDGE_QUERIES = {
        "token-breakdown",
        "token-spike",
        "tool-audit",
        "step-sequence",
        "step-loop",
        "memory-retrieve",
        "agent-status",
        "event-timeline",
    }

    if name in _BRIDGE_QUERIES:
        _cmd_bridge_query(args)
        return
    args.query_name = name
    args.limit = limit
    cmd_dx(args)


def _cmd_bridge_query(args):
    """通过 QueryBridge 执行查询（v0.11.9m 新增）"""
    from query_bridge import QueryBridge

    bridge = QueryBridge()
    try:
        name = args.query_name

        if name == "token-breakdown":
            system = getattr(args, "system", None)
            since = getattr(args, "since", None)
            group_by = getattr(args, "group_by", "model") or "model"
            result = bridge.query_token_breakdown(system, since, group_by)
        elif name == "token-spike":
            system = getattr(args, "system", None)
            window = getattr(args, "window", 300) or 300
            threshold = getattr(args, "threshold", 3.0) or 3.0
            result = bridge.query_token_spike(system, window, threshold)
        elif name == "tool-audit":
            system = getattr(args, "system", None)
            since = getattr(args, "since", None)
            result = bridge.query_tool_dangerous(system, since)
        elif name == "step-sequence":
            system = getattr(args, "system", None)
            session_id = getattr(args, "session_id", None)
            since = getattr(args, "since", None)
            limit = args.limit if args.limit is not None else 100
            result = bridge.query_step_sequence(system, session_id, since, limit)
        elif name == "step-loop":
            system = getattr(args, "system", None)
            window = getattr(args, "window", 600) or 600
            result = bridge.query_step_loop(system, window)
        elif name == "memory-retrieve":
            system = getattr(args, "system", None)
            since = getattr(args, "since", None)
            limit = args.limit if args.limit is not None else 50
            result = bridge.query_memory_retrieve(system, since, limit)
        elif name == "agent-status":
            result = bridge.query_agent_status()
        elif name == "event-timeline":
            system = getattr(args, "system", None)
            since = getattr(args, "since", None)
            limit = args.limit if args.limit is not None else 200
            result = bridge.query_event_timeline(system, since, min(limit, 1000))
        else:
            print(_("未知查询: %s") % name)
            return

        if not result:
            print(_("无结果"))
            return
        for r in result:
            if "timestamp" in r:
                r["timestamp"] = (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["timestamp"]))
                    if isinstance(r["timestamp"], (int, float))
                    else r["timestamp"]
                )
            if "last_seen" in r and isinstance(r["last_seen"], (int, float)):
                r["last_seen"] = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(r["last_seen"])
                )
            print(json.dumps(r, ensure_ascii=False, default=str))
    finally:
        bridge.close()


def main():
    from i18n import init_i18n

    init_i18n()
    parser = argparse.ArgumentParser(
        prog="ming", description=_("乾坤镜 CLI v0.11.14.post1 — OpenClaw 监控与诊断工具")
    )
    parser.add_argument("--version", action="version", version="ming 0.11.14.post1")
    sub = parser.add_subparsers(dest="command")

    p_skill = sub.add_parser("skill", help=_("管理技能插件"))
    p_skill.add_argument("action", choices=["install", "list", "logs"])
    p_skill.add_argument("--name")
    p_skill.add_argument("--path")
    p_skill.add_argument("--tail", type=int, default=50)

    p_dx = sub.add_parser("dx", help=_("诊断管理（列表/详情/确认/导出）"))
    p_dx.add_argument(
        "query_name",
        nargs="?",
        help=_("查询名称（recent_p0/recent_p1/event_stats/system_stats/unconfirmed）"),
    )
    p_dx.add_argument(
        "--limit", type=int, default=20, help=_("返回条数上限（默认 20）")
    )
    p_dx.add_argument("--json", action="store_true", help=_("JSON 输出"))

    p_health = sub.add_parser("health", help=_("查看系统健康状态"))
    p_health.add_argument("--json", action="store_true", help=_("JSON 输出"))

    p_query = sub.add_parser("query", help=_("运行预定义查询（v0.11.9m 扩展）"))
    p_query.add_argument("query_name", help=_("查询名称（可用查询见 --help）"))
    p_query.add_argument("--limit", type=int, default=20, help=_("返回条数上限"))
    p_query.add_argument("--json", action="store_true", help=_("JSON 输出"))
    p_query.add_argument("-s", "--system", help=_("按系统名称过滤"))
    p_query.add_argument("--since", type=float, help=_("起始时间戳（Unix epoch）"))
    p_query.add_argument(
        "--group-by",
        choices=["model", "hour", "agent"],
        default="model",
        help=_("分组维度（token-breakdown 用）"),
    )
    p_query.add_argument("--session-id", help=_("Session ID（step-sequence 用）"))
    p_query.add_argument(
        "--window",
        type=int,
        default=600,
        help=_("时间窗口秒数（step-loop / token-spike 用）"),
    )
    p_query.add_argument(
        "--threshold",
        type=float,
        default=3.0,
        help=_("突增倍数阈值（token-spike 用，默认 3.0）"),
    )

    p_status = sub.add_parser("status", help=_("查看归档器运行状态"))
    p_status.add_argument("--json", action="store_true", help=_("JSON 输出"))

    p_self_check = sub.add_parser(
        "self-check", help=_("运行系统自检（退出码: 0=正常 1=警告 2=严重）")
    )
    p_self_check.add_argument("--json", action="store_true", help=_("JSON 输出"))

    p_config = sub.add_parser("config", help=_("查看/验证配置"))
    p_config.add_argument("action", choices=["show", "validate"])
    p_config.add_argument("--json", action="store_true", help=_("JSON 输出"))

    p_admin = sub.add_parser(
        "admin", help=_("管理命令（删除 session / 压缩数据库 / 清理测试数据）")
    )
    p_admin.add_argument("action", choices=["forget", "vacuum", "cleanup"])
    p_admin.add_argument(
        "--session-id", help=_("要删除的 Session ID（forget 操作必需）")
    )
    p_admin.add_argument(
        "--confirm", action="store_true", help=_("确认删除（forget 操作必需）")
    )

    p_archive = sub.add_parser("archive", help=_("管理冷轨归档文件"))
    p_archive.add_argument("action", choices=["list", "cat", "verify"])
    p_archive.add_argument("--file")

    sub.add_parser("verify", help=_("校验哈希链完整性"))

    p_web = sub.add_parser("web", help=_("Web 面板（导出/启动服务）"))
    p_web.add_argument("action", choices=["export", "serve", "stop"])
    p_web.add_argument("--port", type=int, default=18088)
    p_web.add_argument(
        "--host", type=str, default=None, help=_("绑定地址（默认 127.0.0.1）")
    )
    p_web.add_argument(
        "--token", type=str, default=None, help=_("API 认证令牌（可选）")
    )
    p_web.add_argument("--no-browser", action="store_true")

    p_triage = sub.add_parser("triage", help=_("运行分诊诊断"))
    p_triage.add_argument("action", choices=["run", "status", "report"])
    p_triage.add_argument("--no-diagnose", action="store_true", help=_("跳过自动诊断"))
    p_triage.add_argument("--json", action="store_true", help=_("JSON 输出"))

    p_report = sub.add_parser("report", help=_("生成结构化诊断报告"))
    p_report.add_argument(
        "--days", type=int, default=1, help=_("统计天数（默认 1 天）")
    )
    p_report.add_argument(
        "--month", type=str, default=None, help=_("月度报告：格式 YYYY_MM，如 2026_04")
    )
    p_report.add_argument("--json", action="store_true", help=_("JSON 输出"))

    p_replay = sub.add_parser("replay", help=_("诊断回放时间线（事件→诊断演化）"))
    p_replay.add_argument("--system", "-s", required=True, help=_("系统名称"))
    p_replay.add_argument(
        "--since", default="1h", help=_("起始时间（30m/2h/1d 或 Unix 时间戳）")
    )
    p_replay.add_argument("--until", default=None, help=_("结束时间（同上，默认 now）"))
    p_replay.add_argument(
        "--detail", choices=["normal", "full"], default="normal", help=_("详细程度")
    )
    p_replay.add_argument("--json", action="store_true", help=_("JSON 输出"))

    p_ignore = sub.add_parser(
        "ignore", help=_("忽略某疾病（疾病仍然记录，仅报告不展示）")
    )
    p_ignore.add_argument("fault_id", help=_("疾病规则ID"))
    p_ignore.add_argument("-s", "--system", required=True, help=_("系统名称"))

    p_archive_disease = sub.add_parser(
        "archive-disease", help=_("归档某疾病（永久隐藏，不计入健康）")
    )
    p_archive_disease.add_argument("fault_id", help=_("疾病规则ID"))
    p_archive_disease.add_argument("-s", "--system", required=True, help=_("系统名称"))

    p_restore = sub.add_parser("restore", help=_("取消忽略/取消归档"))
    p_restore.add_argument("fault_id", help=_("疾病规则ID"))
    p_restore.add_argument("-s", "--system", required=True, help=_("系统名称"))

    p_reset = sub.add_parser(
        "reset", help=_("健康复位某实例（强制标记健康，新疾病出现取消）")
    )
    p_reset.add_argument("system", help=_("系统名称"))

    p_reset_status = sub.add_parser("reset-status", help=_("查看健康复位状态"))
    p_reset_status.add_argument("system", nargs="?", help=_("系统名称（不指定则全部）"))

    sub.add_parser("instance-list", help=_("紧凑列出所有实例及其健康状态"))

    sub.add_parser("hermes-install", help=_("一键安装乾坤镜探针到 Hermes Agent"))

    p_exclude = sub.add_parser("exclude", help=_("停止/恢复观察某个实例"))
    p_exclude.add_argument("action", choices=["add", "remove", "list"])
    p_exclude.add_argument("system", nargs="?", help=_("实例名称"))

    p_check = sub.add_parser("check-adapters", help=_("检查所有适配器状态与版本兼容性"))
    p_check.add_argument("--json", action="store_true", help=_("JSON 输出"))

    p_service = sub.add_parser("service", help=_("管理 OpenCode 守护进程系统服务"))
    p_service.add_argument("action", choices=["install", "remove", "status"])

    p_probe = sub.add_parser("probe", help=_("探针管理（列出 / 安全卸载）"))
    p_probe.add_argument("action", choices=["list", "uninstall"])
    p_probe.add_argument("system", nargs="?", help=_("实例名称（uninstall 必需）"))
    p_probe.add_argument("--force", action="store_true", help=_("跳过确认"))
    p_probe.add_argument("--keep-data", action="store_true", help=_("保留数据库记录"))
    p_probe.add_argument(
        "--dry-run", action="store_true", help=_("预演模式（只显示不执行）")
    )

    p_known = sub.add_parser(
        "known-probes", help=_("管理已知探针列表（三层：内置/用户/自动发现）")
    )
    p_known.add_argument("action", choices=["list", "add", "remove", "discover"])
    p_known.add_argument("--name", help=_("探针名称（add/remove 必需）"))

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "skill": cmd_skill,
        "dx": cmd_dx,
        "health": cmd_health,
        "query": cmd_query,
        "status": cmd_status,
        "self-check": cmd_self_check,
        "config": cmd_config,
        "admin": cmd_admin,
        "archive": cmd_archive,
        "verify": cmd_verify,
        "web": cmd_web,
        "triage": cmd_triage,
        "report": cmd_report,
        "hermes-install": cmd_hermes_install,
    }
    if cmd_replay is not None:
        cmds["replay"] = cmd_replay
    if cmd_ignore is not None:
        cmds["ignore"] = cmd_ignore
        cmds["archive-disease"] = cmd_archive_disease
        cmds["restore"] = cmd_restore
        cmds["reset"] = cmd_reset
        cmds["reset-status"] = cmd_reset_status
        cmds["instance-list"] = cmd_instance_list
    cmds["check-adapters"] = cmd_check_adapters
    cmds["service"] = cmd_service
    cmds["exclude"] = cmd_exclude
    cmds["probe"] = cmd_probe
    cmds["known-probes"] = cmd_known_probes
    cmds[args.command](args)


if __name__ == "__main__":
    main()
