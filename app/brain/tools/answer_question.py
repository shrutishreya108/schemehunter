"""answer_question {questions[]}: grounded answers with labels and sources (verified / estimate / unknown).

Sources are gathered in code: guideline text (when loaded), the rule files (marked as estimates), privacy facts
that the code guarantees, the person's own papers (masked fields only), and subsidy figures computed in code.
The model may only cite source ids it was given. Citations to anything else are dropped, and an answer with no
valid source is labelled unknown. Every question gets an answer.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.brain import claude
from app.brain.tools.base import Ctx, ToolError, scan_text, tool_schema
from app.brain.tools.build_project_report import load_artifact, percentages
from app.brain.tools.explain_match import page_files
from app.docs.masking import mask_fields
from app.finance.project_report import compute, inputs_from, inr
from app.rules.loader import load_schemes

MAX_GUIDELINE_CHARS = 1500
MAX_QUESTIONS = 8
LABELS = ("verified", "estimate", "unknown")

# Facts about the code itself. Each one is checkable in the file named in its text.
PRIVACY_FACTS = [
    (
        "code:encryption",
        "Uploaded papers are encrypted with AES-GCM on the server before they are saved (app/docs/vault.py).",
    ),
    (
        "code:masking",
        "ID numbers are masked: only the last four digits are kept in stored fields (app/docs/masking.py).",
    ),
    (
        "code:vision",
        "Only Aadhaar and passbook photos are sent to the AI for a check. Other papers are not (app/docs/pipeline.py).",
    ),
    (
        "code:no-delete",
        "There is no button yet to delete your papers. Papers are deleted automatically 90 days after a case is closed (app/clock/handlers.py).",
    ),
    (
        "code:no-submit",
        "The app never submits a form. You press Submit on the official portal yourself (app/brain/tools/request_submit.py).",
    ),
]

QA_TOOL = {
    "name": "answers",
    "description": "One answer per question, with a label and the source ids used.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answers": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "q": {"type": "integer"},
                        "answer": {"type": "string"},
                        "label": {"type": "string", "enum": list(LABELS)},
                        "sources": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["q", "answer", "label", "sources"],
                },
            }
        },
        "required": ["answers"],
    },
}


class Input(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=MAX_QUESTIONS)


SCHEMA = tool_schema(
    "answer_question",
    "Answer the user's questions (one item per question) from grounded sources. Each answer is labelled verified, "
    "estimate or unknown. Use it for questions about documents, privacy, subsidy amounts, eligibility wording or "
    "application steps. Copy the answers into your reply, numbered, with their labels.",
    Input,
)


def guideline_sources(ctx: Ctx, scheme_ids: list[str]) -> list[dict]:
    out = []
    for sid in scheme_ids:
        pages = page_files(sid)
        if not pages:
            out.append(
                {
                    "id": f"guideline:{sid}:none",
                    "label": "unknown",
                    "text": f"No official guideline text is loaded for {sid} yet.",
                }
            )
            continue
        budget = MAX_GUIDELINE_CHARS
        for num, path in pages[:6]:
            text = path.read_text(encoding="utf-8")[:budget]
            budget -= len(text)
            out.append({"id": f"guideline:{sid}:p{num}", "label": "verified", "text": text})
            if budget <= 0:
                break
    return out


def rule_sources(schemes: list[dict]) -> list[dict]:
    out = []
    for s in schemes:
        docs = ", ".join(d for d in (s.get("documents") or []) if "_if_" not in d) or "not listed yet"
        out.append(
            {
                "id": f"rulefile:{s['id']}",
                "label": "estimate",
                "text": f"{s['name']['en']} rule file (not yet checked against the official guideline). Papers listed: {docs}.",
            }
        )
    return out


def paper_sources(ctx: Ctx) -> list[dict]:
    rows = ctx.conn.execute(
        "select id, doc_type, check_status, check_result, extracted from documents where case_id = %s order by created_at",
        (ctx.case_id,),
    ).fetchall()
    return [
        {
            "id": f"paper:{str(r['id'])[:8]}",
            "label": "verified",
            "text": f"Your {r['doc_type'] or 'paper'}: check result {r['check_status']}. Masked fields: {mask_fields(r['extracted'] or {})}.",
        }
        for r in rows
    ]


def subsidy_source(ctx: Ctx, schemes_fit: list[str]) -> list[dict]:
    costs = load_artifact(ctx.conn, ctx.case_id, "cost_inputs")
    if not costs or not schemes_fit:
        return [
            {
                "id": "calc:subsidy:none",
                "label": "unknown",
                "text": "There is no cost sheet for this person yet, so the subsidy amount cannot be calculated.",
            }
        ]
    out = []
    for sid in schemes_fit:
        try:
            subsidy_pct, own_pct = percentages(ctx.conn, ctx.case_id, sid)
        except ToolError:
            continue
        t = compute(inputs_from(costs, subsidy_pct, own_pct))
        out.append(
            {
                "id": f"calc:subsidy:{sid}",
                "label": "estimate",
                "text": (
                    f"{sid.upper()}, calculated from the cost sheet: total project cost {inr(t.total_cost)}; "
                    f"subsidy {subsidy_pct:g}% = {inr(t.subsidy)}; own contribution {own_pct:g}% = {inr(t.own)}; "
                    f"bank loan {inr(t.loan)}; monthly instalment about {inr(t.emi)} over {costs['tenure_months']} months."
                ),
            }
        )
    return out or [
        {
            "id": "calc:subsidy:none",
            "label": "unknown",
            "text": "The subsidy cannot be calculated for the schemes that fit.",
        }
    ]


def gather_sources(ctx: Ctx) -> list[dict]:
    schemes = load_schemes()
    matches = ctx.conn.execute(
        "select scheme_id, result from matches where case_id = %s", (ctx.case_id,)
    ).fetchall()
    fit = [m["scheme_id"] for m in matches if m["result"] == "FIT"]
    relevant = [m["scheme_id"] for m in matches] or [s["id"] for s in schemes]
    sources = [{"id": pid, "label": "verified", "text": text} for pid, text in PRIVACY_FACTS]
    sources += guideline_sources(ctx, relevant)
    sources += rule_sources([s for s in schemes if s["id"] in relevant])
    sources += paper_sources(ctx)
    sources += subsidy_source(ctx, fit)
    return sources


def check_answers(raw: list[dict], questions: list[str], valid_ids: set[str], ctx: Ctx) -> list[dict]:
    by_q = {int(a["q"]): a for a in raw if str(a.get("q", "")).isdigit()}
    out = []
    for i, question in enumerate(questions, 1):
        a = by_q.get(i, {})
        label = a.get("label") if a.get("label") in LABELS else "unknown"
        sources = [s for s in (a.get("sources") or []) if s in valid_ids]
        answer = (a.get("answer") or "").strip()
        if label in ("verified", "estimate") and not sources:
            label = "unknown"
        if label == "unknown" or not answer:
            label = "unknown"
            answer = (
                answer
                or "I don't have verified information on this yet. You can ask the scheme office or check the official portal."
            )
        if not scan_text(ctx, answer)["ok"]:  # promises or invented schemes: replace with the safe wording
            label, sources = "unknown", []
            answer = "I don't have verified information on this yet. You can ask the scheme office or check the official portal."
        out.append({"q": i, "question": question, "answer": answer, "label": label, "sources": sources})
    return out


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    sources = gather_sources(ctx)
    valid_ids = {s["id"] for s in sources}
    listing = "\n\n".join(f"[{s['id']}] ({s['label']}) {s['text']}" for s in sources)
    questions = "\n".join(f"{i}. {q}" for i, q in enumerate(data.questions, 1))
    raw = claude.forced_tool_call(
        QA_TOOL,
        [{"type": "text", "text": claude.prompt("answer_question.md")}],
        [{"role": "user", "content": f"SOURCES:\n{listing}\n\nQUESTIONS:\n{questions}"}],
        ctx.meter,
        purpose="qa",
        max_tokens=1200,
        client=ctx.client,
    )
    answers = check_answers(raw.get("answers", []), data.questions, valid_ids, ctx)
    return {
        "answers": answers,
        "instruction": "Copy each answer into your reply, numbered, with its label and sources.",
    }
