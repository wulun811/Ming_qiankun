# _auto.py —— 乾坤镜 LangChain 自动激活模块
# 通过 .pth 文件注册 PEP 302 import hook，
# 在 langchain_core 首次导入时自动 monkey-patch

import sys


def _check_langchain_version():
    try:
        import importlib.metadata as md
        ver = md.version("langchain-core")
        print(
            f"[ming-probe-langchain] langchain-core {ver} detected. "
            f"Tested: >=1.0. See README for compatibility.",
            file=sys.stderr,
        )
    except md.PackageNotFoundError:
        print(
            "[ming-probe-langchain] langchain-core not found in metadata. "
            "Patch will be attempted on import.",
            file=sys.stderr,
        )
    except Exception:
        pass


class _MingLangChainAutoPatch:
    _patched = False

    def find_spec(self, fullname, path, target=None):
        if not self._patched and (
            fullname == "langchain_core"
            or fullname.startswith("langchain_core.")
            or fullname == "langchain"
        ):
            self._apply_patch()
        return None

    @classmethod
    def _apply_patch(cls):
        if cls._patched:
            return
        cls._patched = True
        _check_langchain_version()
        probe = None
        try:
            from ming_probe_langchain.probe_langchain import init_langchain_probe

            probe = init_langchain_probe(system="langchain", mode="white")
        except Exception:
            pass
        if probe is not None:
            try:
                from ming_probe_langchain.health import get_health

                r = get_health()
                if r.get("overall") == "no_archiver":
                    print(
                        "[ming-probe-langchain] 乾坤镜归档器未运行。"
                        "探针已激活（事件将在归档器启动后被消费），"
                        "执行 pip install mingjing && ming start 启动归档器。",
                        file=sys.stderr,
                    )
                elif r.get("overall") == "ok":
                    pass
                else:
                    warn_items = [
                        k for k, v in r.items()
                        if isinstance(v, dict) and v.get("status") in ("warn", "crit")
                    ]
                    if warn_items:
                        print(
                            f"[ming-probe-langchain] 乾坤镜健康警告: {', '.join(warn_items)}",
                            file=sys.stderr,
                        )
            except Exception:
                pass
        finally:
            try:
                sys.meta_path = [m for m in sys.meta_path if not isinstance(m, cls)]
            except Exception:
                pass


sys.meta_path.insert(0, _MingLangChainAutoPatch())
