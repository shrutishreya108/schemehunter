"""Public contact form (GoDaddy checklist): GET/POST /contact, no case session needed.

POST: honeypot → silently "thanks" (nothing stored or sent); 5/hour per IP; validates; stores
messages(channel='web', direction='in'); emails CONTACT_TO (the Titan mailbox) through the SMTP adapter
with Reply-To set to the sender. If SMTP fails the message is still stored (processed=false) for the curator.
"""

import logging
import re
from typing import Annotated

from fastapi import APIRouter, Form, Request

from app.access import client_ip, rate_limit
from app.config import get_settings
from app.db import connect, jsonb
from app.mouth.base import SendError
from app.mouth.email import SmtpEmail
from app.routes.render import lang_for, render

log = logging.getLogger(__name__)
router = APIRouter()

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LIMIT_PER_HOUR = 5


def validate(name: str, email: str, message: str) -> list[str]:
    errors = []
    if not 1 <= len(name.strip()) <= 100:
        errors.append("name")
    if len(email) > 200 or not EMAIL_RE.match(email.strip()):
        errors.append("email")
    if not 1 <= len(message.strip()) <= 5000:
        errors.append("message")
    return errors


@router.get("/contact")
def contact_form(request: Request):
    return render(request, "contact.html", lang_for(request, default="en"), errors=[], form={})


@router.post("/contact")
def contact_submit(
    request: Request,
    name: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    message: Annotated[str, Form()] = "",
    website: Annotated[str, Form()] = "",
):
    lang = lang_for(request, default="en")
    if website.strip():  # honeypot: bots fill every field
        return render(request, "contact_thanks.html", lang)
    form = {"name": name, "email": email, "message": message}
    errors = validate(name, email, message)
    if errors:
        return render(request, "contact.html", lang, errors=errors, form=form, status_code=422)
    settings = get_settings()
    with connect() as conn:
        if not rate_limit(conn, f"contact:{client_ip(request)}", LIMIT_PER_HOUR, 3600):
            conn.commit()
            return render(request, "contact.html", lang, errors=["rate"], form=form, status_code=429)
        row = conn.execute(
            "insert into messages (direction, channel, from_addr, to_addr, body, attachments, processed) "
            "values ('in', 'web', %s, %s, %s, %s, false) returning id",
            (
                email.strip(),
                settings.CONTACT_TO,
                message.strip(),
                jsonb({"kind": "contact", "name": name.strip()}),
            ),
        ).fetchone()
        conn.commit()
        try:
            SmtpEmail.from_settings().send(
                settings.CONTACT_TO,
                f"From: {name.strip()} <{email.strip()}>\n\n{message.strip()}\n",
                subject=f"Contact form: {name.strip()[:60]}",
                reply_to=email.strip(),
            )
            conn.execute("update messages set processed = true where id = %s", (row["id"],))
        except SendError as exc:
            log.warning("contact email not sent (stored as %s): %s", row["id"], exc)
    return render(request, "contact_thanks.html", lang)
