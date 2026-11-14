"""check_document {document_id}: run the §12 check on an uploaded paper. Vision only for aadhaar/passbook."""

from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.brain.tools.base import Ctx, ToolError, tool_schema
from app.docs.pipeline import check_stored_document


class Input(BaseModel):
    document_id: str


SCHEMA = tool_schema(
    "check_document",
    "Check an uploaded paper: type, readability, masked fields, name match. Returns OK, RETAKE or PROBLEM "
    "with a one-line reason. Never returns a full ID number.",
    Input,
)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    try:
        UUID(data.document_id)
    except ValueError:
        raise ToolError("document_id must be a document id from the case file") from None
    try:
        result = check_stored_document(ctx.conn, ctx.case_id, data.document_id, ctx.meter, ctx.client)
    except LookupError:
        raise ToolError("no such document for this case") from None
    return {
        "doc_type_detected": result["doc_type_detected"],
        "status": result["status"],
        "issues": result["issues"],
        "reason": result["reason"].get(ctx.language) or result["reason"]["en"],
        "fields_masked": result["fields_masked"],
    }
