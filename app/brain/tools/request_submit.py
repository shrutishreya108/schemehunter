"""request_submit {application_id, pack_document_id}: put the "press submit" instruction in Needs-you. (LLD §8)

The system never submits anything. This moves FORM_READY → AWAITING_HUMAN_SUBMIT (the tracker effect creates the
`instruct_submit` pending action) and attaches the prefill pack to it. The person presses the real button.
"""

from typing import Any

from pydantic import BaseModel

from app.brain.tools.base import Ctx, ToolError, tool_schema
from app.db import jsonb
from app.tracker.states import IllegalTransition, transition
from app.tracker.store import DbTrackerStore, load_application


class Input(BaseModel):
    application_id: str
    pack_document_id: str


SCHEMA = tool_schema(
    "request_submit",
    "Ask the user to press submit on the portal. Needs the pre-filled pack from prefill_form. Nothing is "
    "submitted by us: the user does it with their own OTP.",
    Input,
)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    app = load_application(ctx.conn, data.application_id, ctx.case_id)
    if not app:
        raise ToolError("no such application for this case")
    pack = ctx.conn.execute(
        "select id from documents where id = %s and case_id = %s and doc_type = 'prefill_pack'",
        (data.pack_document_id, ctx.case_id),
    ).fetchone()
    if not pack:
        raise ToolError("pack_document_id must be the pack returned by prefill_form")
    if app.state == "FORM_READY":
        try:
            transition(app, "AWAITING_HUMAN_SUBMIT", "agent", "pack ready", DbTrackerStore(ctx.conn))
        except IllegalTransition as exc:
            raise ToolError(str(exc)) from None
    elif app.state != "AWAITING_HUMAN_SUBMIT":
        raise ToolError(f"application is {app.state}; it must be FORM_READY first")
    row = ctx.conn.execute(
        "update pending_actions set payload = payload || %s where application_id = %s "
        "and kind = 'instruct_submit' and status = 'PENDING' returning short_code",
        (jsonb({"pack_document_id": data.pack_document_id}), app.id),
    ).fetchone()
    return {"ok": True, "state": app.state, "short_code": row["short_code"] if row else None}
