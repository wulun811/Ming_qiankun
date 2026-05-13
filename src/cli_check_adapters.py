# cli_check_adapters.py —— 0.11.9m 适配器状态检查命令
# 检查各适配器是否正常工作，版本兼容性
import sys, sqlite3, json
from pathlib import Path
from i18n import _


def _check_hermes():
    plugins_dir = Path.home() / ".hermes" / "plugins" / "mingjing-probe"
    if not plugins_dir.exists():
        return {
            "name": "Hermes",
            "status": "not_installed",
            "detail": _("插件目录不存在"),
        }
    enabled_file = Path.home() / ".hermes" / "plugins_enabled.txt"
    if enabled_file.exists():
        try:
            enabled = enabled_file.read_text().splitlines()
            if "mingjing-probe" not in enabled:
                return {
                    "name": "Hermes",
                    "status": "warn",
                    "detail": _(
                        "插件已安装但未启用（hermes plugins enable mingjing-probe）"
                    ),
                }
        except OSError:
            pass
    return {
        "name": "Hermes",
        "status": "ok",
        "detail": _("插件已安装 (%s)") % (plugins_dir,),
    }


def _check_opencode():
    db_path = Path.home() / ".local/share/opencode/opencode.db"
    if not db_path.exists():
        return {
            "name": "OpenCode",
            "status": "not_installed",
            "detail": _("opencode.db 不存在"),
        }
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1)
        conn.execute("PRAGMA query_only = ON")
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('message', 'part')"
        )
        found = {row[0] for row in cursor.fetchall()}
        missing = {"message", "part"} - found
        if missing:
            conn.close()
            return {
                "name": "OpenCode",
                "status": "incompatible",
                "detail": _("DB schema 不兼容: 缺少表 %s（测试至 v1.3.13）")
                % (missing,),
            }
        for tbl in ("message", "part"):
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({tbl})")}
            needed = {"id", "session_id", "data", "time_created"}
            missing_cols = needed - cols
            if missing_cols:
                conn.close()
                return {
                    "name": "OpenCode",
                    "status": "incompatible",
                    "detail": _("表 '%s' 缺少字段 %s") % (tbl, missing_cols),
                }
        conn.close()
        return {
            "name": "OpenCode",
            "status": "ok",
            "detail": _("DB schema 兼容 (%s)") % (db_path,),
        }
    except sqlite3.Error as e:
        return {
            "name": "OpenCode",
            "status": "error",
            "detail": _("DB 连接失败: %s") % (e,),
        }


def _check_langchain():
    try:
        import importlib.metadata as md

        ver = md.version("langchain-core")
        return {
            "name": "LangChain",
            "status": "ok",
            "detail": _("langchain-core %s（测试版本 >=1.0）") % (ver,),
        }
    except md.PackageNotFoundError:
        return {
            "name": "LangChain",
            "status": "not_installed",
            "detail": _("langchain-core 未安装"),
        }
    except Exception as e:
        return {
            "name": "LangChain",
            "status": "error",
            "detail": _("检查失败: %s") % (e,),
        }


def _check_openclaw():
    ext_dir = Path.home() / ".openclaw" / "extensions" / "mingjing-probe"
    if not ext_dir.exists():
        return {
            "name": "OpenClaw",
            "status": "not_installed",
            "detail": _("探针扩展目录不存在"),
        }
    index_js = ext_dir / "index.js"
    if not index_js.exists():
        return {
            "name": "OpenClaw",
            "status": "warn",
            "detail": _("扩展目录存在但缺少 index.js"),
        }
    installs_json = Path.home() / ".openclaw" / "plugins" / "installs.json"
    if installs_json.exists():
        try:
            reg = json.loads(installs_json.read_text())
            if "mingjing-probe" not in str(reg):
                return {
                    "name": "OpenClaw",
                    "status": "warn",
                    "detail": _(
                        "探针已安装但未注册到 OpenClaw（需执行 openclaw plugins install --link ...）"
                    ),
                }
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "name": "OpenClaw",
        "status": "ok",
        "detail": _("探针已安装 (%s)") % (ext_dir,),
    }


def cmd_check_adapters(args):
    checks = [_check_hermes(), _check_opencode(), _check_langchain(), _check_openclaw()]
    if getattr(args, "json", False):
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return
    print(f"{'Adapter':<15} {'Status':<16} Detail")
    print("-" * 70)
    for c in checks:
        status_icon = {
            "ok": "+",
            "warn": "!",
            "error": "x",
            "not_installed": "-",
            "incompatible": "x",
        }
        icon = status_icon.get(c["status"], "?")
        print(f"{c['name']:<15} {icon} {c['status']:<14} {c['detail']}")
    has_issues = any(c["status"] not in ("ok", "not_installed") for c in checks)
    sys.exit(1 if has_issues else 0)
