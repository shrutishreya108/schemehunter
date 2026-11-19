"""Messages to the user themself: the web chat only.

Messages to banks or officers never come through here: they are pending actions that a human approves.
Templated nudges and reminders to the user are written to the chat and logged, not sent to any third party.
"""

from app.db import jsonb
from app.events import log_event
from app.i18n import bilingual


def notify_user(
    conn,
    case_id: str,
    text: dict[str, str],
    kind: str,
    application_id: str | None = None,
    event_type: str = "REMINDER_SENT",
    payload: dict | None = None,
) -> None:
    """Write the message to the web chat and log one event."""
    case = conn.execute("select language from cases where id = %s", (case_id,)).fetchone()
    lang = (case or {}).get("language") or "en"
    body = text.get(lang) or text["en"]
    conn.execute(
        "insert into messages (case_id, direction, channel, body, attachments, processed) "
        "values (%s, 'out', 'web', %s, %s, true)",
        (case_id, body, jsonb({"kind": kind})),
    )
    log_event(
        conn,
        case_id,
        "clock" if event_type == "REMINDER_SENT" else "system",
        event_type,
        bilingual(text["en"], text["hi"]),
        payload={"kind": kind, **(payload or {})},
        application_id=application_id,
    )
