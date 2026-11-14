"""schedule(): the one place DEMO_CLOCK_SCALE applies (LLD §10.1)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import get_settings
from app.db import jsonb

NEXT_WINDOW_DAYS = 30  # "next window" for rolling schemes: one month on (BUILD_NOTES §3)


def run_at_for(days_from_now: float, scale: float, now: datetime | None = None) -> datetime:
    """now + days / DEMO_CLOCK_SCALE. 1 = real time; 1440 = 1 day becomes 1 minute."""
    now = now or datetime.now(UTC)
    return now + timedelta(days=days_from_now) / scale


def schedule(
    conn,
    type: str,
    case_id: str,
    application_id: str | None = None,
    days_from_now: float = 0,
    at: datetime | None = None,
    payload: dict[str, Any] | None = None,
    scale: float | None = None,
) -> int:
    """Insert a PENDING job. `at` is an absolute time (not scaled); otherwise days_from_now / scale."""
    run_at = at or run_at_for(days_from_now, scale or get_settings().DEMO_CLOCK_SCALE)
    row = conn.execute(
        "insert into jobs (type, case_id, application_id, payload, run_at) values (%s, %s, %s, %s, %s) "
        "returning id",
        (type, case_id, application_id, jsonb(payload or {}), run_at),
    ).fetchone()
    return row["id"]
