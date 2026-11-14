"""prefill_form: input {application_id}. Does: §13.2. (LLD §8)"""

from typing import Any

from app.brain.tools.base import Ctx

SCHEMA: dict[str, Any] = {"name": "prefill_form"}  # full JSON schema in slice 9


def handle(input: dict[str, Any], ctx: Ctx) -> dict:
    raise NotImplementedError("slice 9")
