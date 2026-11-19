"""/admin/web: basic auth, one row per curated page, age and the over-a-month flag. Needs Postgres."""

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = pytest.mark.db


def test_admin_web_page_needs_the_password_and_shows_each_copy_age(seeded, conn, settings_db) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    conn.execute("delete from web_cache")
    conn.execute(
        "insert into web_cache (url, scheme_id, title, text, content_sha256, fetched_at, last_status, error) values "
        "('https://kswdc.karnataka.gov.in/21/udyogini/en', 'udyogini_ka', 'Udyogini page', 'Text.', 'x', %s, 200, null), "
        "('https://financialservices.gov.in/pradhan-mantri-mudra-yojana-pmmy', 'mudra', 'MUDRA page', 'Text.', 'x', %s, 200, null)",
        (datetime.now(UTC) - timedelta(days=45), datetime.now(UTC) - timedelta(days=1)),
    )
    conn.commit()
    with TestClient(app) as client:
        assert client.get("/admin/web").status_code == 401
        page = client.get("/admin/web", auth=("curator", "test-admin")).text
    assert "Udyogini page" in page and "MUDRA page" in page
    assert "(over a month old)" in page  # the 45-day-old copy only
    assert page.count("(over a month old)") == 1
