"""Outbound email over SMTP through the Titan Business Email mailbox on our domain. No inbound email.

- STARTTLS on SMTP_PORT (default 587); if STARTTLS fails, falls back to implicit SSL on 465.
- SMTP_SECURITY=ssl uses implicit SSL directly; SMTP_SECURITY=none is plain SMTP, for local fake servers only.
- Bank/officer recipients always CC the user (LLD §15).
- The password is never logged, never put in an exception message, and excluded from repr().
"""

import logging
import mimetypes
import smtplib
import ssl
from collections.abc import Sequence
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

from app.mouth.base import SendError

log = logging.getLogger(__name__)

OFFICIAL_AUDIENCES = {"bank", "officer"}
SSL_FALLBACK_PORT = 465


def clean_header(value: str) -> str:
    """No CR/LF in headers (blocks header injection from form input)."""
    return " ".join(value.replace("\r", " ").replace("\n", " ").split())


@dataclass
class SmtpEmail:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    from_addr: str
    security: str = "starttls"
    timeout: float = 20.0

    @classmethod
    def from_settings(cls) -> "SmtpEmail":
        from app.config import get_settings

        s = get_settings()
        return cls(
            host=s.SMTP_HOST,
            port=s.SMTP_PORT,
            user=s.SMTP_USER,
            password=s.SMTP_PASSWORD,
            from_addr=s.EMAIL_FROM or s.SMTP_USER,
            security=s.SMTP_SECURITY,
            timeout=s.SMTP_TIMEOUT_S,
        )

    def build(
        self,
        to: str,
        text: str,
        attachments: Sequence[Path],
        subject: str,
        cc: Sequence[str],
        reply_to: str | None,
    ) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.from_addr
        msg["To"] = clean_header(to)
        if cc:
            msg["Cc"] = ", ".join(clean_header(c) for c in cc)
        if reply_to:
            msg["Reply-To"] = clean_header(reply_to)
        msg["Subject"] = clean_header(subject)
        msg["Message-ID"] = make_msgid(domain=self.from_addr.rpartition("@")[2] or None)
        msg.set_content(text)
        for p in attachments:
            path = Path(p)
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            maintype, subtype = ctype.split("/", 1)
            msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
        return msg

    def _login_and_send(self, server: smtplib.SMTP, msg: EmailMessage) -> None:
        if self.user:
            server.login(self.user, self.password)
        server.send_message(msg)

    def _send_starttls(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            self._login_and_send(server, msg)

    def _send_ssl(self, msg: EmailMessage, port: int) -> None:
        with smtplib.SMTP_SSL(
            self.host, port, timeout=self.timeout, context=ssl.create_default_context()
        ) as server:
            self._login_and_send(server, msg)

    def _send_plain(self, msg: EmailMessage) -> None:
        with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
            self._login_and_send(server, msg)

    def deliver(self, msg: EmailMessage) -> None:
        try:
            if self.security == "none":
                self._send_plain(msg)
            elif self.security == "ssl" or self.port == SSL_FALLBACK_PORT:
                self._send_ssl(msg, self.port)
            else:
                try:
                    self._send_starttls(msg)
                except smtplib.SMTPAuthenticationError:
                    raise
                except (smtplib.SMTPException, OSError) as exc:
                    log.warning(
                        "smtp starttls failed (%s); falling back to SSL on %s",
                        type(exc).__name__,
                        SSL_FALLBACK_PORT,
                    )
                    self._send_ssl(msg, SSL_FALLBACK_PORT)
        except smtplib.SMTPAuthenticationError as exc:
            raise SendError(f"smtp auth failed for {self.user} (code {exc.smtp_code})") from None
        except (smtplib.SMTPException, OSError) as exc:
            raise SendError(f"smtp send failed: {type(exc).__name__}") from None

    def send(
        self,
        to: str,
        text: str,
        attachments: Sequence[Path] = (),
        *,
        subject: str = "Scheme Hunter",
        cc: Sequence[str] = (),
        audience: str = "user",
        reply_to: str | None = None,
    ) -> str:
        """Send and return the Message-ID. Channel-compatible: (to, text, attachments) positional."""
        if audience in OFFICIAL_AUDIENCES and not cc:
            raise SendError("emails to banks/officers must CC the user")
        if not self.host or not self.from_addr:
            raise SendError("SMTP_HOST / EMAIL_FROM not configured")
        msg = self.build(to, text, attachments, subject, cc, reply_to)
        self.deliver(msg)
        log.info("email sent message_id=%s cc=%d", msg["Message-ID"], len(cc))  # no addresses in logs
        return msg["Message-ID"]
