"""interpret_response {message_id | text, application_id?}: read a portal SMS / letter / user text, then apply it. (LLD §8)

Claude only classifies and extracts (MODEL_CHEAP, forced schema). Code decides what to do with it:
the state changes only if confidence >= 0.7 and the transition is legal; otherwise the user is asked.
"""

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, model_validator

from app.brain import claude
from app.brain.tools.base import Ctx, ToolError, tool_schema
from app.config import get_settings
from app.events import log_event
from app.i18n import bilingual
from app.mouth.outbox import notify_user
from app.tracker.states import IllegalTransition, transition
from app.tracker.store import DbTrackerStore, load_application

CONFIDENCE_MIN = 0.7
INTENTS = ["status_update", "query_raised", "rejected", "sanctioned", "question", "approval_reply", "other"]
INTERPRET_TOOL = {
    "name": "interpret_response",
    "description": "What this message says.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {"type": "string", "enum": INTENTS},
            "application_id_guess": {"type": ["string", "null"]},
            "ref_number": {"type": ["string", "null"]},
            "reason": {"type": ["string", "null"]},
            "missing_docs": {"type": "array", "items": {"type": "string"}},
            "deadline": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
        },
        "required": ["intent", "confidence"],
    },
}
CLARIFY = {
    "en": "I'm not sure what that message means. Which application is it about, and what does it say?",
    "hi": "मुझे पक्का समझ नहीं आया कि यह संदेश क्या कहता है। यह किस आवेदन के बारे में है, और उसमें क्या लिखा है?",
}


class Input(BaseModel):
    message_id: str | None = None
    text: str | None = None
    application_id: str | None = None

    @model_validator(mode="after")
    def one_source(self) -> "Input":
        if not self.message_id and not (self.text or "").strip():
            raise ValueError("give message_id or text")
        return self


SCHEMA = tool_schema(
    "interpret_response",
    "Read a forwarded SMS, email, rejection letter or user message and apply it to the right application: "
    "moves the state if the model is confident and the move is legal, otherwise returns a clarifying question.",
    Input,
)


def classify(ctx_like: Any, conn, case_id: str, text: str, today: date | None = None) -> dict:
    """One forced-schema call. ctx_like needs .meter and .client."""
    apps = conn.execute(
        "select id, scheme_id, ref_number, state from applications where case_id = %s order by created_at",
        (case_id,),
    ).fetchall()
    listing = "\n".join(
        f"- {a['id']}: {a['scheme_id']} ref={a['ref_number']} state={a['state']}" for a in apps
    )
    task = f"Today is {(today or date.today()).isoformat()}.\nApplications:\n{listing or '(none)'}\n\nMessage:\n{text[:2000]}"
    return claude.forced_tool_call(
        INTERPRET_TOOL,
        [{"type": "text", "text": claude.prompt("interpret_response.md")}],
        [{"role": "user", "content": task}],
        ctx_like.meter,
        purpose="classify",
        model=get_settings().MODEL_CHEAP,
        max_tokens=600,
        client=ctx_like.client,
    )


def is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def resolve_application(conn, case_id: str, result: dict, hint: str | None):
    for candidate in (hint, result.get("application_id_guess")):
        if candidate and is_uuid(candidate):  # a malformed id from the model is just "not found"
            app = load_application(conn, candidate, case_id)
            if app:
                return app
    ref = result.get("ref_number")
    if ref:
        row = conn.execute(
            "select id from applications where case_id = %s and ref_number = %s", (case_id, ref)
        ).fetchone()
        if row:
            return load_application(conn, str(row["id"]), case_id)
    open_ones = conn.execute(
        "select id from applications where case_id = %s and state not in ('CLOSED','MISSED','RETRY_NEXT_CYCLE','SANCTIONED')",
        (case_id,),
    ).fetchall()
    return load_application(conn, str(open_ones[0]["id"]), case_id) if len(open_ones) == 1 else None


