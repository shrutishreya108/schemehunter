"""Public contact form → Titan mailbox over SMTP (fake local server). Works without a case session."""

from email import message_from_bytes, policy

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.db

FORM = {
    "name": "Asha Rao",
    "email": "asha@example.com",
    "message": "Can you help my tailoring unit?",
    "website": "",
}


@pytest.fixture
def client(settings_db, smtp_settings):
    from app.db import connect
    from app.main import app

    with connect(settings_db) as c:
        c.execute("truncate messages, rate_limits")
    with TestClient(app) as c:
        yield c


def test_get_form_without_session(client) -> None:
    r = client.get("/contact")
    assert r.status_code == 200 and 'name="website"' in r.text


def test_submit_sends_email_and_stores_message(client, smtp_settings, conn) -> None:
    r = client.post("/contact", data=FORM)
    assert r.status_code == 200 and "Thank you" in r.text
    (envelope,) = smtp_settings.handler.messages
    msg = message_from_bytes(envelope.content, policy=policy.default)
    assert envelope.rcpt_tos == ["hello@example.in"] and msg["Reply-To"] == "asha@example.com"
    assert msg["Subject"] == "Contact form: Asha Rao" and "tailoring unit" in msg.get_content()
    row = conn.execute(
        "select * from messages where channel = 'web' and direction = 'in' and case_id is null"
    ).fetchone()
    assert row["from_addr"] == "asha@example.com" and row["processed"] is True


def test_honeypot_is_silently_dropped(client, smtp_settings, conn) -> None:
    r = client.post("/contact", data={**FORM, "website": "http://spam.example"})
    assert r.status_code == 200 and "Thank you" in r.text
    assert smtp_settings.handler.messages == []
    assert not conn.execute("select 1 from messages where case_id is null").fetchone()


def test_validation(client, smtp_settings) -> None:
    r = client.post("/contact", data={**FORM, "email": "not-an-email"})
    assert r.status_code == 422 and "valid email" in r.text and smtp_settings.handler.messages == []


def test_rate_limit_five_per_hour_per_ip(client, smtp_settings) -> None:
    headers = {"X-Forwarded-For": "203.0.113.7"}
    codes = [client.post("/contact", data=FORM, headers=headers).status_code for _ in range(6)]
    assert codes == [200, 200, 200, 200, 200, 429]
    assert client.post("/contact", data=FORM, headers={"X-Forwarded-For": "203.0.113.8"}).status_code == 200


def test_smtp_down_still_stores(client, conn, monkeypatch) -> None:
    monkeypatch.setenv("SMTP_PORT", "1")
    from app.config import get_settings

    get_settings.cache_clear()
    r = client.post("/contact", data=FORM)
    assert r.status_code == 200
    assert (
        conn.execute("select processed from messages where case_id is null").fetchone()["processed"] is False
    )
