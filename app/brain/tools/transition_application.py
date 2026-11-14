"""transition_application {application_id, to_state, reason}: tracker transition as actor "agent". (LLD §8)"""

from typing import Any

from pydantic import BaseModel

from app.brain.tools.base import Ctx, ToolError, tool_schema
from app.tracker.states import STATES, IllegalTransition, allowed_next, transition
from app.tracker.store import DbTrackerStore, load_application


class Input(BaseModel):
    application_id: str
    to_state: str
    reason: str = ""


SCHEMA = tool_schema(
    "transition_application",
    "Move an application to its next state. The tracker refuses illegal moves and tells you what is allowed. "
    "Only the user can mark SUBMITTED or RESUBMITTED.",
    Input,
)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    if data.to_state not in STATES:
        raise ToolError(f"unknown state {data.to_state!r}; valid: {', '.join(sorted(STATES))}")
    app = load_application(ctx.conn, data.application_id, ctx.case_id)
    if not app:
        raise ToolError("no such application for this case")
    try:
        transition(app, data.to_state, "agent", data.reason[:200], DbTrackerStore(ctx.conn))
    except IllegalTransition as exc:
        return {"error": str(exc), "allowed_next_for_agent": allowed_next(app.state, "agent")}
    return {"ok": True, "state": app.state}
