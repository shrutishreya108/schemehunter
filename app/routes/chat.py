"""/chat (LLD §16.2): a message is saved at once and its run starts in the background; the page polls while a reply is
pending. /answers: no-JS fallback.

POST /chat with HX-Request returns the chat-messages partial; without HTMX it redirects back to /chat.
/answers saves the answer and re-runs the rules engine in code, so it costs no Claude credit.

The page knows a reply is pending when a run for this case is open, or the newest message is the user's and is
recent. When neither holds for long enough and the last run never answered, the page says so (no silence).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.access import check_csrf, current_case_id, rate_limit
from app.brain.runner import run_brain
from app.brain.tools.base import Ctx
from app.brain.tools.facts_vocab import VocabError, coerce
from app.brain.tools.match_schemes import run_match
from app.config import get_settings
from app.db import connect, jsonb
from app.events import log_event
from app.i18n import bilingual
from app.routes.plain import plain_text
from app.routes.render import lang_for, render
from app.rules.loader import load_schemes

router = APIRouter()
# one thread per waiting run; a few cases can be busy at once without starving the others
_runs = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chat-brain")
MAX_TEXT = 1000
POLL_EXTRA_S = 15  # the page keeps polling this long beyond the run deadline


def load_chat(conn, case_id: str) -> list[dict]:
    rows = conn.execute(
        "select direction, body, attachments, created_at from messages where case_id = %s "
        "and channel = 'web' order by created_at desc limit 50",
        (case_id,),
    ).fetchall()
    rows = [dict(r, body=plain_text(r["body"])) if r["direction"] == "out" else r for r in rows]
    return list(reversed(rows))


CHAT_TASK = (
    "The user's latest message is the last user turn of this conversation. Work out what they want: a question, "
    "profile information, a document, a correction to an earlier answer, a request about the application, or moving "
    "forward. Answer every question they asked, numbered if there are several, before anything else. Only then, if "
    "still needed, ask at most one profile question. Do not repeat document-upload instructions unless documents "
    "came up in this conversation."
)


def chat_task(latest: str) -> str:
    return f"{CHAT_TASK}\n\nLatest user message: «{latest}»"


def _age(msg: dict) -> float:
    return (datetime.now(UTC) - msg["created_at"]).total_seconds()


def _unanswered(msgs: list[dict]) -> dict | None:
    """The newest message, if it is the user's chat message and nothing came after it. Form answers do not count."""
    if not msgs:
        return None
    last = msgs[-1]
    if last["direction"] != "in" or (last.get("attachments") or {}).get("kind") == "answer":
        return None
    return last


def _window_s() -> float:
    return get_settings().CHAT_RUN_DEADLINE_S + POLL_EXTRA_S


def is_pending(conn, case_id: str, msgs: list[dict]) -> bool:
    """Keep polling while the user's last message is still waiting for its reply (in the queue, in the lock, or in a
    run). The message itself is the signal, so the page never goes quiet before a run row exists."""
    last = _unanswered(msgs)
    return last is not None and _age(last) < _window_s()


def gave_up(conn, case_id: str, msgs: list[dict]) -> bool:
    """The user's last message waited the whole window and nothing was written back: say so, do not stay silent."""
    last = _unanswered(msgs)
    return last is not None and _age(last) >= _window_s()


def case_lang(conn, case_id: str) -> str | None:
    row = conn.execute("select language from cases where id = %s", (case_id,)).fetchone()
    return row["language"] if row else None


def _state(conn, case_id: str, msgs: list[dict]) -> dict:
    return {"pending": is_pending(conn, case_id, msgs), "gave_up": gave_up(conn, case_id, msgs)}


@router.get("/chat")
def chat_page(request: Request, case_id: Annotated[str, Depends(current_case_id)]):
    with connect() as conn:
        lang = lang_for(request, case_lang(conn, case_id))
        msgs = load_chat(conn, case_id)
        state = _state(conn, case_id, msgs)
    return render(request, "chat.html", lang, case_id, messages=msgs, notice=None, **state)


@router.get("/chat/messages")
def chat_messages(request: Request, case_id: Annotated[str, Depends(current_case_id)]):
    """Polled by the page while a reply is pending. Returns the message list only."""
    with connect() as conn:
        lang = lang_for(request, case_lang(conn, case_id))
        msgs = load_chat(conn, case_id)
        state = _state(conn, case_id, msgs)
    return render(request, "partials/chat_messages.html", lang, case_id, messages=msgs, notice=None, oob=True, **state)


@router.post("/chat")
def post_chat(
    request: Request,
    case_id: Annotated[str, Depends(current_case_id)],
    text: Annotated[str, Form()],
    csrf: Annotated[str | None, Form()] = None,
):
    check_csrf(case_id, request.headers.get("x-csrf-token") or csrf)
    text = text.strip()[:MAX_TEXT]
    message_id = None
    with connect() as conn:
        lang = lang_for(request, case_lang(conn, case_id))
        if text:
            if not rate_limit(conn, f"chat:{case_id}", 20, 60):
                text = ""
            else:
                row = conn.execute(
                    "insert into messages (case_id, direction, channel, body, processed) "
                    "values (%s, 'in', 'web', %s, true) returning id",
                    (case_id, text),
                ).fetchone()
                message_id = row["id"]
    if text:
        # The message is saved; the run's outcome reaches the page through the chat, never through this response.
        _runs.submit(run_brain, case_id, chat_task(text), "chat", message_id=message_id)
    if request.headers.get("hx-request"):
        with connect() as conn:
            msgs = load_chat(conn, case_id)
            state = _state(conn, case_id, msgs)
        return render(request, "partials/chat_messages.html", lang, case_id, messages=msgs, notice=None, oob=True, **state)
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
                request, "chat.html", lang, case_id, messages=msgs, notice=str(exc), status_code=422,
                pending=False, gave_up=False,
            )
        conn.execute(
            "insert into messages (case_id, direction, channel, body, processed, attachments) "
            "values (%s, 'in', 'web', %s, true, %s)",
            (case_id, value, jsonb({"kind": "answer"})),
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
