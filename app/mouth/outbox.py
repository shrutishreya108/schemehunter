"""Messages to the user themself (no third party): chat reply + Telegram if linked.

Messages to banks or officers never come through here: they are pending actions that a human approves.
Templated nudges and reminders to the user are sent directly, because the user is the only recipient.
"""

import logging

from app.db import jsonb
from app.events import log_event
from app.i18n import bilingual

log = logging.getLogger(__name__)


def notify_user(
    conn,
    case_id: str,
    text: dict[str, str],
    kind: str,
    application_id: str | None = None,
    event_type: str = "REMINDER_SENT",
    payload: dict | None = None,
) -> None:
    """Write the message to the web chat, send it to Telegram if linked (best effort), log one event."""
    case = conn.execute("select language, telegram_chat_id from cases where id = %s", (case_id,)).fetchone()
    lang = (case or {}).get("language") or "hi"
    body = text.get(lang) or text["en"]
    conn.execute(
        "insert into messages (case_id, direction, channel, body, attachments, processed) "
        "values (%s, 'out', 'web', %s, %s, true)",
        (case_id, body, jsonb({"kind": kind})),
    )
    chat_id = (case or {}).get("telegram_chat_id")
    if chat_id:
        try:
            from app.mouth.telegram import TelegramChannel

            ext = TelegramChannel().send(chat_id, body)
            conn.execute(
                "insert into messages (case_id, direction, channel, external_id, to_addr, body, processed) "
                "values (%s, 'out', 'telegram', %s, %s, %s, true) on conflict (external_id) do nothing",
                (case_id, f"tg-out:{chat_id}:{ext}", chat_id, body),
            )
        except Exception as exc:  # noqa: BLE001 - the chat copy is already saved; Telegram is a bonus
            log.warning("telegram nudge failed: %s", type(exc).__name__)
    log_event(
        conn,
        case_id,
        "clock" if event_type == "REMINDER_SENT" else "system",
        event_type,
        bilingual(text["en"], text["hi"]),
        payload={"kind": kind, **(payload or {})},
        application_id=application_id,
    )
