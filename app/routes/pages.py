"""HTML pages (LLD §16.2): case link, language toggle, schemes. Later-slice pages stay stubs."""

from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from app.access import (
    LANG_COOKIE,
    SESSION_COOKIE,
    SESSION_DAYS,
    case_for_token,
    client_ip,
    current_case_id,
    rate_limit,
    sign_session,
)
from app.config import get_settings
from app.db import connect
from app.i18n import LANGS
from app.routes.render import lang_for, render
from app.rules.loader import load_schemes

router = APIRouter()


@router.get("/")
def dashboard(case_id: Annotated[str, Depends(current_case_id)]) -> RedirectResponse:
    return RedirectResponse("/chat", status_code=303)


@router.get("/c/{token}")
def open_link(token: str, request: Request):
    with connect() as conn:
        if not rate_limit(conn, f"link:{client_ip(request)}", 20, 60):
            return render(
                request,
                "error.html",
                "hi",
                message="Too many attempts. Try again in a minute.",
                status_code=429,
            )
        case = case_for_token(conn, token)
    if not case:
        return render(
            request, "error.html", "hi", message="This link is not valid any more.", status_code=404
        )
    resp = RedirectResponse("/chat", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        sign_session(str(case["id"])),
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        secure=get_settings().APP_BASE_URL.startswith("https"),
        samesite="lax",
    )
    return resp


def safe_back(referer: str | None) -> str:
    """Only ever redirect to a path on this site (no //host or lookalike-domain open redirects)."""
    path = urlsplit(referer or "").path or "/"
    return path if path.startswith("/") and not path.startswith("//") else "/"


@router.get("/lang/{lang}")
def set_language(lang: str, request: Request) -> RedirectResponse:
    """Hindi/English toggle: cookie for the UI; also the case's language so Claude replies in it."""
    lang = lang if lang in LANGS else "hi"
    from app.access import optional_case_id

    case_id = optional_case_id(request)
    if case_id:
        with connect() as conn:
            conn.execute("update cases set language = %s where id = %s", (lang, case_id))
    resp = RedirectResponse(safe_back(request.headers.get("referer")), status_code=303)
    resp.set_cookie(LANG_COOKIE, lang, max_age=365 * 86400, samesite="lax")
    return resp


def scheme_cards(conn, case_id: str, lang: str) -> list[dict]:
    schemes = {s["id"]: s for s in load_schemes()}
    rows = conn.execute(
        "select scheme_id, result, rule_results, missing_facts, benefit, explanation "
        "from matches where case_id = %s",
        (case_id,),
    ).fetchall()
    order = {"FIT": 0, "NEEDS_INFO": 1, "NO_FIT": 2}
    cards = []
    for r in sorted(rows, key=lambda r: (order.get(r["result"], 3), r["scheme_id"])):
        s = schemes.get(r["scheme_id"])
        if not s:
            continue
        exp = r["explanation"] or {}
        cards.append(
            {
                "id": s["id"],
                "name": s["name"].get(lang) or s["name"]["en"],
                "result": r["result"],
                "benefit_text": ((s["benefit"].get("text") or {}).get(lang)) or "",
                "benefit": {k: v for k, v in (r["benefit"] or {}).items() if v is not None},
                "deciding_rule": (r["rule_results"] or {}).get("deciding_rule"),
                "missing": r["missing_facts"] or [],
                "explanation": exp.get(lang) or exp.get("en"),
                "citations": exp.get("citations") or [],
                "source_status": exp.get("source_status") or ("pending" if exp else None),
            }
        )
    return cards


@router.get("/schemes")
def schemes_page(request: Request, case_id: Annotated[str, Depends(current_case_id)]):
    with connect() as conn:
        case = conn.execute("select language from cases where id = %s", (case_id,)).fetchone()
        lang = lang_for(request, case["language"] if case else None)
        cards = scheme_cards(conn, case_id, lang)
    return render(request, "schemes.html", lang, case_id, cards=cards)


@router.get("/applications")
def applications_page() -> None:
    raise NotImplementedError("slice 4")


@router.get("/applications/{application_id}")
def application_detail(application_id: str) -> None:
    raise NotImplementedError("slice 4")


@router.get("/timeline")
def timeline_page() -> None:
    raise NotImplementedError("slice 4")


@router.get("/approvals")
def approvals_page() -> None:
    raise NotImplementedError("slice 7")


@router.get("/documents")
def documents_page() -> None:
    raise NotImplementedError("slice 5")
