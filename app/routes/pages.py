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


JOURNEY = [
    "ELIGIBLE",
    "DOCS_PENDING",
    "DOCS_READY",
    "FORM_READY",
    "AWAITING_HUMAN_SUBMIT",
    "SUBMITTED",
    "UNDER_REVIEW",
    "SANCTIONED",
]


def journey_steps(state: str, lang: str) -> list[dict]:
    """Parcel-tracking rows: every stop on the usual road, marked done / current / later."""
    from app.tracker.store import state_label

    path = list(JOURNEY)
    if state not in path:  # off-road states (query, rejected, missed...) show as the current stop
        at = {"QUERY_RAISED": "UNDER_REVIEW", "RESUBMITTED": "UNDER_REVIEW", "REJECTED": "UNDER_REVIEW"}.get(
            state, "ELIGIBLE"
        )
        path.insert(path.index(at) + 1, state)
    here = path.index(state)
    return [
        {
            "state": st,
            "label": state_label(st, lang),
            "status": "done" if i < here else "now" if i == here else "next",
        }
        for i, st in enumerate(path)
    ]


def event_rows(rows: list[dict], lang: str) -> list[dict]:
    return [
        {
            "when": r["created_at"],
            "actor": r["actor"],
            "type": r["type"],
            "text": (r["summary"] or {}).get(lang) or (r["summary"] or {}).get("en") or r["type"],
        }
        for r in rows
    ]


def days_since(ts) -> int:
    from datetime import UTC, datetime

    return max(0, (datetime.now(UTC) - ts).days)


@router.get("/applications")
def applications_page(request: Request, case_id: Annotated[str, Depends(current_case_id)]):
    from app.tracker.store import state_label

    schemes = {s["id"]: s for s in load_schemes()}
    with connect() as conn:
        case = conn.execute("select language from cases where id = %s", (case_id,)).fetchone()
        lang = lang_for(request, case["language"] if case else None)
        rows = conn.execute(
            "select id, scheme_id, state, ref_number, deadline, last_movement_at from applications "
            "where case_id = %s order by created_at",
            (case_id,),
        ).fetchall()
    apps = [
        {
            "id": str(r["id"]),
            "name": (schemes[r["scheme_id"]]["name"].get(lang) or schemes[r["scheme_id"]]["name"]["en"])
            if r["scheme_id"] in schemes
            else r["scheme_id"],
            "state": r["state"],
            "label": state_label(r["state"], lang),
            "ref_number": r["ref_number"],
            "deadline": r["deadline"],
            "quiet_days": days_since(r["last_movement_at"]),
            "steps": journey_steps(r["state"], lang),
        }
        for r in rows
    ]
    return render(request, "applications.html", lang, case_id, apps=apps)


@router.get("/applications/{application_id}")
def application_detail(
    application_id: str, request: Request, case_id: Annotated[str, Depends(current_case_id)]
):
    from uuid import UUID

    from fastapi import HTTPException

    from app.tracker.store import state_label

    try:
        UUID(application_id)
    except ValueError:
        raise HTTPException(404, "not found") from None
    schemes = {s["id"]: s for s in load_schemes()}
    with connect() as conn:
        case = conn.execute("select language from cases where id = %s", (case_id,)).fetchone()
        lang = lang_for(request, case["language"] if case else None)
        row = conn.execute(
            "select id, scheme_id, state, ref_number, deadline, last_movement_at from applications "
            "where id = %s and case_id = %s",
            (application_id, case_id),  # another case's application looks like it doesn't exist
        ).fetchone()
        if not row:
            raise HTTPException(404, "not found")
        events = conn.execute(
            "select created_at, actor, type, summary from events "
            "where case_id = %s and application_id = %s order by created_at",
            (case_id, application_id),
        ).fetchall()
    scheme = schemes.get(row["scheme_id"])
    app = {
        "id": str(row["id"]),
        "name": (scheme["name"].get(lang) or scheme["name"]["en"]) if scheme else row["scheme_id"],
        "label": state_label(row["state"], lang),
        "ref_number": row["ref_number"],
        "deadline": row["deadline"],
        "quiet_days": days_since(row["last_movement_at"]),
        "steps": journey_steps(row["state"], lang),
    }
    return render(request, "application_detail.html", lang, case_id, app=app, events=event_rows(events, lang))


@router.get("/timeline")
def timeline_page(request: Request, case_id: Annotated[str, Depends(current_case_id)]):
    with connect() as conn:
        case = conn.execute("select language from cases where id = %s", (case_id,)).fetchone()
        lang = lang_for(request, case["language"] if case else None)
        events = conn.execute(
            "select created_at, actor, type, summary from events where case_id = %s order by created_at",
            (case_id,),
        ).fetchall()
    return render(request, "timeline.html", lang, case_id, events=event_rows(events, lang))


@router.get("/approvals")
def approvals_page() -> None:
    raise NotImplementedError("slice 7")
