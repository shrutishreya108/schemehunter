"""request_submit: input {application_id, pack_document_id}. Does: pending action instruct_submit; state → AWAITING_HUMAN_SUBMIT. (LLD §8)"""

from typing import Any

from app.brain.tools.base import Ctx

SCHEMA: dict[str, Any] = {"name": "request_submit"}  # full JSON schema in slice 9


def handle(input: dict[str, Any], ctx: Ctx) -> dict:
    raise NotImplementedError("slice 9")
