"""prefill_form {application_id}: profile → the scheme's form fields → a pre-filled pack PDF plus step list. (LLD §13.2)"""

from typing import Any

from pydantic import BaseModel

from app.brain.tools.base import Ctx, ToolError, tool_schema
from app.brain.tools.build_project_report import load_artifact, percentages
from app.docs.generated import store_generated
from app.docs.requirements import load_facts
from app.finance.project_report import compute, inputs_from
from app.forms.prefill import apply_mapping, load_mapping, missing_fields, steps_for
from app.i18n import bilingual
from app.pdf.render import PdfUnavailable, render
from app.tracker.store import load_application


class Input(BaseModel):
    application_id: str


SCHEMA = tool_schema(
    "prefill_form",
    "Build the pre-filled copy-paste pack and portal steps for an application. The official form is not edited; "
    "fields we can't fill are listed in missing_fields.",
    Input,
)


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    app = load_application(ctx.conn, data.application_id, ctx.case_id)
    if not app:
        raise ToolError("no such application for this case")
    scheme = ctx.scheme(app.scheme_id)
    mapping = load_mapping(app.scheme_id)
    if not mapping:
        raise ToolError(f"no form mapping for {app.scheme_id} yet; give the user the portal steps instead")
    facts = load_facts(ctx.conn, ctx.case_id)
    costs = load_artifact(ctx.conn, ctx.case_id, "cost_inputs")
    if costs:  # the report's computed total beats the rough project_cost answer
        try:
            pcts = percentages(ctx.conn, ctx.case_id, app.scheme_id)
            facts["total_cost"] = compute(inputs_from(costs, *pcts)).total_cost
        except ToolError:
            facts.setdefault("total_cost", facts.get("project_cost"))
    else:
        facts["total_cost"] = facts.get("project_cost")
    fields = apply_mapping(mapping, facts)
    steps = mapping.get("steps") or []
    try:
        pdf, fell_back = render(
            "prefill_pack",
            {
                "fields": fields,
                "steps": steps,
                "scheme_name": scheme["name"],
                "portal_url": scheme.get("portal_url", ""),
            },
            ctx.language,
        )
    except PdfUnavailable:
        raise ToolError("the PDF engine isn't available on this server") from None
    doc_id = store_generated(
        ctx.conn, ctx.case_id, pdf, "prefill_pack", bilingual("Pre-filled form pack", "भरा हुआ फ़ॉर्म पैक")
    )
    return {
        "pack_document_id": doc_id,
        "steps": steps_for(app.scheme_id, ctx.language),
        "missing_fields": missing_fields(fields),
        "english_fallback": fell_back,
    }
