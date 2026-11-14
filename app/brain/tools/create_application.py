"""create_application {scheme_id}: new application DISCOVERED → ELIGIBLE, only if the match is FIT. (LLD §8)"""

from typing import Any

from pydantic import BaseModel

from app.brain.tools.base import Ctx, ToolError, tool_schema
from app.tracker.store import create_application


class Input(BaseModel):
    scheme_id: str


SCHEMA = tool_schema(
    "create_application",
    "Start tracking an application for a scheme the rules engine says FIT. Refused for any other result. "
    "Call match_schemes first.",
    Input,
)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    ctx.scheme(data.scheme_id)  # unknown id → ToolError listing valid ids
    match = ctx.conn.execute(
        "select result from matches where case_id = %s and scheme_id = %s", (ctx.case_id, data.scheme_id)
    ).fetchone()
    if not match:
        raise ToolError("no match result yet; call match_schemes first")
    if match["result"] != "FIT":
        raise ToolError(f"cannot start an application: the rules engine says {match['result']}")
    existing = ctx.conn.execute(
        "select id, state from applications where case_id = %s and scheme_id = %s "
        "and state not in ('CLOSED', 'MISSED', 'RETRY_NEXT_CYCLE', 'SANCTIONED')",
        (ctx.case_id, data.scheme_id),
    ).fetchone()
    if existing:
        return {"application_id": str(existing["id"]), "state": existing["state"], "already_exists": True}
    app = create_application(ctx.conn, ctx.case_id, data.scheme_id, actor="agent")
    return {"application_id": app.id, "state": app.state}
