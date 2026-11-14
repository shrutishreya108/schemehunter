"""schedule: input {type, days_from_now, application_id?}. Does: insert job (demo clock applied). (LLD §8)"""

from typing import Any

from app.brain.tools.base import Ctx

SCHEMA: dict[str, Any] = {"name": "schedule"}  # full JSON schema in slice 6


def handle(input: dict[str, Any], ctx: Ctx) -> dict:
    raise NotImplementedError("slice 6")
