"""SMTP adapter against a real local fake SMTP server (aiosmtpd), plus STARTTLS→SSL fallback logic."""

import logging
import smtplib
from email import message_from_bytes as _parse
from email import policy
from typing import ClassVar


def message_from_bytes(raw: bytes):
    return _parse(raw, policy=policy.default)


import pytest

from app.mouth.base import SendError
from app.mouth.email import SmtpEmail


def adapter(server, **kw) -> SmtpEmail:
    return SmtpEmail(
        host=server.hostname,
        port=server.port,
        user="",
        password="s3cret-pw",
        from_addr="hello@example.in",
        security="none",
        **kw,
    )


def test_sends_to_fake_server_with_cc_reply_to_and_attachment(smtp_server, tmp_path) -> None:
    pdf = tmp_path / "pack.pdf"
    pdf.write_bytes(b"%PDF-1.4 demo")
    msg_id = adapter(smtp_server).send(
        "bank@example.com",
        "Hello bank",
        [pdf],
        subject="PMEGP/KA/2026/04471",
        cc=["meena@example.com"],
        audience="bank",
        reply_to="meena@example.com",
    )
    (envelope,) = smtp_server.handler.messages
    msg = message_from_bytes(envelope.content)
    assert sorted(envelope.rcpt_tos) == ["bank@example.com", "meena@example.com"]
    assert msg["Subject"] == "PMEGP/KA/2026/04471" and msg["Cc"] == "meena@example.com"
    assert msg["Reply-To"] == "meena@example.com" and msg["Message-ID"] == msg_id
    assert [p.get_filename() for p in msg.iter_attachments()] == ["pack.pdf"]


def test_header_injection_is_flattened(smtp_server) -> None:
    adapter(smtp_server).send("to@example.com", "x", subject="Hi\r\nBcc: evil@example.com")
    msg = message_from_bytes(smtp_server.handler.messages[-1].content)
    assert msg["Bcc"] is None and msg["Subject"] == "Hi Bcc: evil@example.com"


def test_bank_email_must_cc_user(smtp_server) -> None:
    with pytest.raises(SendError, match="CC"):
        adapter(smtp_server).send("bank@x.com", "hi", audience="officer")


def test_password_never_in_repr_or_errors(caplog) -> None:
    mail = SmtpEmail(
        host="127.0.0.1",
        port=1,
        user="hello@example.in",
        password="s3cret-pw",
        from_addr="hello@example.in",
        security="none",
        timeout=1,
    )
    assert "s3cret-pw" not in repr(mail)
    with caplog.at_level(logging.DEBUG), pytest.raises(SendError) as exc:
        mail.send("to@example.com", "x")
    assert "s3cret-pw" not in str(exc.value) and "s3cret-pw" not in caplog.text


class FakeSMTP:
    """Stands in for smtplib.SMTP / SMTP_SSL to check which path was taken."""

    calls: ClassVar[list] = []
    fail_starttls = False

    def __init__(self, host, port, timeout=None, context=None):
        self.port = port
        FakeSMTP.calls.append((type(self).__name__, port))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        if FakeSMTP.fail_starttls:
            raise smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")

    def login(self, user, password):
        FakeSMTP.calls.append(("login", user))

    def send_message(self, msg):
        FakeSMTP.calls.append(("sent", self.port))


class FakeSSL(FakeSMTP):
    pass


@pytest.fixture
def fake_smtplib(monkeypatch):
    FakeSMTP.calls, FakeSMTP.fail_starttls = [], False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSSL)
    return FakeSMTP


def titan(**kw) -> SmtpEmail:
    return SmtpEmail(
        host="smtpout.secureserver.net",
        user="hello@example.in",
        password="pw",
        from_addr="hello@example.in",
        **kw,
    )


def test_starttls_default(fake_smtplib) -> None:
    titan(port=587).send("a@b.com", "x")
    assert fake_smtplib.calls == [("FakeSMTP", 587), ("login", "hello@example.in"), ("sent", 587)]


def test_starttls_failure_falls_back_to_ssl_465(fake_smtplib) -> None:
    fake_smtplib.fail_starttls = True
    titan(port=587).send("a@b.com", "x")
    assert fake_smtplib.calls[-3:] == [("FakeSSL", 465), ("login", "hello@example.in"), ("sent", 465)]


def test_port_465_uses_ssl_directly(fake_smtplib) -> None:
    titan(port=465).send("a@b.com", "x")
    assert fake_smtplib.calls[0] == ("FakeSSL", 465)


def test_from_settings_uses_titan_default_host(monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("SMTP_USER", "hello@example.in")
    get_settings.cache_clear()
    try:
        mail = SmtpEmail.from_settings()
        assert (mail.host, mail.port, mail.from_addr) == ("smtpout.secureserver.net", 587, "hello@example.in")
    finally:
        get_settings.cache_clear()
