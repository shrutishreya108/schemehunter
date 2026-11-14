"""schedule {type, days_from_now, application_id?}: set an alarm. The demo clock is applied in one place. (LLD §8)"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.brain.tools.base import Ctx, ToolError, tool_schema
from app.clock.schedule import schedule
from app.tracker.store import load_application


class Input(BaseModel):
    type: Literal["status_check", "chase", "doc_nudge", "deadline_reminders", "retry_cycle"]
    days_from_now: float = Field(default=1, ge=0, le=90)
    application_id: str | None = None


SCHEMA = tool_schema(
    "schedule",
    "Set an alarm to check status, nudge for papers, chase, remind about a deadline or retry next cycle.",
    Input,
)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    if data.application_id and not load_application(ctx.conn, data.application_id, ctx.case_id):
        raise ToolError("no such application for this case")
    job_id = schedule(ctx.conn, data.type, ctx.case_id, data.application_id, days_from_now=data.days_from_now)
    return {"job_id": job_id}
