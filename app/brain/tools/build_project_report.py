"""build_project_report {scheme_id}: numbers by code (app/finance), words by Claude, PDF into the vault. (LLD §13.1)

The narrative comes from the seeded `report_narrative` artifact when one exists (demos spend no credit); otherwise
one forced-schema Claude call writes business / market / location text, which the Guard scans like any outbound text.
"""

from typing import Any

from pydantic import BaseModel

from app.brain import claude
from app.brain.tools.base import Ctx, ToolError, scan_text, tool_schema
from app.db import jsonb
from app.docs.generated import store_generated
from app.docs.requirements import load_facts
from app.events import log_event
from app.finance.project_report import compute, inputs_from, render_pdf, report_context
from app.i18n import bilingual
from app.pdf.render import PdfUnavailable

SECTIONS = ("business_description", "market", "location")
BILINGUAL = {
    "type": "object",
    "properties": {"en": {"type": "string"}, "hi": {"type": "string"}},
    "required": ["en", "hi"],
}
NARRATIVE_TOOL = {
    "name": "report_narrative",
    "description": "Narrative sections of the project report, in English and simple Hindi. No numbers.",
    "input_schema": {
        "type": "object",
        "properties": {s: BILINGUAL for s in SECTIONS},
        "required": list(SECTIONS),
    },
}


class Input(BaseModel):
    scheme_id: str


SCHEMA = tool_schema(
    "build_project_report",
    "Build the project report PDF for a scheme: cost table, EMI, break-even, DSCR and a 3-year view (all computed "
    "by code) with a short narrative. Needs the cost sheet and a rules-engine match.",
    Input,
)


def load_artifact(conn, case_id: str, kind: str, scheme_id: str | None = None) -> dict | None:
    row = conn.execute(
        "select content from artifacts where case_id = %s and kind = %s and (%s::text is null or scheme_id = %s) "
        "order by created_at desc limit 1",
        (case_id, kind, scheme_id, scheme_id),
    ).fetchone()
    return row["content"] if row else None


def percentages(conn, case_id: str, scheme_id: str) -> tuple[float, float]:
    match = conn.execute(
        "select result, benefit from matches where case_id = %s and scheme_id = %s", (case_id, scheme_id)
    ).fetchone()
    if not match or match["result"] != "FIT":
        raise ToolError("the rules engine has not marked this scheme FIT; call match_schemes first")
    benefit = match["benefit"] or {}
    subsidy, own = benefit.get("subsidy_percent"), benefit.get("own_contribution_percent")
    if subsidy is None or own is None:
        raise ToolError("this scheme's subsidy or own-contribution percentage is not known yet")
    return float(subsidy), float(own)


def narrative_for(ctx: Ctx, scheme: dict, costs: dict, facts: dict) -> tuple[dict, str]:
    stored = load_artifact(ctx.conn, ctx.case_id, "report_narrative", scheme["id"])
    if stored:
        return {k: stored[k] for k in SECTIONS}, stored.get("generated_by", "seed")
    prompt = (
        f"Scheme: {scheme['name']['en']}. Business: {costs['business']['en']}. Sector: {facts.get('sector')}. "
        f"Area: {facts.get('area')}, state: {facts.get('state_name')}. Machines and items: "
        + ", ".join(c["item"] for c in costs["capex"])
        + "."
    )
    out = claude.forced_tool_call(
        NARRATIVE_TOOL,
        [{"type": "text", "text": claude.prompt("report_narrative.md")}],
        [{"role": "user", "content": prompt}],
        ctx.meter,
        purpose="report_narrative",
        client=ctx.client,
    )
    narrative = {k: out[k] for k in SECTIONS}
    text = " ".join(v for section in narrative.values() for v in section.values())
    if not scan_text(ctx, text)["ok"]:  # promises or invented schemes: use neutral facts-only text instead
        narrative = {
            "business_description": {"en": costs["business"]["en"], "hi": costs["business"]["hi"]},
            "market": {"en": "Local customers.", "hi": "स्थानीय ग्राहक।"},
            "location": {"en": "As given in the application.", "hi": "आवेदन में दिया गया स्थान।"},
        }
        return narrative, "fallback"
    ctx.conn.execute(
        "insert into artifacts (case_id, scheme_id, kind, content, source) values (%s, %s, 'report_narrative', %s, 'claude')",
        (ctx.case_id, scheme["id"], jsonb({**narrative, "generated_by": "claude"})),
    )
    return narrative, "claude"


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    scheme = ctx.scheme(data.scheme_id)
    costs = load_artifact(ctx.conn, ctx.case_id, "cost_inputs")
    if not costs:
        raise ToolError(
            "no cost sheet for this person yet: ask for machine costs, working capital and sales first"
        )
    subsidy_pct, own_pct = percentages(ctx.conn, ctx.case_id, data.scheme_id)
    totals = compute(inputs_from(costs, subsidy_pct, own_pct))
    facts = load_facts(ctx.conn, ctx.case_id)
    narrative, source = narrative_for(ctx, scheme, costs, facts)
    context = report_context(costs, totals, narrative, facts.get("name_latin") or "", scheme["name"])
    try:
        pdf, fell_back = render_pdf(context, ctx.language)
    except PdfUnavailable:
        raise ToolError("the PDF engine isn't available on this server; the numbers are in totals") from None
    doc_id = store_generated(
        ctx.conn,
        ctx.case_id,
        pdf,
        "project_report",
        bilingual("Project report", "प्रोजेक्ट रिपोर्ट"),
    )
    log_event(
        ctx.conn,
        ctx.case_id,
        "agent",
        "DOC_UPLOADED",
        bilingual("Project report ready", "प्रोजेक्ट रिपोर्ट तैयार"),
        payload={"document_id": doc_id, "scheme_id": data.scheme_id, "english_fallback": fell_back},
    )
    t = totals.to_dict()
    return {
        "pdf_document_id": doc_id,
        "narrative_source": source,
        "english_fallback": fell_back,
        "totals": {
            k: t[k]
            for k in (
                "total_cost",
                "subsidy",
                "own",
                "loan",
                "emi",
                "break_even_units",
                "profit_month",
                "dscr",
            )
        },
    }
