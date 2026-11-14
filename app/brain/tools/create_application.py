"""create_application: input {scheme_id}. Does: row DISCOVERED → ELIGIBLE if FIT. (LLD §8)"""

from typing import Any

from app.brain.tools.base import Ctx

SCHEMA: dict[str, Any] = {"name": "create_application"}  # full JSON schema in slice 4


def handle(input: dict[str, Any], ctx: Ctx) -> dict:
    raise NotImplementedError("slice 4")
