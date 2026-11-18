"""What an APPROVED pending action actually does. Called only from guard.actions.execute()."""

import logging

from app.db import jsonb
from app.events import log_event
from app.i18n import bilingual
from app.mouth.base import SendError

log = logging.getLogger(__name__)


def run_action(conn, action: dict) -> None:
    kind = action["kind"]
    if kind == "send_email":
        return send_email(conn, action)
    if kind == "send_chat":
        return send_chat(conn, action)
    if kind == "instruct_submit":
        return instruct_submit(conn, action)
    raise SendError(f"{kind} is not enabled")  # register_domain: flag-gated stretch, not built


def _case(conn, case_id) -> dict:
    return conn.execute(
        "select language, email, display_name from cases where id = %s", (case_id,)
    ).fetchone()


def _record(conn, action: dict, channel: str, external_id: str | None, to_addr: str, body: str) -> None:
    conn.execute(
        "insert into messages (case_id, direction, channel, external_id, to_addr, body, processed) "
        "values (%s, 'out', %s, %s, %s, %s, true) on conflict (external_id) do nothing",
        (action["case_id"], channel, external_id, to_addr, body),
    )
    log_event(
        conn,
        action["case_id"],
        "system",
        "MESSAGE_SENT",
        bilingual("Sent the approved message", "मंज़ूर किया हुआ संदेश भेज दिया"),
        payload={"action_id": str(action["id"]), "channel": channel},  # ids only: no addresses, no text
        application_id=action["application_id"],
    )


def send_email(conn, action: dict) -> None:
    from app.mouth.base import get_channel

    p = action["payload"]
    case = _case(conn, action["case_id"])
    cc = [case["email"]] if case and case["email"] else []
    audience = p.get("audience", "officer")
    message_id = get_channel("email").send(
        p["to"], p["body"], subject=p.get("subject") or "Scheme Hunter", cc=cc, audience=audience
    )
    _record(conn, action, "email", message_id, p["to"], p["body"])


def send_chat(conn, action: dict) -> None:
    from app.mouth.outbox import notify_user

    p = action["payload"]
    body = p["body"]
    notify_user(
        conn,
        str(action["case_id"]),
        {"en": body, "hi": body},
        kind="approved_message",
        application_id=action["application_id"],
        event_type="MESSAGE_SENT",
        payload={"action_id": str(action["id"])},
    )


def instruct_submit(conn, action: dict) -> None:
    """The user pressed "I'll do it": send the step list (and the pre-filled pack, if linked) to them."""
    from app.forms.prefill import steps_for
    from app.mouth.outbox import notify_user

    p = action["payload"]
    steps_en = steps_for(p.get("scheme_id", ""), "en")
    steps_hi = steps_for(p.get("scheme_id", ""), "hi")
    name = str(p.get("scheme_id", "")).upper()
    en = f"Steps to submit your {name} application:\n" + "\n".join(
        f"{i}. {s}" for i, s in enumerate(steps_en, 1)
    )
    hi = f"{name} आवेदन जमा करने के स्टेप:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps_hi, 1))
    en += "\nWhen it's done, send me the reference number."
    hi += "\nहो जाए तो मुझे संदर्भ संख्या भेजिए।"
    notify_user(
        conn,
        str(action["case_id"]),
        {"en": en, "hi": hi},
        kind="instruct_submit",
        application_id=action["application_id"],
        event_type="MESSAGE_SENT",
        payload={"action_id": str(action["id"])},
    )
    conn.execute(
        "update pending_actions set payload = payload || %s where id = %s",
        (jsonb({"steps_sent": True}), action["id"]),
    )
