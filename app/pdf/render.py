"""Jinja HTML → WeasyPrint PDF bytes; Noto Sans Devanagari; English fallback (LLD §13.1, §24).

render_html() never needs WeasyPrint, so templates are testable anywhere. render() needs WeasyPrint's native
libraries (Pango); the Docker image has them, a bare Windows dev box does not (PdfUnavailable).
"""

import logging
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.i18n import t

log = logging.getLogger(__name__)

APP = Path(__file__).resolve().parent.parent
FONT = APP / "static" / "fonts" / "NotoSansDevanagari-Regular.ttf"
MIN_FONT_BYTES = 10_000  # the repo once shipped a 0-byte placeholder

env = Environment(
    loader=FileSystemLoader(str(APP / "templates")),
    autoescape=select_autoescape(["html"]),
)


class PdfUnavailable(RuntimeError):
    """WeasyPrint or its native libraries are not installed on this machine."""


def font_usable() -> bool:
    return FONT.exists() and FONT.stat().st_size >= MIN_FONT_BYTES


def weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        return False
    return True


def render_html(template: str, context: dict[str, Any], lang: str = "hi") -> tuple[str, str]:
    """Returns (html, language_used). Hindi falls back to English if the Devanagari font is missing."""
    used = lang if lang == "en" or font_usable() else "en"
    if used != lang:
        log.warning("Devanagari font missing or empty; rendering %s in English", template)
    from app.finance.project_report import inr

    env.filters["inr"] = inr
    html = env.get_template(f"pdf/{template}.html").render(
        lang=used, t=lambda key: t(key, used), font_url=FONT.as_uri() if font_usable() else "", **context
    )
    return html, used


def render(template: str, context: dict[str, Any], lang: str = "hi") -> tuple[bytes, bool]:
    """Returns (pdf_bytes, fell_back_to_english)."""
    html, used = render_html(template, context, lang)
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as exc:
        raise PdfUnavailable("WeasyPrint is not available on this machine") from exc
    try:
        return HTML(string=html, base_url=str(APP)).write_pdf(), used != lang
    except Exception:
        if used == "en":
            raise
        log.exception("PDF render failed in %s; retrying in English", used)
        html, _ = render_html(template, context, "en")
        return HTML(string=html, base_url=str(APP)).write_pdf(), True
