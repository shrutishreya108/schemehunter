"""Jinja rendering with language, translations and CSRF in every template."""

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.access import LANG_COOKIE, csrf_token
from app.i18n import LANGS, t

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def lang_for(request: Request, case_language: str | None = None, default: str = "hi") -> str:
    lang = request.cookies.get(LANG_COOKIE) or case_language or default
    return lang if lang in LANGS else default


def render(
    request: Request, name: str, lang: str, case_id: str | None = None, status_code: int = 200, **context: Any
):
    return templates.TemplateResponse(
        request,
        name,
        {
            "lang": lang,
            "t": lambda key: t(key, lang),
            "csrf": csrf_token(case_id) if case_id else "",
            "case_id": case_id,
            **context,
        },
        status_code=status_code,
    )
