"""update_profile {key, value}: upsert profile_answers (source=typed). (LLD §8)"""

from typing import Any

from pydantic import BaseModel

from app.brain.tools.base import Ctx, ToolError, tool_schema
from app.brain.tools.facts_vocab import VocabError, coerce
from app.db import jsonb
from app.events import log_event
from app.i18n import bilingual


class Input(BaseModel):
    key: str
    value: str | int | float | bool


SCHEMA = tool_schema(
    "update_profile",
    "Save one answer the user gave. Money in whole rupees (3 lakh = 300000). Dates as YYYY-MM-DD.",
    Input,
)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    try:
        value = coerce(data.key, data.value)
    except VocabError as exc:
        raise ToolError(str(exc)) from None
    ctx.conn.execute(
        "insert into profile_answers (case_id, key, value, source) values (%s, %s, %s, 'typed') "
        "on conflict (case_id, key) do update set value = excluded.value, source = 'typed', updated_at = now()",
        (ctx.case_id, data.key, jsonb(value)),
    )
    log_event(
        ctx.conn,
        ctx.case_id,
        "agent",
        "PROFILE_UPDATED",
        bilingual(f"Saved answer: {data.key}", f"जवाब सहेजा: {data.key}"),
        payload={"key": data.key},
    )
    return {"ok": True, "key": data.key, "value": value}
