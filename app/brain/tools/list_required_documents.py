"""list_required_documents {scheme_id}: the papers this person needs, conditionals resolved against facts."""

from typing import Any

from pydantic import BaseModel

from app.brain.tools.base import Ctx, tool_schema
from app.docs.requirements import document_statuses


class Input(BaseModel):
    scheme_id: str


SCHEMA = tool_schema(
    "list_required_documents",
    "List the papers needed for one scheme and whether each is uploaded and OK. Conditional papers "
    "(caste certificate, education certificate) are included only if this person needs them.",
    Input,
)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    scheme = ctx.scheme(data.scheme_id)
    return {"scheme_id": data.scheme_id, "documents": document_statuses(ctx.conn, ctx.case_id, scheme)}
