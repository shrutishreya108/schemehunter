"""Shared tool plumbing: Ctx, ToolError, pydantic validation, schema helper, outbound text scan."""

from dataclasses import dataclass, field
from typing import Any

import psycopg
from pydantic import BaseModel, ValidationError

from app.costs import UsageMeter
from app.guard.scanner import regex_scheme_mentions, scan

MAX_REWRITES = 2


@dataclass
class Ctx:
    case_id: str
    conn: psycopg.Connection
    schemes: list[dict]
    language: str = "hi"
    state_code: str | None = None
    run_id: str | None = None
    meter: UsageMeter = field(default_factory=UsageMeter)
    client: Any = None  # ClaudeClient override (tests); None → claude.get_client()
    finished: bool = False
    user_message: str = ""
    rewrites: int = 0
    asked: list[dict] = field(default_factory=list)

    def scheme(self, scheme_id: str) -> dict:
        for s in self.schemes:
            if s["id"] == scheme_id:
                return s
        raise ToolError(f"unknown scheme_id {scheme_id!r}; valid: {', '.join(s['id'] for s in self.schemes)}")


class ToolError(Exception):
    """Returned to Claude as {"error": message} so it can adapt."""


def validate[M: BaseModel](model: type[M], data: dict[str, Any]) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
        raise ToolError(f"invalid input: {details}") from None


def tool_schema(name: str, description: str, model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    return {"name": name, "description": description, "input_schema": schema}


def scan_text(ctx: Ctx, *texts: str) -> dict:
    """Guard scanner over outbound text: regex extractor, plus MODEL_CHEAP when SCAN_WITH_MODEL is on."""
    from app.config import get_settings

    body = "\n".join(t for t in texts if t)
    if not body.strip():
        return {"ok": True, "hits": [], "unknown_schemes": []}
    extractor = regex_scheme_mentions
    if get_settings().SCAN_WITH_MODEL:
        from app.brain.claude import cheap_scheme_extractor

        model_extract = cheap_scheme_extractor(ctx.meter, ctx.client)

        def extractor(text: str) -> list[str]:
            return list(dict.fromkeys(regex_scheme_mentions(text) + model_extract(text)))

    return scan(body, ctx.schemes, extractor)
