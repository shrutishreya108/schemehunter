"""draft_message {purpose, audience, application_id, points[]}: draft, scan, save as a PENDING action. (LLD §8, §11)

Never sends. The recipient address comes from seed/contacts.yaml, never from the model. Every body goes through the
Guard scanner; a failing draft is sent back for up to 2 rewrites, then parked as FAILED ("guard: ...") for the curator.
"""

from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, Field

from app.brain import claude
from app.brain.tools.base import MAX_REWRITES, Ctx, ToolError, scan_text, tool_schema
from app.config import ROOT
from app.events import log_event
from app.guard.actions import create_pending
from app.i18n import bilingual
from app.mouth.contacts import contact_for
from app.tracker.store import load_application

DRAFT_TOOL = {
    "name": "message_draft",
    "description": "The drafted message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "enum": ["email"]},
            "to": {"type": "string"},
            "subject": {"type": ["string", "null"]},
            "body": {"type": "string"},
            "language": {"type": "string", "enum": ["hi", "en"]},
        },
        "required": ["channel", "to", "subject", "body", "language"],
    },
}
env = Environment(loader=FileSystemLoader(str(ROOT / "app" / "templates" / "email")), autoescape=False)


class Input(BaseModel):
    purpose: str = Field(max_length=300)
    audience: Literal["bank", "officer", "user"]
    application_id: str | None = None
    points: list[str] = Field(default_factory=list, max_length=10)


SCHEMA = tool_schema(
    "draft_message",
    "Draft a message to the bank, a nodal officer, or the user. It is saved as pending and a human approves it "
    "before anything is sent. You cannot send anything yourself.",
    Input,
)


def applicant_name(ctx: Ctx) -> str:
    row = ctx.conn.execute(
        "select value from profile_answers where case_id = %s and key = 'name_latin'", (ctx.case_id,)
    ).fetchone()
    return row["value"] if row and isinstance(row["value"], str) else "the applicant"


def model_prompt(data: Input, scheme: dict | None, ref: str | None, applicant: str, language: str) -> str:
    lines = [
        f"Purpose: {data.purpose}",
        f"Audience: {data.audience}",
        f"Applicant: {applicant}",
        f"Scheme: {scheme['name']['en'] if scheme else 'none'}",
        f"Reference number: {ref or 'not issued yet'}",
        f"Language: {'Hindi' if language == 'hi' else 'English'}",
        "Points to cover:",
        *[f"- {p}" for p in data.points],
    ]
    return "\n".join(lines)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    app = None
    if data.application_id:
        app = load_application(ctx.conn, data.application_id, ctx.case_id)
        if not app:
            raise ToolError("no such application for this case")
    if data.audience != "user" and not app:
        raise ToolError(
            "a message to the bank or an officer must be about one application: pass application_id"
        )
    scheme = ctx.scheme(app.scheme_id) if app else None
    case = ctx.conn.execute("select email from cases where id = %s", (ctx.case_id,)).fetchone()
    official = data.audience in ("bank", "officer")
    if official:
        if not case["email"]:
            raise ToolError(
                "the user's email is not saved yet; ask_user for it (they are copied on official mail)"
            )
        contact = contact_for(app.scheme_id, data.audience)
        if not contact:
            raise ToolError(
                f"no {data.audience} contact on file for {app.scheme_id}; tell the user to contact them"
            )
        to = contact["email"]
    else:
        to = "web"
    applicant = applicant_name(ctx)
    prompt = model_prompt(data, scheme, app.ref_number if app else None, applicant, ctx.language)
    messages = [{"role": "user", "content": prompt}]
    draft: dict = {}
    check: dict = {"ok": False, "hits": [], "unknown_schemes": []}
    for attempt in range(MAX_REWRITES + 1):
        draft = claude.forced_tool_call(
            DRAFT_TOOL,
            [{"type": "text", "text": claude.prompt("draft_message.md")}],
            messages,
            ctx.meter,
            purpose="draft",
            client=ctx.client,
        )
        check = scan_text(ctx, draft.get("subject") or "", draft["body"])
        if check["ok"]:
            break
        messages = [
            *messages,
            {"role": "assistant", "content": draft["body"]},
            {
                "role": "user",
                "content": f"Blocked by the guard (hits={check['hits']}, unknown schemes={check['unknown_schemes']}). "
                "Rewrite without any promise and naming only the scheme in the task.",
            },
        ]
    kind = "send_email" if official else "send_chat"
    body = draft["body"]
    if official:
        template = "followup_bank.html" if data.audience == "bank" else "followup_officer.html"
        body = env.get_template(template).render(
            body=draft["body"].strip(),
            scheme=scheme["name"]["en"] if scheme else "",
            ref=app.ref_number if app else None,
            applicant=applicant,
        )
    payload = {
        "to": to,
        "subject": (draft.get("subject") or f"{scheme['name']['en'] if scheme else 'Application'} follow-up")[
            :200
        ],
        "body": body,
        "audience": data.audience,
        "language": draft.get("language", ctx.language),
        "application_id": app.id if app else None,
    }
    created = create_pending(ctx.conn, ctx.case_id, kind, payload, app.id if app else None)
    if created is None:
        return {"ok": True, "duplicate": True, "note": "an identical draft is already waiting for approval"}
    if not check["ok"]:
        ctx.conn.execute(
            "update pending_actions set status = 'FAILED', error = %s where id = %s",
            (f"guard: hits={check['hits']} unknown_schemes={check['unknown_schemes']}"[:300], created["id"]),
        )
        log_event(
            ctx.conn,
            ctx.case_id,
            "system",
            "TOOL_CALLED",
            bilingual("A draft was blocked by the guard", "एक ड्राफ़्ट गार्ड ने रोक दिया"),
            payload={"tool": "draft_message", "ok": False, "action_id": str(created["id"])},
            application_id=app.id if app else None,
        )
        return {
            "error": "the draft kept failing the guard and was parked for the curator; do not retry, tell the user you will follow up"
        }
    log_event(
        ctx.conn,
        ctx.case_id,
        "agent",
        "DRAFT_CREATED",
        bilingual("Drafted a message for you to approve", "आपके मंज़ूर करने के लिए एक संदेश तैयार किया"),
        payload={"action_id": str(created["id"]), "kind": kind, "audience": data.audience},
        application_id=app.id if app else None,
    )
    return {"pending_action_id": str(created["id"]), "short_code": created["short_code"]}
