from datetime import UTC, datetime, timedelta

from app.clock.schedule import run_at_for

NOW = datetime(2026, 11, 16, tzinfo=UTC)


def test_real_time() -> None:
    assert run_at_for(1, 1, NOW) == NOW + timedelta(days=1)


def test_demo_scale_one_day_is_one_minute() -> None:
    assert run_at_for(1, 1440, NOW) == NOW + timedelta(minutes=1)
    assert run_at_for(10, 1440, NOW) == NOW + timedelta(minutes=10)
