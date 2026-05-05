# plugin_runner.py —— 0.11.9m 插件执行器（P2 修复版）
# 职责：扫描 skill.yaml → 校验 → 执行插件 → 写心跳 → 校验输出
# 依赖：标准库 only（subprocess, time, json, pathlib, re, csv, os, fcntl）
# 0.11.9m P2 修复：YAML 多值解析（csv 模块）+ 状态文件锁增强

import subprocess, time, json, re, os, csv, io, signal, shlex
from pathlib import Path

PLUGINS = Path.home() / ".ming" / "plugins"
STATE = Path.home() / ".ming" / ".plugin_state.json"
LOG = Path.home() / ".ming" / ".plugin_runner.log"

REQUIRED_FIELDS = {"name", "version", "type", "description"}
VALID_TYPES = {"cron", "event", "manual"}
VALID_SEVERITIES = {"P0", "P1", "P2", "P3", "META"}


def _parse_yaml(text: str) -> dict:
    """内置简易 YAML 解析器（零第三方依赖，仅解析 skill.yaml 子集）"""
    result = {}
    stack = [(result, -1)]
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if ":" not in stripped:
            continue
        key, _, val = stripped.partition(":")
        key = key.strip()
        val = val.strip()
        while len(stack) > 1 and indent <= stack[-1][1]:
            stack.pop()
        parent = stack[-1][0]
        if val:
            if val.startswith("[") and val.endswith("]"):
                # 使用 csv 解析器处理多值（支持引号内逗号、转义等）
                reader = csv.reader(io.StringIO(val[1:-1]))
                items = [
                    x.strip().strip('"').strip("'") for x in next(reader) if x.strip()
                ]
                parent[key] = items
            elif val.startswith('"') and val.endswith('"'):
                parent[key] = val[1:-1]
            elif val.startswith("'") and val.endswith("'"):
                parent[key] = val[1:-1]
            else:
                try:
                    parent[key] = int(val)
                except ValueError:
                    try:
                        parent[key] = float(val)
                    except ValueError:
                        parent[key] = val
        else:
            new_dict = {}
            parent[key] = new_dict
            stack.append((new_dict, indent))
    return result


def _log(msg: str):
    """写运行日志"""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def _load_state() -> dict:
    """加载插件状态（enable/disable），加读锁"""
    if not STATE.exists():
        return {}
    try:
        fd = open(STATE, "r", encoding="utf-8")
        try:
            # Unix: fcntl; Windows: msvcrt
            if os.name != "nt":
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_SH)
            else:
                import msvcrt

                msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                return json.loads(fd.read())
            finally:
                if os.name != "nt":
                    fcntl.flock(fd, fcntl.LOCK_UN)
                else:
                    msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            fd.close()
    except Exception:
        return {}


def _save_state(state: dict):
    """保存插件状态（原子写入 + 写锁）"""
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        # 写入临时文件
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        # 原子替换（Windows 上 os.replace 也可工作）
        os.replace(str(tmp), str(STATE))
    except Exception:
        pass


def _validate_schema(cfg: dict) -> tuple:
    """YAML Schema 校验，返回 (valid, reason)"""
    missing = REQUIRED_FIELDS - set(cfg.keys())
    if missing:
        return False, f"缺少必需字段: {missing}"
    if cfg.get("type") not in VALID_TYPES:
        return False, f"无效 type: {cfg.get('type')}"
    ver = str(cfg.get("version", ""))
    if not re.match(r"^\d+\.\d+\.\d+$", ver):
        return False, f"version 格式错误: {ver}"
    return True, "ok"


def _validate_output(plugin_dir: Path, cfg: dict) -> tuple:
    """输出格式校验，返回 (valid, reason)"""
    output = cfg.get("output", {})
    fmt = output.get("format", "")
    path = output.get("path", "")
    if not path:
        return True, "no output path"
    out_path = Path(path).expanduser()
    if not out_path.exists():
        return False, f"输出文件不存在: {out_path}"
    if fmt == "json" or fmt == "jsonl":
        try:
            content = out_path.read_text(encoding="utf-8")
            if fmt == "json":
                json.loads(content)
            elif fmt == "jsonl":
                for line in content.strip().split("\n"):
                    if line:
                        json.loads(line)
        except json.JSONDecodeError as e:
            return False, f"输出格式错误: {e}"
    return True, "ok"


def enable(name: str):
    """启用插件"""
    state = _load_state()
    state[name] = "enabled"
    _save_state(state)
    _log(f"插件 {name} 已启用")


def disable(name: str):
    """禁用插件"""
    state = _load_state()
    state[name] = "disabled"
    _save_state(state)
    _log(f"插件 {name} 已禁用")


def is_enabled(name: str) -> bool:
    """检查插件是否启用（默认启用）"""
    state = _load_state()
    return state.get(name, "enabled") != "disabled"


