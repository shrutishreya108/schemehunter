"""Scheme discovery pages (integration §6): /discover, /discover/{key}, and the eligibility check button.

Discovery shows what the source says. Eligibility comes only from the rules engine, and only for schemes that
have a rulebook entry. Everything shown says where it came from.
"""

import json
import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.access import check_csrf, current_case_id
from app.brain.tools.base import Ctx
from app.brain.tools.match_schemes import run_match
from app.config import get_settings
from app.db import connect
from app.discovery.matching import dedupe
from app.discovery.providers import DemoSchemeProvider, MySchemeApiProvider, ProviderError, get_provider
from app.routes.render import lang_for, render
from app.rules.loader import load_schemes
from app.rules.verification import result_label_key, warning

log = logging.getLogger(__name__)
router = APIRouter()
DISCOVERY_FIELDS = ("age_years", "gender", "category", "education_level", "sector", "business_stage", "area")


def discovery_profile(conn, case_id: str) -> dict:
    """Only the profile fields the live API is configured to receive. No names, dates of birth or addresses."""
    raw = get_settings().MYSCHEME_QUERY_MAP
    allowed = set(json.loads(raw).keys()) if raw.strip() else set()
    answers = {
        r["key"]: r["value"]
        for r in conn.execute(
            "select key, value from profile_answers where case_id = %s", (case_id,)
        ).fetchall()
    }
    case = conn.execute("select state_code from cases where id = %s", (case_id,)).fetchone()
    out = {k: answers[k] for k in DISCOVERY_FIELDS if k in answers}
    if case and case["state_code"]:
        out["state_code"] = case["state_code"]
    return {k: v for k, v in out.items() if k in allowed}


def load_items(conn, case_id: str) -> dict:
    profile = discovery_profile(conn, case_id)
    provider, note = get_provider()
    error = None
    result = None
    try:
        result = provider.search(profile)
    except ProviderError as exc:
        error = str(exc)
        if get_settings().DISCOVERY_FALLBACK_TO_DEMO and isinstance(provider, MySchemeApiProvider):
            result = DemoSchemeProvider().search(profile)
            note = f"Live myScheme failed ({exc.code}), so this is demo data."
    rulebook = load_schemes()
    items, duplicates = dedupe(result.schemes, rulebook) if result else ([], 0)
    matches = {
        r["scheme_id"]: r
        for r in conn.execute(
            "select scheme_id, result, rule_results from matches where case_id = %s", (case_id,)
        ).fetchall()
    }
    rows = []
    for it in items:
        m = matches.get(it.internal_id) if it.internal_id else None
        verification = (m["rule_results"] or {}).get("verification", "preliminary") if m else None
        rows.append(
            {
                "item": it,
                "match": m,
                "verification": verification,
                "label_key": result_label_key(m["result"], verification) if m else None,
                "warning": warning(verification, "en") if m else None,
            }
        )
    retrieved = result.retrieved_at if result else None
    return {
        "rows": rows,
        "duplicates": duplicates,
        "note": note,
        "error": error,
        "source": result.source if result else None,
        "retrieved_text": retrieved.strftime("%d %b %Y, %H:%M UTC") if retrieved else None,
        "skipped": result.skipped if result else 0,
    }


@router.get("/discover")
def discover_page(request: Request, case_id: Annotated[str, Depends(current_case_id)]):
    with connect() as conn:
        lang = lang_for(request, None)
        data = load_items(conn, case_id)
    return render(request, "discover.html", lang, case_id, **data)


@router.get("/discover/{key}")
def discover_detail(request: Request, key: str, case_id: Annotated[str, Depends(current_case_id)]):
    with connect() as conn:
        lang = lang_for(request, None)
        data = load_items(conn, case_id)
    row = next((r for r in data["rows"] if r["item"].key == key), None)
    if row is None:
        raise HTTPException(404, "scheme not found in the current results")
    return render(
        request,
        "discover_detail.html",
        lang,
        case_id,
        row=row,
        **{k: v for k, v in data.items() if k != "rows"},
    )


@router.post("/discover/{key}/check")
def discover_check(
    request: Request,
    key: str,
    case_id: Annotated[str, Depends(current_case_id)],
    csrf: Annotated[str | None, Form()] = None,
):
    check_csrf(case_id, request.headers.get("x-csrf-token") or csrf)
    with connect() as conn:
        data = load_items(conn, case_id)
        row = next((r for r in data["rows"] if r["item"].key == key), None)
        if row is None or not row["item"].internal_id:
            raise HTTPException(409, "this scheme has no rules yet, so it cannot be checked")
        ctx = Ctx(case_id=case_id, conn=conn, schemes=load_schemes(), state_code=None, language="en")
        run_match(ctx)
    return RedirectResponse(f"/discover/{key}", status_code=303)
