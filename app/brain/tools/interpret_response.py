"""interpret_response: input {message_id} or {text}. Does: forced schema; apply if confident (>= 0.7) and legal. (LLD §8)"""

from typing import Any

from app.brain.tools.base import Ctx

SCHEMA: dict[str, Any] = {"name": "interpret_response"}  # full JSON schema in slice 8


def handle(input: dict[str, Any], ctx: Ctx) -> dict:
    raise NotImplementedError("slice 8")
