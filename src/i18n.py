import gettext
import os
from pathlib import Path

_LOCALE_DIR = Path(__file__).parent / "locales"
_current_translation = None


def _(msg: str) -> str:
    if _current_translation is not None:
        return _current_translation.gettext(msg)
    return msg


def set_language(lang: str):
    global _current_translation
    t = gettext.translation(
        "mingjing", localedir=_LOCALE_DIR, languages=[lang], fallback=True
    )
    _current_translation = t
    t.install()


def init_i18n():
    lang = os.getenv("MING_LANG", "zh")
    set_language(lang)
