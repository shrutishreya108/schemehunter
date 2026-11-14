"""Telegram channel: sendMessage / sendDocument, webhook verification, /start <token> binding. Slice 7."""

from collections.abc import Sequence
from pathlib import Path


class TelegramChannel:
    def send(self, to: str, text: str, attachments: Sequence[Path] = ()) -> str:
        raise NotImplementedError("slice 7")

    def send_document(self, to: str, path: Path, caption: str = "") -> str:
        raise NotImplementedError("slice 7")


def verify_webhook(secret_header: str | None) -> bool:
    """Compare X-Telegram-Bot-Api-Secret-Token with TELEGRAM_WEBHOOK_SECRET."""
    raise NotImplementedError("slice 7")


def parse_update(update: dict) -> dict:
    raise NotImplementedError("slice 7")


def bind_start(chat_id: str, case_link_token: str) -> str:
    """/start <token> → sets cases.telegram_chat_id; returns case id."""
    raise NotImplementedError("slice 7")
