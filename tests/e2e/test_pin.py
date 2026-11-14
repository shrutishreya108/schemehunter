"""Optional 4-digit PIN (LLD §16.1) and the friendly 500 page."""

import time

import pytest
from fastapi.testclient import TestClient

from app.access import PIN_COOKIE, csrf_token, demo_link_token, hash_pin, read_pin_pending, sign_pin_pending
from app.config import get_settings

pytestmark = pytest.mark.db


@pytest.fixture
def app_client(seeded, settings_db):
    from app.main import app

    get_settings.cache_clear()
    clients = []

    def new() -> TestClient:
        c = TestClient(app, follow_redirects=False)
        c.__enter__()
        clients.append(c)
        return c

    yield new
    for c in clients:
        c.__exit__(None, None, None)
    get_settings.cache_clear()


def login(client: TestClient, persona: str):
    return client.get(f"/c/{demo_link_token(persona)}")


def set_pin(client: TestClient, case_id: str, pin: str):
    return client.post("/pin/set", data={"pin": pin, "csrf": csrf_token(case_id)})


def test_a_case_without_a_pin_opens_straight_away(app_client, seeded) -> None:
    c = app_client()
    r = login(c, "meena")
    assert r.status_code == 303 and r.headers["location"] == "/chat" and c.get("/chat").status_code == 200


def test_pin_is_asked_on_a_new_phone_and_unlocks_the_session(app_client, seeded, conn) -> None:
    phone1 = app_client()
    login(phone1, "meena")
    assert set_pin(phone1, seeded["meena"], "4821").status_code == 303
    stored = conn.execute("select pin_hash from cases where id = %s", (seeded["meena"],)).fetchone()[
        "pin_hash"
    ]
    assert stored == hash_pin(seeded["meena"], "4821") and "4821" not in stored  # only a keyed hash is stored

    phone2 = app_client()
    r = login(phone2, "meena")
    assert (
        r.headers["location"] == "/pin" and phone2.get("/chat").status_code == 401
    )  # link alone gives no session
    assert phone2.get("/pin").status_code == 200
    wrong = phone2.post("/pin", data={"pin": "0000"})
    assert wrong.status_code == 403 and "सही नहीं" in wrong.text  # Hindi is the default language
    assert phone2.get("/chat").status_code == 401
    ok = phone2.post("/pin", data={"pin": "4821"})
    assert ok.status_code == 303 and ok.headers["location"] == "/chat"
    assert phone2.get("/chat").status_code == 200  # session now set; the pending cookie is gone
    assert phone2.cookies.get(PIN_COOKIE) is None


def test_five_wrong_tries_lock_even_the_right_pin(app_client, seeded) -> None:
    owner = app_client()
    login(owner, "meena")
    set_pin(owner, seeded["meena"], "4821")
    thief = app_client()
    login(thief, "meena")
    for _ in range(5):
        assert thief.post("/pin", data={"pin": "1111"}).status_code == 403
    locked = thief.post("/pin", data={"pin": "4821"})  # the right PIN, but too late
    assert locked.status_code == 403 and thief.get("/chat").status_code == 401


def test_pin_page_and_submit_need_the_pending_cookie(app_client, seeded) -> None:
    c = app_client()
    assert c.get("/pin").status_code == 401 and c.post("/pin", data={"pin": "1234"}).status_code == 401


def test_pending_cookie_expires_and_cannot_be_forged() -> None:
    cookie = sign_pin_pending("case-1", now=time.time())
    assert read_pin_pending(cookie) == "case-1"
    assert read_pin_pending(sign_pin_pending("case-1", now=time.time() - 3600)) is None  # 10 minutes only
    assert read_pin_pending(cookie[:-1] + ("0" if cookie[-1] != "0" else "1")) is None
    assert read_pin_pending(None) is None and read_pin_pending("junk") is None


def test_a_pending_cookie_is_not_a_session(app_client, seeded) -> None:
    owner = app_client()
    login(owner, "meena")
    set_pin(owner, seeded["meena"], "4821")
    phone = app_client()
    login(phone, "meena")
    for path in ("/chat", "/applications", "/documents", "/approvals", "/timeline"):
        assert phone.get(path).status_code == 401


def test_pin_can_be_cleared(app_client, seeded, conn) -> None:
    c = app_client()
    login(c, "meena")
    set_pin(c, seeded["meena"], "4821")
    set_pin(c, seeded["meena"], "")
    assert (
        conn.execute("select pin_hash from cases where id = %s", (seeded["meena"],)).fetchone()["pin_hash"]
        is None
    )
    other = app_client()
    assert login(other, "meena").headers["location"] == "/chat"


@pytest.mark.parametrize("bad", ["123", "12345", "abcd", "12 4", "١٢٣٤"])
def test_only_four_ascii_digits_are_accepted(app_client, seeded, conn, bad) -> None:
    c = app_client()
    login(c, "meena")
    assert set_pin(c, seeded["meena"], bad).status_code == 422
    assert (
        conn.execute("select pin_hash from cases where id = %s", (seeded["meena"],)).fetchone()["pin_hash"]
        is None
    )


def test_setting_a_pin_needs_a_session_and_csrf(app_client, seeded) -> None:
    anon = app_client()
    assert anon.post("/pin/set", data={"pin": "1234"}).status_code == 401
    c = app_client()
    login(c, "meena")
    assert c.post("/pin/set", data={"pin": "1234"}).status_code == 403
    assert c.post("/pin/set", data={"pin": "1234", "csrf": "wrong"}).status_code == 403


def test_one_persons_pin_does_not_affect_another(app_client, seeded) -> None:
    c = app_client()
    login(c, "meena")
    set_pin(c, seeded["meena"], "4821")
    other = app_client()
    assert login(other, "ravi").headers["location"] == "/chat"


def test_a_crash_shows_the_friendly_page_not_a_stack_trace(seeded, settings_db) -> None:
    from app.main import app

    @app.get("/_probe_crash")
    def crash() -> None:
        raise RuntimeError("secret internal detail: db password hunter2")

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            page = client.get("/_probe_crash", headers={"accept": "text/html"})
            assert page.status_code == 500 and "hunter2" not in page.text and "RuntimeError" not in page.text
            assert "जवाब" in page.text  # "I'll get back to you shortly", in Hindi
            api = client.get("/_probe_crash")
            assert api.status_code == 500 and api.json() == {"error": "something went wrong"}
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/_probe_crash"]
