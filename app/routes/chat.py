"""/chat (LLD §16.2): sync brain run ≤ CHAT_SYNC_TIMEOUT_S, then "working on it". /answers: no-JS fallback.

POST /chat with HX-Request returns the chat-messages partial; without HTMX it redirects back to /chat.
/answers saves the answer and re-runs the rules engine in code, so it costs no Claude credit.
"""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.access import check_csrf, current_case_id, rate_limit
from app.brain.runner import run_brain
from app.brain.tools.base import Ctx
from app.brain.tools.facts_vocab import VocabError, coerce
from app.brain.tools.match_schemes import run_match
from app.db import connect, jsonb
from app.events import log_event
from app.i18n import bilingual
from app.routes.render import lang_for, render
from app.rules.loader import load_schemes

router = APIRouter()
_runs = ThreadPoolExecutor(max_workers=2, thread_name_prefix="brain")
MAX_TEXT = 1000
WORKING = {
    "hi": "मैं इस पर काम कर रही हूँ, थोड़ी देर में यहाँ जवाब दिखेगा।",
    "en": "I'm working on it; the answer will appear here shortly.",
}


def load_chat(conn, case_id: str) -> list[dict]:
    rows = conn.execute(
        "select direction, body, attachments, created_at from messages where case_id = %s "
        "and channel = 'web' order by created_at desc limit 50",
        (case_id,),
    ).fetchall()
    return list(reversed(rows))


PENDING_SECONDS = 120  # keep polling for a reply this long after the user's message


def is_pending(msgs: list[dict]) -> bool:
    """The last message is the user's and no reply has come yet: the page should keep checking."""
    if not msgs or msgs[-1]["direction"] != "in":
        return False
    age = (datetime.now(UTC) - msgs[-1]["created_at"]).total_seconds()
    return age < PENDING_SECONDS


def case_lang(conn, case_id: str) -> str | None:
    row = conn.execute("select language from cases where id = %s", (case_id,)).fetchone()
    return row["language"] if row else None


@router.get("/chat")
def chat_page(request: Request, case_id: Annotated[str, Depends(current_case_id)]):
    with connect() as conn:
        lang = lang_for(request, case_lang(conn, case_id))
        msgs = load_chat(conn, case_id)
    return render(request, "chat.html", lang, case_id, messages=msgs, notice=None, pending=is_pending(msgs))


@router.get("/chat/messages")
def chat_messages(request: Request, case_id: Annotated[str, Depends(current_case_id)]):
    """Polled by the page while a reply is pending. Returns the message list only."""
    with connect() as conn:
        lang = lang_for(request, case_lang(conn, case_id))
        msgs = load_chat(conn, case_id)
    return render(
        request,
        "partials/chat_messages.html",
        lang,
        case_id,
        messages=msgs,
        notice=None,
        pending=is_pending(msgs),
    )


@router.post("/chat")
def post_chat(
    request: Request,
    case_id: Annotated[str, Depends(current_case_id)],
    text: Annotated[str, Form()],
    csrf: Annotated[str | None, Form()] = None,
):
    check_csrf(case_id, request.headers.get("x-csrf-token") or csrf)
    text = text.strip()[:MAX_TEXT]
    with connect() as conn:
        lang = lang_for(request, case_lang(conn, case_id))
        if text:
            if not rate_limit(conn, f"chat:{case_id}", 20, 60):
                text = ""
            else:
                conn.execute(
                    "insert into messages (case_id, direction, channel, body, processed) "
                    "values (%s, 'in', 'web', %s, true)",
                    (case_id, text),
                )
    notice = None
    if text:
        future = _runs.submit(
            run_brain, case_id, f"The user sent a chat message: «{text}». Continue onboarding.", "chat"
        )
        try:
            # Answer quickly: the user's message is already saved. A slow reply arrives by polling.
            result = future.result(timeout=0.5)
            if result.status != "DONE" and result.user_message:
                notice = result.user_message
        except FutureTimeout:
            notice = None
    if request.headers.get("hx-request"):
        with connect() as conn:
            msgs = load_chat(conn, case_id)
        return render(
            request,
            "partials/chat_messages.html",
            lang,
            case_id,
            messages=msgs,
            notice=notice,
            pending=is_pending(msgs),
        )
    return RedirectResponse("/chat", status_code=303)


@router.post("/answers")
def post_answers(
    request: Request,
    case_id: Annotated[str, Depends(current_case_id)],
    key: Annotated[str, Form()],
    value: Annotated[str, Form()],
    csrf: Annotated[str, Form()],
):
    check_csrf(case_id, csrf)
    with connect() as conn:
        lang = lang_for(request, case_lang(conn, case_id))
        try:
            clean = coerce(key, value)
        except VocabError as exc:
            msgs = load_chat(conn, case_id)
            return render(
                request, "chat.html", lang, case_id, messages=msgs, notice=str(exc), status_code=422
            )
        conn.execute(
            "insert into messages (case_id, direction, channel, body, processed) "
            "values (%s, 'in', 'web', %s, true)",
            (case_id, value),
        )
        conn.execute(
            "insert into profile_answers (case_id, key, value, source) values (%s, %s, %s, 'typed') "
            "on conflict (case_id, key) do update set value = excluded.value, source = 'typed', updated_at = now()",
            (case_id, key, jsonb(clean)),
        )
        log_event(
            conn,
            case_id,
            "user",
            "PROFILE_UPDATED",
            bilingual(f"Answered: {key}", f"जवाब दिया: {key}"),
            payload={"key": key, "via": "answers_form"},
        )
        state = conn.execute("select state_code from cases where id = %s", (case_id,)).fetchone()
        run_match(
            Ctx(
                case_id=case_id,
                conn=conn,
                schemes=load_schemes(),
                language=lang,
                state_code=state["state_code"] if state else None,
            )
        )
    return RedirectResponse("/schemes", status_code=303)
