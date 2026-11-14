"""transition_application: input {application_id, to_state, reason}. Does: §9 transition(). (LLD §8)"""

from typing import Any

from app.brain.tools.base import Ctx

SCHEMA: dict[str, Any] = {"name": "transition_application"}  # full JSON schema in slice 4


def handle(input: dict[str, Any], ctx: Ctx) -> dict:
    raise NotImplementedError("slice 4")
