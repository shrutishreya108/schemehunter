"""log_event(): the only writer to the events table (LLD §17)."""

from typing import Any
from uuid import UUID

import psycopg

from app.db import jsonb

EVENT_TYPES = {
    "PROFILE_UPDATED",
    "DOC_UPLOADED",
    "DOC_CHECKED",
    "MATCHED",
    "STATE_CHANGED",
    "DRAFT_CREATED",
    "ACTION_APPROVED",
    "ACTION_REJECTED",
    "MESSAGE_SENT",
    "MESSAGE_RECEIVED",
    "STATUS_CHECKED",
    "REMINDER_SENT",
    "ADMIN_SIGNIN_LINK",
    "TOOL_CALLED",
    "RUN_STUCK",
}
ACTORS = {"user", "agent", "clock", "system"}


def log_event(
    conn: psycopg.Connection,
    case_id: UUID | str,
    actor: str,
    type: str,
    summary: dict[str, str],
    payload: dict[str, Any] | None = None,
    application_id: UUID | str | None = None,
    created_at: Any = None,
) -> int:
    """Append one event. summary is {"hi": ..., "en": ...}; payload holds ids and small facts only."""
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r}")
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type!r}")
    row = conn.execute(
        "insert into events (case_id, application_id, actor, type, summary, payload, created_at) "
        "values (%s, %s, %s, %s, %s, %s, coalesce(%s, clock_timestamp())) returning id",
        (
            case_id,
            application_id,
            actor,
            type,
            jsonb(summary),
            jsonb(payload) if payload else None,
            created_at,
        ),
    ).fetchone()
    return row["id"]
