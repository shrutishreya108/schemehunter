"""Email sign-in over HTTP (LLD signin §5, §10): codes and links are read from the captured emails."""

import re
import time
from email import message_from_bytes, policy

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.mouth.email import SmtpEmail
from app.signin import cookies

pytestmark = pytest.mark.db
CODE_RE = re.compile(r"(?:code is|कोड है):\s*(\d{6})")
LINK_RE = re.compile(r"https?://\S+/signin/link\?t=(\S+)")


@pytest.fixture
def app_client(seeded, settings_db, smtp_settings, monkeypatch):
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


def fresh_token() -> str:
    return cookies.form_token(now=time.time() - 5)  # old enough: the form was not sent instantly


def sent_bodies(server) -> list[str]:
    out = []
    for env in server.handler.messages:
        msg = message_from_bytes(env.content, policy=policy.default)
        out.append(msg.get_body().get_content())
    return out


def last_code(server) -> str:
    return CODE_RE.search(sent_bodies(server)[-1]).group(1)


def last_link_token(server) -> str:
    return LINK_RE.search(sent_bodies(server)[-1]).group(1)


def start(client, email: str, token: str | None = None):
    return client.post("/signin", data={"email": email, "form_token": token or fresh_token(), "website": ""})


def test_known_and_unknown_emails_look_identical(app_client, seeded, conn) -> None:
    conn.execute(
        "update identities set value_norm = 'meena@gmail.com' where case_id = %s", (seeded["meena"],)
    )
    conn.commit()
    a = app_client()
    r_known = start(a, "Meena@Gmail.com")
    b = app_client()
    r_unknown = start(b, "someone.new@example.in")
    assert r_known.status_code == r_unknown.status_code == 303
    assert r_known.headers["location"] == r_unknown.headers["location"] == "/signin/code"
    assert len(sent_bodies(app_client.server)) == 2  # both got one email; nothing else distinguishes them


def test_code_signs_a_known_person_in_and_opens_home(app_client, seeded, conn) -> None:
    conn.execute(
        "update identities set value_norm = 'meena@gmail.com' where case_id = %s", (seeded["meena"],)
    )
    conn.commit()
    c = app_client()
    start(c, "meena@gmail.com")
    code = last_code(app_client.server)
    r = c.post("/signin/code", data={"code": code})
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.get("/").status_code == 200 and "Meena" in c.get("/").text


def test_new_person_signs_up_with_consent_and_gets_one_case(app_client, seeded, conn) -> None:
    c = app_client()
    start(c, "fresh.person@example.in")
    assert c.get("/signin/code").status_code == 200
    assert "agree" in c.get("/signin/code").text.lower()
    code = last_code(app_client.server)
    refused = c.post("/signin/code", data={"code": code})  # no consent ticked
    assert (
        refused.status_code == 400
        and conn.execute(
            "select count(*) as n from identities where value_norm = 'fresh.person@example.in'"
        ).fetchone()["n"]
        == 0
    )
    ok = c.post("/signin/code", data={"code": code, "consent": "yes"})
    assert ok.status_code == 303 and ok.headers["location"] == "/"
    row = conn.execute(
        "select case_id from identities where value_norm = 'fresh.person@example.in'"
    ).fetchone()
    assert (
        row is not None
        and conn.execute("select consent_version from cases where id = %s", (row["case_id"],)).fetchone()[
            "consent_version"
        ]
        == "signin-v1"
    )


def test_five_wrong_codes_end_the_challenge(app_client, seeded, conn) -> None:
    c = app_client()
    start(c, "fresh.two@example.in")
    code = last_code(app_client.server)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert c.post("/signin/code", data={"code": wrong, "consent": "yes"}).status_code == 400
    r = c.post("/signin/code", data={"code": code, "consent": "yes"})  # the right one is dead now
    assert r.status_code == 400 and "Check the newest email" in r.text


def test_resend_within_cooldown_sends_nothing_new(app_client, seeded) -> None:
    c = app_client()
    start(c, "cool.down@example.in")
    before = len(sent_bodies(app_client.server))
    assert c.post("/signin/resend").status_code == 303
    assert len(sent_bodies(app_client.server)) == before  # cooldown: same page, no mail


