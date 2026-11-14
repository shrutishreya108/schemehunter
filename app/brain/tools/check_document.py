"""check_document: input {document_id}. Does: §12 pipeline. (LLD §8)"""

from typing import Any

from app.brain.tools.base import Ctx

SCHEMA: dict[str, Any] = {"name": "check_document"}  # full JSON schema in slice 5


def handle(input: dict[str, Any], ctx: Ctx) -> dict:
    raise NotImplementedError("slice 5")
