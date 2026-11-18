"""Channel protocol and get_channel(name) (LLD §15)."""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol


class SendError(RuntimeError):
    """Provider refused or failed; the Guard executor retries up to 3 times, then marks FAILED."""


class Channel(Protocol):
    def send(self, to: str, text: str, attachments: Sequence[Path] = ()) -> str:
        """Send and return the provider message id."""
        ...


def get_channel(name: str) -> Channel:
    if name == "email":
        from app.mouth.email import SmtpEmail

        return SmtpEmail.from_settings()
    if name == "whatsapp":
        from app.mouth.whatsapp import WhatsAppChannel

        return WhatsAppChannel()
    raise ValueError(f"unknown channel {name!r}")
