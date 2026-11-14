"""list_required_documents: input {scheme_id}. Does: resolve conditionals against facts. (LLD §8)"""

from typing import Any

from app.brain.tools.base import Ctx

SCHEMA: dict[str, Any] = {"name": "list_required_documents"}  # full JSON schema in slice 5


def handle(input: dict[str, Any], ctx: Ctx) -> dict:
    raise NotImplementedError("slice 5")
