"""ask_user {question_hi, question_en, expected_fact}: show one question in chat. (LLD §8)

The question is scanned like any outbound text. expected_fact is stored on the message so the no-JS
/answers fallback knows which profile key the answer fills.
"""

from typing import Any

from pydantic import BaseModel

from app.brain.tools.base import Ctx, ToolError, scan_text, tool_schema
from app.brain.tools.facts_vocab import ALLOWED
from app.db import jsonb


class Input(BaseModel):
    question_hi: str
    question_en: str
    expected_fact: str


SCHEMA = tool_schema(
    "ask_user",
    "Ask the user ONE question, in both Hindi and English, for one profile fact (usually next_fact_to_ask).",
    Input,
)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    if data.expected_fact not in ALLOWED:
        raise ToolError(f"expected_fact must be a profile key: {', '.join(sorted(ALLOWED))}")
    if ctx.asked:
        raise ToolError("one question per run: you already asked; call finish_run")
    check = scan_text(ctx, data.question_hi, data.question_en)
    if not check["ok"]:
        raise ToolError(f"question blocked by guard: {check}; rewrite it")
    body = data.question_hi if ctx.language == "hi" else data.question_en
    ctx.conn.execute(
        "insert into messages (case_id, direction, channel, body, attachments, processed) "
        "values (%s, 'out', 'web', %s, %s, true)",
        (
            ctx.case_id,
            body,
            jsonb(
                {
                    "kind": "question",
                    "expected_fact": data.expected_fact,
                    "hi": data.question_hi,
                    "en": data.question_en,
                }
            ),
        ),
    )
    ctx.asked.append(data.model_dump())
    return {"ok": True, "shown_to_user": True}
