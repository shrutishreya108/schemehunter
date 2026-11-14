"""Telegram channel: sendMessage / sendDocument, webhook verification, update parsing, /start binding (LLD §15).

Free, five-minute bot setup. The chat id (not a phone number) is the only identifier we keep.
"""

import hmac
import logging
import re
from collections.abc import Sequence
from pathlib import Path

import httpx

from app.config import get_settings
from app.mouth.base import SendError

log = logging.getLogger(__name__)

YES_NO = re.compile(r"^\s*(YES|NO|हाँ|हां|नहीं)\s+(\d{4})\s*$", re.IGNORECASE)
START = re.compile(r"^/start(?:@\w+)?\s+(\S+)\s*$")
MAX_TEXT = 4000  # Telegram's limit is 4096


class TelegramChannel:
    def __init__(
        self, token: str | None = None, base: str | None = None, transport: httpx.BaseTransport | None = None
    ):
        s = get_settings()
        self.token = token if token is not None else s.TELEGRAM_BOT_TOKEN
        self.base = (base or s.TELEGRAM_API_BASE).rstrip("/")
        self.transport = transport

    def __repr__(self) -> str:  # the bot token is a secret
        return "TelegramChannel(token=***)"

    def _call(self, method: str, **kwargs) -> dict:
        if not self.token:
            raise SendError("TELEGRAM_BOT_TOKEN not configured")
        try:
            with httpx.Client(transport=self.transport, timeout=15) as client:
                resp = client.post(f"{self.base}/bot{self.token}/{method}", **kwargs)
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SendError(
                f"telegram {method} failed: {type(exc).__name__}"
            ) from None  # no URL: it holds the token
        if resp.status_code != 200 or not data.get("ok"):
            raise SendError(f"telegram {method} refused (code {resp.status_code})")
        return data["result"]

    def send(self, to: str, text: str, attachments: Sequence[Path] = ()) -> str:
        result = self._call("sendMessage", json={"chat_id": to, "text": text[:MAX_TEXT]})
        for path in attachments:
            self.send_document(to, Path(path).read_bytes(), Path(path).name)
        return str(result["message_id"])

    def send_document(self, to: str, data: bytes, filename: str, caption: str = "") -> str:
        """Upload from memory, so a decrypted paper never touches disk."""
        result = self._call(
            "sendDocument",
            data={"chat_id": to, "caption": caption[:1000]},
            files={"document": (filename, data)},
        )
        return str(result["message_id"])


def verify_webhook(secret_header: str | None) -> bool:
    """Compare X-Telegram-Bot-Api-Secret-Token with TELEGRAM_WEBHOOK_SECRET (constant time; empty secret = closed)."""
    secret = get_settings().TELEGRAM_WEBHOOK_SECRET
    return bool(secret) and bool(secret_header) and hmac.compare_digest(secret_header, secret)


def parse_update(update: dict) -> dict:
    """Normalise a Telegram update to {kind, chat_id, external_id, text, token?, decision?, code?}.

    kind: start | decision | text | ignore. Anything that isn't a plain text message is ignored.
    """
    msg = update.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    if not text or chat.get("type") != "private" or "id" not in chat:
        return {"kind": "ignore"}
    base = {
        "chat_id": str(chat["id"]),
        "external_id": f"tg:{update.get('update_id')}",
        "text": text,
    }
    if m := START.match(text):
        return {**base, "kind": "start", "token": m.group(1)}
    if m := YES_NO.match(text):
        word = m.group(1).upper()
        return {
            **base,
            "kind": "decision",
            "decision": "YES" if word in ("YES", "हाँ", "हां") else "NO",
            "code": m.group(2),
        }
    return {**base, "kind": "text"}


def bind_start(conn, chat_id: str, case_link_token: str) -> str | None:
    """/start <token> → sets cases.telegram_chat_id; returns the case id, or None for a bad token."""
    from app.access import case_for_bind_token

    case_id = case_for_bind_token(case_link_token)
    if not case_id:
        return None
    row = conn.execute("select id from cases where id = %s", (case_id,)).fetchone()
    if not row:
        return None
    conn.execute(
        "update cases set telegram_chat_id = null where telegram_chat_id = %s and id <> %s",
        (chat_id, case_id),
    )
    conn.execute("update cases set telegram_chat_id = %s where id = %s", (chat_id, case_id))
    return case_id