def move(conn, app, path: list[str], reason: str, payload: dict) -> None:
    """Walk a short legal path as the agent; a step that is already behind us is skipped."""
    store = DbTrackerStore(conn)
    for to in path:
        if app.state == to:
            continue
        transition(app, to, "agent", reason, store, payload)


def apply_result(
    conn, case_id: str, result: dict, hint: str | None = None, message_id: str | None = None
) -> dict:
    """Code decides: confidence gate, application match, legal transition."""
    if message_id:
        conn.execute(
            "update messages set processed = true where id = %s and case_id = %s", (message_id, case_id)
        )
    confidence = float(result.get("confidence") or 0)
    intent = result.get("intent", "other")
    if confidence < CONFIDENCE_MIN:
        return {"needs_clarification": True, "question": CLARIFY, "confidence": confidence}
    if intent in ("question", "approval_reply", "other"):
        return {"intent": intent, "applied": False}
    app = resolve_application(conn, case_id, result, hint)
    if not app:
        return {
            "needs_clarification": True,
            "question": CLARIFY,
            "confidence": confidence,
            "why": "no unique application",
        }
    ref = result.get("ref_number")
    if ref and not app.ref_number:
        conn.execute("update applications set ref_number = %s where id = %s", (ref, app.id))
        app.ref_number = ref
    if result.get("deadline"):
        try:
            conn.execute(
                "update applications set deadline = %s where id = %s",
                (date.fromisoformat(result["deadline"]), app.id),
            )
        except ValueError:
            pass  # a bad date from the model is ignored, not stored
    before = app.state
    payload = {"intent": intent, "missing_docs": result.get("missing_docs") or []}
    paths = {
        "status_update": ["UNDER_REVIEW"] if app.state in ("SUBMITTED", "RESUBMITTED") else [],
        "query_raised": ["UNDER_REVIEW", "QUERY_RAISED", "DOCS_PENDING"]
        if app.state in ("SUBMITTED", "RESUBMITTED", "UNDER_REVIEW")
        else [],
        "rejected": ["UNDER_REVIEW", "REJECTED"]
        if app.state in ("SUBMITTED", "RESUBMITTED", "UNDER_REVIEW")
        else [],
        "sanctioned": ["UNDER_REVIEW", "SANCTIONED"]
        if app.state in ("SUBMITTED", "RESUBMITTED", "UNDER_REVIEW")
        else [],
    }
    path = paths.get(intent, [])
    try:
        move(conn, app, path, (result.get("reason") or intent)[:200], payload)
    except IllegalTransition as exc:
        return {"intent": intent, "applied": False, "error": str(exc)}
    log_event(
        conn,
        case_id,
        "agent",
        "STATUS_CHECKED",
        bilingual(f"Read a message: {intent.replace('_', ' ')}", f"संदेश पढ़ा: {intent.replace('_', ' ')}"),
        payload={**payload, "from": before, "to": app.state},
        application_id=app.id,
    )
    if intent == "query_raised" and result.get("missing_docs"):
        docs = ", ".join(d.replace("_", " ") for d in result["missing_docs"])
        notify_user(
            conn,
            case_id,
            {
                "en": f"They asked for: {docs}. Please send a clear photo and I'll take it from there.",
                "hi": f"उन्होंने माँगा है: {docs}। कृपया साफ़ फ़ोटो भेजिए, आगे मैं देख लूँगी।",
            },
            kind="query_raised",
            application_id=app.id,
        )
    return {"intent": intent, "applied": app.state != before, "application_id": app.id, "state": app.state}


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    text = (data.text or "").strip()
    message_id = None
    if data.message_id:
        row = ctx.conn.execute(
            "select id, body from messages where id = %s and case_id = %s and direction = 'in'",
            (data.message_id, ctx.case_id),
        ).fetchone()
        if not row:
            raise ToolError("no such inbound message for this case")
        text, message_id = row["body"] or "", str(row["id"])
    result = classify(ctx, ctx.conn, ctx.case_id, text)
    return apply_result(ctx.conn, ctx.case_id, result, data.application_id, message_id)
