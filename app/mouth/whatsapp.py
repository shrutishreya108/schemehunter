"""WhatsApp Cloud API adapter: stubbed for the demo build (LLD v1.1 §0)."""

from collections.abc import Sequence
from pathlib import Path


class WhatsAppChannel:
    def send(self, to: str, text: str, attachments: Sequence[Path] = ()) -> str:
        raise NotImplementedError("WhatsApp is stubbed in the demo build")