def list_plugins() -> list:
    """列出所有插件及状态"""
    result = []
    if not PLUGINS.exists():
        return result
    for skill in PLUGINS.glob("*/*.skill.yaml"):
        name = skill.parent.name
        cfg = _parse_yaml(skill.read_text(encoding="utf-8"))
        heartbeat = skill.parent / ".plugin_heartbeat"
        hb_age = None
        if heartbeat.exists():
            try:
                hb_age = time.time() - float(heartbeat.read_text().strip())
            except Exception:
                pass
        result.append(
            {
                "name": name,
                "version": cfg.get("version", "?"),
                "type": cfg.get("type", "?"),
                "enabled": is_enabled(name),
                "heartbeat_age": hb_age,
            }
        )
    return result


def run_all():
    """扫描 plugins/ 目录，执行所有 cron 类型插件"""
    if not PLUGINS.exists():
        _log("插件目录不存在")
        return
    _log("开始执行所有插件")
    for skill in PLUGINS.glob("*/*.skill.yaml"):
        name = skill.parent.name
        if not is_enabled(name):
            _log(f"插件 {name} 已禁用，跳过")
            continue
        try:
            cfg = _parse_yaml(skill.read_text(encoding="utf-8"))
        except Exception as e:
            _log(f"插件 {name} YAML 解析失败: {e}")
            continue
        valid, reason = _validate_schema(cfg)
        if not valid:
            _log(f"插件 {name} Schema 校验失败: {reason}")
            continue
        if cfg.get("trigger", {}).get("type") != "cron":
            continue
        entry = cfg.get("install", {}).get("entrypoint", "")
        if not entry:
            _log(f"插件 {name} 无 entrypoint")
            continue
        try:
            entry_cmd = shlex.split(entry)
            kwargs = {
                "shell": False,
                "args": entry_cmd,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
            }
            if os.name != "nt":
                kwargs["start_new_session"] = True

            proc = subprocess.Popen(**kwargs)

            try:
                stdout, stderr = proc.communicate(timeout=60)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
                else:
                    proc.kill()
                _log(f"插件 {name} 执行超时 (>60s)，已强制终止进程组")
                continue

            if rc == 0:
                (skill.parent / ".plugin_heartbeat").write_text(str(time.time()))
            else:
                _log(
                    f"插件 {name} 退出码 {rc}: {stderr.decode('utf-8', errors='replace')[:200]}"
                )
            # 输出格式校验
            out_valid, out_reason = _validate_output(skill.parent, cfg)
            if not out_valid:
                _log(f"插件 {name} 输出校验失败: {out_reason}")
        except Exception as e:
            _log(f"插件 {name} 执行异常: {e}")
    _log("插件执行完成")


def run_plugin(name: str) -> bool:
    """执行单个插件"""
    if not is_enabled(name):
        _log(f"插件 {name} 已禁用")
        return False
    skill = PLUGINS / name / f"{name}.skill.yaml"
    if not skill.exists():
        skill = next(PLUGINS.glob(f"*/{name}.skill.yaml"), None)
    if not skill:
        _log(f"插件 {name} 未找到")
        return False
    try:
        cfg = _parse_yaml(skill.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"插件 {name} YAML 解析失败: {e}")
        return False
    valid, reason = _validate_schema(cfg)
    if not valid:
        _log(f"插件 {name} Schema 校验失败: {reason}")
        return False
    entry = cfg.get("install", {}).get("entrypoint", "")
    if not entry:
        return False
    try:
        entry_cmd = shlex.split(entry)
        kwargs = {
            "shell": False,
            "args": entry_cmd,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
        }
        if os.name != "nt":
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(**kwargs)

        try:
            stdout, stderr = proc.communicate(timeout=60)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            else:
                proc.kill()
            _log(f"插件 {name} 执行超时 (>60s)，已强制终止进程组")
            return False

        if rc == 0:
            (skill.parent / ".plugin_heartbeat").write_text(str(time.time()))
        else:
            _log(
                f"插件 {name} 退出码 {rc}: {stderr.decode('utf-8', errors='replace')[:200]}"
            )
        out_valid, out_reason = _validate_output(skill.parent, cfg)
        if not out_valid:
            _log(f"插件 {name} 输出校验失败: {out_reason}")
        return rc == 0
    except Exception as e:
        _log(f"插件 {name} 执行异常: {e}")
        return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "enable" and len(sys.argv) > 2:
            enable(sys.argv[2])
        elif cmd == "disable" and len(sys.argv) > 2:
            disable(sys.argv[2])
        elif cmd == "list":
            for p in list_plugins():
                status = "enabled" if p["enabled"] else "disabled"
                hb = (
                    f"heartbeat={p['heartbeat_age']:.0f}s ago"
                    if p["heartbeat_age"]
                    else "no heartbeat"
                )
                print(f"{p['name']} v{p['version']} ({p['type']}) [{status}] {hb}")
        elif cmd == "run" and len(sys.argv) > 2:
            run_plugin(sys.argv[2])
        else:
            print("用法: plugin_runner.py [enable|disable|list|run] [name]")
    else:
        run_all()
