"""draft_message: input {purpose, audience, application_id, points[]}. Does: forced schema → scanner → pending action. (LLD §8)"""

from typing import Any

from app.brain.tools.base import Ctx

SCHEMA: dict[str, Any] = {"name": "draft_message"}  # full JSON schema in slice 7


def handle(input: dict[str, Any], ctx: Ctx) -> dict:
    raise NotImplementedError("slice 7")
