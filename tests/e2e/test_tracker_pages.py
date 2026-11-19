"""/applications, /applications/{id}, /timeline: session-scoped, bilingual, and the seeded diaries read well."""

import re

import pytest
from fastapi.testclient import TestClient

from app.access import SESSION_COOKIE, sign_session

pytestmark = pytest.mark.db


@pytest.fixture
def client_for(seeded, settings_db):
    from app.config import get_settings
    from app.main import app

    get_settings.cache_clear()
    clients = []

    def make(persona: str) -> TestClient:
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        c.cookies.set(SESSION_COOKIE, sign_session(seeded[persona]))
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)
    get_settings.cache_clear()


def get_in(client: TestClient, path: str, lang: str):
    client.get(f"/lang/{lang}")  # sets the language cookie on the client
    return client.get(path)


def app_id(conn, case_id: str) -> str:
    return str(conn.execute("select id from applications where case_id = %s", (case_id,)).fetchone()["id"])


def test_pages_need_a_session(seeded, settings_db) -> None:
    from app.main import app

    with TestClient(app) as anon:
        for path in ("/applications", "/timeline", f"/applications/{'0' * 8}-0000-0000-0000-{'0' * 12}"):
            assert anon.get(path).status_code == 401


def test_ravi_applications_page_is_parcel_tracking(client_for, seeded, conn) -> None:
    ravi = client_for("ravi")
    page = ravi.get("/applications").text
    assert "PMEGP/KA/2026/04471" in page and "Under review" in page
    assert page.count('class="step done"') >= 5 and 'class="step now"' in page
    en = get_in(ravi, "/applications", "en").text
    assert "Under review" in en and "No news for 11 days" in en


def test_ravi_detail_shows_only_his_application_events(client_for, seeded, conn) -> None:
    ravi = client_for("ravi")
    page = get_in(ravi, f"/applications/{app_id(conn, seeded['ravi'])}", "en").text
    assert "Submitted with reference PMEGP/KA/2026/04471" in page
    assert "answered the onboarding questions" not in page  # profile event isn't tied to the application


def test_another_cases_application_is_a_404(client_for, seeded, conn) -> None:
    meena = client_for("meena")
    assert meena.get(f"/applications/{app_id(conn, seeded['ravi'])}").status_code == 404
    assert meena.get("/applications/not-a-uuid").status_code == 404


def test_timeline_is_in_order_and_in_the_users_language(client_for, seeded) -> None:
    ravi = client_for("ravi")
    hi = get_in(ravi, "/timeline", "hi").text
    en = get_in(ravi, "/timeline", "en").text
    assert "Ravi answered the onboarding questions" in hi and "Ravi answered the onboarding questions" in en
    assert (
        en.index("answered the onboarding")
        < en.index("Submitted with reference")
        < en.index("no new message")
    )


@pytest.mark.parametrize("persona", ["ravi", "fatima"])
def test_seeded_diaries_read_like_a_diary(client_for, persona) -> None:
    """No raw state codes (DOCS_PENDING, UNDER_REVIEW...) in the sentences people read."""
    for lang in ("en", "hi"):
        page = get_in(client_for(persona), "/timeline", lang).text
        diary = page[page.index('class="diary"') :]
        sentences = re.findall(r'<span class="what">(.*?)</span>', diary)
        assert len(sentences) >= 9
        assert not [s for s in sentences if re.search(r"\b[A-Z]{3,}_[A-Z_]+\b", s)], sentences
