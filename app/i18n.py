"""Static UI strings from locales/{hi,en}.json; bilingual() for code-generated summaries."""

import json
from functools import lru_cache
from pathlib import Path

LOCALES_DIR = Path(__file__).parent / "locales"
LANGS = ("en",)  # English only for now; Hindi strings stay in the files for later
DEFAULT_LANG = "en"


@lru_cache
def _load(lang: str) -> dict[str, str]:
    return json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def t(key: str, lang: str = DEFAULT_LANG) -> str:
    """Look up a UI string; falls back to English, then to the key itself."""
    return _load(lang).get(key) or _load("en").get(key) or key


def bilingual(en: str, hi: str | None = None) -> dict[str, str]:
    """Build a {"hi", "en"} summary. If hi is missing, try the locale key, else reuse English."""
    return {"en": en, "hi": hi or _load("hi").get(en) or en}
