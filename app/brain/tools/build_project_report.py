"""build_project_report: input {scheme_id}. Does: §13.1; PDF stored as document. (LLD §8)"""

from typing import Any

from app.brain.tools.base import Ctx

SCHEMA: dict[str, Any] = {"name": "build_project_report"}  # full JSON schema in slice 9


def handle(input: dict[str, Any], ctx: Ctx) -> dict:
    raise NotImplementedError("slice 9")
