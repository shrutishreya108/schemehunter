"""finish_run {user_message, internal_note, next_check_in_days}: end the run. (LLD §8)

user_message is scanned. On a guard failure the model gets up to MAX_REWRITES chances; after that the
run ends with a safe fallback message and the blocked text is logged for the curator.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.brain.tools.base import MAX_REWRITES, Ctx, scan_text, tool_schema
from app.db import jsonb
from app.events import log_event
from app.i18n import bilingual

FALLBACK = {"hi": "मैं जाँच करके आपको जल्दी बताऊँगी।", "en": "Let me check and get back to you shortly."}


class Input(BaseModel):
    user_message: str = ""
    internal_note: str = ""
    next_check_in_days: int | None = Field(default=None, ge=0, le=60)


SCHEMA = tool_schema(
    "finish_run",
    "End this run. user_message is shown to the user (empty if ask_user already asked). Always call this last.",
    Input,
)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    message = data.user_message.strip()
    if message:
        check = scan_text(ctx, message)
        if not check["ok"]:
            ctx.rewrites += 1
            if ctx.rewrites <= MAX_REWRITES:
                return {
                    "error": "user_message blocked by guard; rewrite without promises and only Rule Book "
                    f"schemes. hits={check['hits']} unknown_schemes={check['unknown_schemes']}"
                }
            log_event(
                ctx.conn,
                ctx.case_id,
                "system",
                "TOOL_CALLED",
                bilingual("Message blocked by guard; safe reply sent", "संदेश रोका गया; सुरक्षित जवाब भेजा"),
                payload={"tool": "finish_run", "ok": False, "guard": check},
            )
            message = FALLBACK.get(ctx.language, FALLBACK["en"])
        if ctx.cited:  # after the promise check: the citation is code-written and always kept
            lines = [f"Source: {c['title']}, version {c['version']}, page {c['page']}." for c in ctx.cited]
            message = message + "\n" + "\n".join(dict.fromkeys(lines))
        ctx.conn.execute(
            "insert into messages (case_id, direction, channel, body, attachments, processed) "
            "values (%s, 'out', 'web', %s, %s, true)",
            (ctx.case_id, message, jsonb({"kind": "reply"})),
        )
    ctx.finished = True
    ctx.user_message = message
    return {"ok": True, "next_check_in_days": data.next_check_in_days}