def test_one_tap_link_is_not_spent_by_a_preview(app_client, seeded, conn) -> None:
    c = app_client()
    start(c, "link.person@example.in")
    token = last_link_token(app_client.server)
    assert c.get(f"/signin/link?t={token}").status_code == 200  # the scanner's GET
    assert c.get(f"/signin/link?t={token}").status_code == 200  # still there
    assert c.post("/signin/link", data={"t": token, "consent": "yes"}).status_code == 303
    again = c.post("/signin/link", data={"t": token, "consent": "yes"})
    assert again.status_code == 303 and again.headers["location"] == "/signin"  # used up


def test_honeypot_stores_and_sends_nothing(app_client, seeded, conn) -> None:
    c = app_client()
    before = len(sent_bodies(app_client.server))
    r = c.post(
        "/signin", data={"email": "bot@example.in", "form_token": fresh_token(), "website": "http://spam"}
    )
    assert r.status_code == 303 and len(sent_bodies(app_client.server)) == before
    assert (
        conn.execute(
            "select count(*) as n from login_challenges where email_norm = 'bot@example.in'"
        ).fetchone()["n"]
        == 0
    )


def test_form_sent_too_fast_is_refused(app_client, seeded) -> None:
    c = app_client()
    r = c.post(
        "/signin", data={"email": "fast@example.in", "form_token": cookies.form_token(), "website": ""}
    )
    assert r.status_code == 400


@pytest.mark.parametrize(
    "email", ["a,b@example.in", "a@example.in\r\nBcc: x@y.z", "not an email", "मीना@example.com"]
)
def test_bad_addresses_are_refused_before_any_mail_is_built(app_client, seeded, email) -> None:
    c = app_client()
    before = len(sent_bodies(app_client.server))
    r = start(c, email)
    assert r.status_code == 400 and len(sent_bodies(app_client.server)) == before


def test_smtp_failure_looks_like_success_and_is_recorded(app_client, seeded, conn, monkeypatch) -> None:
    def boom(self, *a, **k):
        raise OSError("relay down")

    monkeypatch.setattr(SmtpEmail, "send", boom)
    c = app_client()
    r = start(c, "smtp.down@example.in")
    assert r.status_code == 303 and r.headers["location"] == "/signin/code"
    row = conn.execute(
        "select send_error from login_challenges where email_norm = 'smtp.down@example.in'"
    ).fetchone()
    assert row["send_error"] == "OSError"


def test_anonymous_pages_go_to_sign_in_and_signed_in_visitors_go_home(app_client, seeded, conn) -> None:
    c = app_client()
    assert c.get("/chat", headers={"accept": "text/html"}).headers["location"] == "/signin"
    assert c.get("/applications", headers={"accept": "text/html"}).headers["location"] == "/signin"
    conn.execute(
        "update identities set value_norm = 'meena@gmail.com' where case_id = %s", (seeded["meena"],)
    )
    conn.commit()
    start(c, "meena@gmail.com")
    c.post("/signin/code", data={"code": last_code(app_client.server)})
    assert c.get("/signin").headers["location"] == "/"


def test_a_cookie_for_a_deleted_case_is_signed_out(app_client, seeded, conn) -> None:
    conn.execute(
        "update identities set value_norm = 'meena@gmail.com' where case_id = %s", (seeded["meena"],)
    )
    conn.commit()
    c = app_client()
    start(c, "meena@gmail.com")
    c.post("/signin/code", data={"code": last_code(app_client.server)})
    conn.execute("delete from cases where id = %s", (seeded["meena"],))
    conn.commit()
    r = c.get("/chat", headers={"accept": "text/html"})
    assert r.status_code == 303 and r.headers["location"] == "/signin"


def test_logout_needs_csrf_and_clears_the_session(app_client, seeded, conn) -> None:
    conn.execute(
        "update identities set value_norm = 'meena@gmail.com' where case_id = %s", (seeded["meena"],)
    )
    conn.commit()
    c = app_client()
    start(c, "meena@gmail.com")
    c.post("/signin/code", data={"code": last_code(app_client.server)})
    from app.access import csrf_token

    assert c.post("/logout").status_code == 403
    assert c.post("/logout", data={"csrf": csrf_token(seeded["meena"])}).headers["location"] == "/signin"
    assert c.get("/chat", headers={"accept": "text/html"}).headers["location"] == "/signin"


def test_rate_limit_keys_contain_no_email_text(app_client, seeded, conn) -> None:
    start(app_client(), "private.person@example.in")
    keys = [r["key"] for r in conn.execute("select key from rate_limits")]
    assert keys and not any("private" in k or "@" in k for k in keys)
