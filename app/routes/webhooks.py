"""POST /webhooks/telegram: verify → dedupe → /start binding | YES/NO code → Guard | anything else → the Brain (LLD §15)."""

import logging

from fastapi import APIRouter, Header, HTTPException, Request

from app.access import client_ip, rate_limit
from app.clock.schedule import schedule
from app.db import connect
from app.events import log_event
from app.guard import actions
from app.i18n import bilingual
from app.mouth import telegram
from app.mouth.outbox import notify_user

log = logging.getLogger(__name__)
router = APIRouter()

OPEN_YOUR_LINK = {
    "en": "Please open your personal Scheme Hunter link first, then try again.",
    "hi": "कृपया पहले अपना निजी Scheme Hunter लिंक खोलिए, फिर कोशिश कीजिए।",
}


def reply_raw(chat_id: str, text: str) -> None:
    """Answer a chat we have no case for. Best effort; never raises into the webhook."""
    try:
        telegram.TelegramChannel().send(chat_id, text)
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram reply failed: %s", type(exc).__name__)


@router.post("/webhooks/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if not telegram.verify_webhook(x_telegram_bot_api_secret_token):
        raise HTTPException(403, "bad secret")
    update = await request.json()
    msg = telegram.parse_update(update)
    if msg["kind"] == "ignore":
        return {"ok": True}
    with connect() as conn:
        if not rate_limit(conn, f"webhook:{client_ip(request)}", 60, 60):
            raise HTTPException(429, "slow down")
        # Duplicate delivery of the same update is a no-op (messages.external_id is unique).
        row = conn.execute(
            "insert into messages (direction, channel, external_id, from_addr, body, processed) "
            "values ('in', 'telegram', %s, %s, %s, false) on conflict (external_id) do nothing returning id",
            (msg["external_id"], msg["chat_id"], msg["text"]),
        ).fetchone()
        if row is None:
            return {"ok": True, "duplicate": True}
        message_id = str(row["id"])

        if msg["kind"] == "start":
            case_id = telegram.bind_start(conn, msg["chat_id"], msg["token"])
            if case_id:
                conn.execute(
                    "update messages set case_id = %s, processed = true where id = %s", (case_id, message_id)
                )
                notify_user(
                    conn,
                    case_id,
                    {
                        "en": "Connected. I'll send updates and questions here.",
                        "hi": "जुड़ गया। अपडेट और सवाल मैं यहीं भेजूँगी।",
                    },
                    kind="telegram_linked",
                    event_type="MESSAGE_SENT",
                )
            else:
                reply_raw(msg["chat_id"], OPEN_YOUR_LINK["en"] + "\n" + OPEN_YOUR_LINK["hi"])
            return {"ok": True}

        case = conn.execute(
            "select id, language from cases where telegram_chat_id = %s", (msg["chat_id"],)
        ).fetchone()
        if not case:
            conn.execute("update messages set processed = true where id = %s", (message_id,))
            reply_raw(msg["chat_id"], OPEN_YOUR_LINK["en"] + "\n" + OPEN_YOUR_LINK["hi"])
            return {"ok": True}
        case_id = str(case["id"])
        conn.execute("update messages set case_id = %s where id = %s", (case_id, message_id))
        log_event(
            conn,
            case_id,
            "user",
            "MESSAGE_RECEIVED",
            bilingual("You sent a message on Telegram", "आपने Telegram पर संदेश भेजा"),
            payload={"message_id": message_id},
        )

        if msg["kind"] == "decision":
            action = actions.find_by_code(conn, case_id, msg["code"])
            if action is None:
                # Late or repeated YES/NO for something already decided: say so, never act twice.
                conn.execute("update messages set processed = true where id = %s", (message_id,))
                lang = case["language"] or "hi"
                text = {
                    "en": "That request isn't waiting any more.",
                    "hi": "यह अनुरोध अब बाकी नहीं है।",
                }
                notify_user(conn, case_id, text, kind="decision_stale", event_type="MESSAGE_SENT")
                return {"ok": True, "stale": True, "lang": lang}
            if msg["decision"] == "YES":
                actions.approve(conn, str(action["id"]), "telegram", case_id=case_id)
            else:
                actions.reject(conn, str(action["id"]), "telegram", case_id=case_id)
            conn.execute("update messages set processed = true where id = %s", (message_id,))
            return {"ok": True}

        # Anything else (forwarded SMS, "done, number is 4471"): a Brain run reads it.
        schedule(
            conn,
            "brain_run",
            case_id,
            payload={"task": f"interpret inbound message {message_id}", "trigger": "telegram"},
        )
    return {"ok": True}
