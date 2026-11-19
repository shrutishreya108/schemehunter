"""Home after sign-in: whose case it is and each scheme's result, with the reason (no personal link or PIN)."""

import pytest
from fastapi.testclient import TestClient

from app.access import SESSION_COOKIE, sign_session

pytestmark = pytest.mark.db


def test_home_shows_whose_case_and_eligibility(seeded, settings_db) -> None:
    from app.main import app

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sign_session(seeded["meena"]))
        page = client.get("/").text
    assert "Meena" in page
    assert "आप आवेदन करने के योग्य हैं" in page or "You qualify to apply" in page  # PMEGP fits for Meena
    assert 'href="/applications"' in page and 'href="/approvals"' in page


def test_home_for_ravi_is_his_case(seeded, settings_db) -> None:
    from app.main import app

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sign_session(seeded["ravi"]))
        assert "Ravi" in client.get("/").text


def test_preliminary_result_never_reads_as_unconditional(seeded, settings_db, conn) -> None:
    """The PMEGP rules are not yet checked against official guidelines: no bare 'You qualify to apply'."""
    from app.main import app

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sign_session(seeded["ravi"]))
        page = client.get("/").text
        schemes = client.get("/schemes").text
    for text in (page, schemes):
        assert "You qualify to apply for PMEGP" not in text
        assert "Preliminary" in text or "प्रारंभिक" in text
    assert "not yet checked" in page or "जाँचे नहीं गए" in page
