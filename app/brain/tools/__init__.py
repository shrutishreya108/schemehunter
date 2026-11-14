"""TOOLS (JSON schemas sent to Claude) + dispatch(name, input, ctx).

dispatch never raises into the loop: unknown tools, invalid input and tool failures all come back as
{"error": "..."} so the model can adapt. Each call runs inside a savepoint, so a failing tool leaves no
half-written rows and doesn't poison the run's transaction.
"""

import logging
from types import ModuleType
from typing import Any

from app.brain.tools import (
    ask_user,
    build_project_report,
    check_document,
    create_application,
    draft_message,
    explain_match,
    finish_run,
    interpret_response,
    list_required_documents,
    match_schemes,
    prefill_form,
    request_submit,
    schedule,
    transition_application,
    update_profile,
)
from app.brain.tools.base import Ctx, ToolError, validate

log = logging.getLogger(__name__)

# All 15 tools from LLD §8.
ENABLED: list[ModuleType] = [
    update_profile,
    match_schemes,
    explain_match,
    ask_user,
    list_required_documents,
    check_document,
    build_project_report,
    prefill_form,
    create_application,
    transition_application,
    interpret_response,
    draft_message,
    request_submit,
    schedule,
    finish_run,
]
REGISTRY: dict[str, ModuleType] = {m.SCHEMA["name"]: m for m in ENABLED}
TOOLS: list[dict] = [m.SCHEMA for m in ENABLED]

TOOL_NAMES = [
    "update_profile",
    "match_schemes",
    "explain_match",
    "ask_user",
    "list_required_documents",
    "check_document",
    "build_project_report",
    "prefill_form",
    "create_application",
    "transition_application",
    "interpret_response",
    "draft_message",
    "request_submit",
    "schedule",
    "finish_run",
]


def dispatch(name: str, input: dict[str, Any], ctx: Ctx) -> dict:
    module = REGISTRY.get(name)
    if module is None:
        return {
            "error": f"unknown tool {name!r}. Available: {', '.join(REGISTRY)}. You cannot send messages; "
            "a human approves anything that leaves the system. Reply via finish_run."
        }
    try:
        data = validate(module.Input, input or {})
        with ctx.conn.transaction():
            return module.handle(data, ctx)
    except ToolError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        log.exception("tool %s failed", name)
        return {"error": f"{name} failed ({type(exc).__name__}); try again or finish_run"}
