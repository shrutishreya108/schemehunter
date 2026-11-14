"""schedule(): the one place DEMO_CLOCK_SCALE applies (LLD §10.1)."""

from datetime import UTC, datetime, timedelta


def run_at_for(days_from_now: float, scale: float, now: datetime | None = None) -> datetime:
    """now + days / DEMO_CLOCK_SCALE. 1 = real time; 1440 = 1 day becomes 1 minute."""
    now = now or datetime.now(UTC)
    return now + timedelta(days=days_from_now) / scale


def schedule(type: str, days_from_now: float = 0, at: datetime | None = None, **kw) -> int:
    raise NotImplementedError("slice 6")
