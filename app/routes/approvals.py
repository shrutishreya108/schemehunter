"""/approvals: the Needs-you inbox. Approve or decline; the session must own the case (LLD §11, §16)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.access import bind_token, check_csrf, current_case_id
from app.config import get_settings
from app.db import connect
from app.guard import actions
from app.routes.render import lang_for, render

router = APIRouter()


def case_language(conn, case_id: str) -> str | None:
    row = conn.execute("select language from cases where id = %s", (case_id,)).fetchone()
    return row["language"] if row else None


def telegram_link(conn, case_id: str) -> str | None:
    s = get_settings()
    row = conn.execute("select telegram_chat_id from cases where id = %s", (case_id,)).fetchone()
    if not s.TELEGRAM_BOT_USERNAME or (row and row["telegram_chat_id"]):
        return None
    return f"https://t.me/{s.TELEGRAM_BOT_USERNAME}?start={bind_token(case_id)}"


@router.get("/approvals")
def approvals_page(request: Request, case_id: Annotated[str, Depends(current_case_id)]):
    with connect() as conn:
        lang = lang_for(request, case_language(conn, case_id))
        items = [
            {
                "id": str(a["id"]),
                "code": a["short_code"],
                "status": a["status"],
                "line": actions.one_line(a["kind"], a["payload"], lang),
                "kind": a["kind"],
                "body": a["payload"].get("body") if a["kind"] in ("send_email", "send_chat") else None,
                "to_role": a["payload"].get("audience"),
                "error": a["error"],
                "created_at": a["created_at"],
            }
            for a in actions.pending_for_case(conn, case_id)
            if not (a["status"] == "FAILED" and str(a["error"] or "").startswith("guard:"))  # curator-only
        ]
        link = telegram_link(conn, case_id)
    return render(request, "approvals.html", lang, case_id, items=items, telegram_link=link)


def decide(action_id: str, case_id: str, request: Request, csrf: str | None, approve: bool):
    check_csrf(case_id, request.headers.get("x-csrf-token") or csrf)
    try:
        UUID(action_id)
    except ValueError:
        raise HTTPException(404, "not found") from None
    with connect() as conn:
        try:
            if approve:
                actions.approve(conn, action_id, "web", case_id=case_id)
            else:
                actions.reject(conn, action_id, "web", case_id=case_id)
        except actions.GuardError:
            raise HTTPException(404, "not found") from None
    return RedirectResponse("/approvals", status_code=303)


@router.post("/approvals/{action_id}/approve")
def approve(
    action_id: str,
    request: Request,
    case_id: Annotated[str, Depends(current_case_id)],
    csrf: Annotated[str | None, Form()] = None,
):
    return decide(action_id, case_id, request, csrf, True)


@router.post("/approvals/{action_id}/reject")
def reject(
    action_id: str,
    request: Request,
    case_id: Annotated[str, Depends(current_case_id)],
    csrf: Annotated[str | None, Form()] = None,
):
    return decide(action_id, case_id, request, csrf, False)
