"""Break-glass sign-in link issued by the curator (LLD signin §6, §10)."""

import re
from email import message_from_bytes, policy

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings

pytestmark = pytest.mark.db
AUTH = ("curator", "test-admin")
LINK_RE = re.compile(r"/signin/link\?t=([^\s<]+)")


@pytest.fixture
def admin(seeded, settings_db, smtp_settings, monkeypatch):
    get_settings.cache_clear()
    from app.main import app

    clients = []

    def new() -> TestClient:
        c = TestClient(app, follow_redirects=False)
        c.__enter__()
        clients.append(c)
        return c

    new.server = smtp_settings
    yield new
    for c in clients:
        c.__exit__(None, None, None)
    get_settings.cache_clear()


def issue(client, case_id):
    return client.post(f"/admin/cases/{case_id}/signin-link", auth=AUTH)


def token_from(html: str) -> str:
    return LINK_RE.search(html).group(1)


def test_needs_the_admin_password(admin, seeded) -> None:
    c = admin()
    assert c.post(f"/admin/cases/{seeded['meena']}/signin-link").status_code == 401
    assert c.post(f"/admin/cases/{seeded['meena']}/signin-link", auth=("curator", "wrong")).status_code == 401


def test_unknown_case_is_404(admin) -> None:
    c = admin()
    assert issue(c, "00000000-0000-0000-0000-000000000000").status_code == 404
    assert issue(c, "not-a-uuid").status_code == 404


def test_link_is_shown_once_and_signs_the_person_in_once(admin, seeded, conn) -> None:
    conn.execute("update cases set email = 'meena@gmail.com' where id = %s", (seeded["meena"],))
    conn.commit()
    c = admin()
    page = issue(c, seeded["meena"]).text
    token = token_from(page)
    assert "m**@gmail.com" in page and "meena@gmail.com" not in page  # the address is masked on the page
    person = TestClient(c.app, follow_redirects=False)
    person.__enter__()
    try:
        assert person.get(f"/signin/link?t={token}").status_code == 200  # preview: nothing spent
        r = person.post("/signin/link", data={"t": token})
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert person.get("/").status_code == 200  # signed in as that case
        assert person.post("/signin/link", data={"t": token}).headers["location"] == "/signin"  # used up
    finally:
        person.__exit__(None, None, None)


def test_a_second_link_supersedes_the_first(admin, seeded) -> None:
    c = admin()
    first = token_from(issue(c, seeded["meena"]).text)
    second = token_from(issue(c, seeded["meena"]).text)
    person = TestClient(c.app, follow_redirects=False)
    person.__enter__()
    try:
        assert person.post("/signin/link", data={"t": first}).headers["location"] == "/signin"
        assert person.post("/signin/link", data={"t": second}).headers["location"] == "/"
    finally:
        person.__exit__(None, None, None)


def test_event_has_no_token_and_no_address(admin, seeded, conn) -> None:
    conn.execute("update cases set email = 'meena@gmail.com' where id = %s", (seeded["meena"],))
    conn.commit()
    token = token_from(issue(admin(), seeded["meena"]).text)
    ev = conn.execute("select payload, summary from events where type = 'ADMIN_SIGNIN_LINK'").fetchone()
    text = str(ev["payload"]) + str(ev["summary"])
    assert token not in text and "meena@gmail.com" not in text and "challenge_id" in text


def test_the_link_is_also_emailed_when_the_case_has_an_address(admin, seeded, conn) -> None:
    conn.execute("update cases set email = 'meena@gmail.com' where id = %s", (seeded["meena"],))
    conn.commit()
    c = admin()
    token = token_from(issue(c, seeded["meena"]).text)
    bodies = [
        message_from_bytes(e.content, policy=policy.default).get_body().get_content()
        for e in admin.server.handler.messages
    ]
    assert any(token in b for b in bodies)


def test_no_address_means_page_only_and_no_mail(admin, seeded, conn) -> None:
    conn.execute("update cases set email = null where id = %s", (seeded["meena"],))
    conn.commit()
    c = admin()
    page = issue(c, seeded["meena"]).text
    assert token_from(page) and "no email on file" in page
    assert admin.server.handler.messages == []


def test_curators_are_limited_to_ten_links_an_hour(admin, seeded) -> None:
    c = admin()
    codes = [issue(c, seeded["meena"]).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
