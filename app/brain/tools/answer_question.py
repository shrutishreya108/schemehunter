"""answer_question {questions[]}: grounded answers with labels and sources (verified / estimate / unknown).

Sources are gathered in code: guideline text (when loaded), the rule files (marked as estimates), privacy facts
that the code guarantees, the person's own papers (masked fields only), and subsidy figures computed in code.
The model may only cite source ids it was given. Citations to anything else are dropped, and an answer with no
valid source is labelled unknown. Every question gets an answer.
"""

import re
from collections import Counter
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.brain import claude
from app.brain.tools.base import Ctx, ToolError, scan_text, tool_schema
from app.brain.tools.build_project_report import load_artifact, percentages
from app.brain.tools.explain_match import page_files, verified_pages
from app.finance.project_report import compute, inputs_from, inr
from app.rag.search import search
from app.rules.loader import load_schemes
from app.rules.sources import is_complete
from app.web.cache import refresh_many, web_pages

GUIDELINE_CHUNKS = 4  # per scheme
WEB_PAGES_PER_SCHEME = 2
WEB_TEXT_CHARS = 6000
WEB_BUDGET_S = 10.0  # per scheme, all its pages together
WEB_FIXED_SENTENCE = (
    "This comes from the official web page, not a checked rule, and the page may not show the latest circulars."
)
WEB_UNREADABLE = "I could not read the official page right now."
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


def guideline_sources(ctx: Ctx, scheme_ids: list[str], questions: list[str]) -> list[dict]:
    """Up to GUIDELINE_CHUNKS retrieved chunks per scheme, ranked for the questions (app/rag).

    A chunk is labelled verified only when its source document is complete AND the page carries a verified
    evidence passage; otherwise it is an estimate. Nothing retrieved means unknown. The model may cite only these ids.
    """
    query = " ".join(questions)
    out: list[dict] = []
    for sid in scheme_ids:
        hits = search(ctx.conn, query, [sid], k=GUIDELINE_CHUNKS)
        if not hits:
            out.append(
                {
                    "id": f"guideline:{sid}:none",
                    "label": "unknown",
                    "text": f"No guideline text for {sid} matched these questions.",
                }
            )
            continue
        docs = [
            d
            for d in ctx.conn.execute(
                "select * from source_documents where scheme_id = %s order by id", (sid,)
            ).fetchall()
            if is_complete(d)
        ]
        doc = docs[0] if docs else None
        verified = set(verified_pages(ctx.conn, sid)) if doc else set()
        if doc:
            cite = f"{doc['title']}, version {doc['version']}, retrieved {doc['retrieval_date']}, sha256 {doc['sha256'][:12]}"
        else:
            cite = f"{sid} guideline; source details not recorded, so not cited as verified"
        per_page = Counter(h["page"] for h in hits)
        for h in hits:
            page = h["page"]
            ref = f"guideline:{sid}:p{page}" + (f":{h['ord']}" if per_page[page] > 1 else "")
            label = "verified" if doc and page in verified else "estimate"
            out.append({"id": ref, "label": label, "text": f"{h['text']}\n(Source: {cite}, page {page})"})
    return out


def web_sources(ctx: Ctx, scheme_ids: list[str]) -> list[dict]:
    """Official web pages, only for schemes with no guideline PDF text. A scheme with PDF text never reaches the network."""
    out: list[dict] = []
    for sid in scheme_ids:
        if page_files(sid):
            continue
        pages = web_pages(sid)[:WEB_PAGES_PER_SCHEME]
        if not pages:
            continue
        results = refresh_many(ctx.conn, sid, [p["url"] for p in pages], budget_s=WEB_BUDGET_S)
        read = 0
        for n, item in enumerate(pages, 1):
            view = results.get(item["url"])
            if not view:
                continue
            read += 1
            domain = urlsplit(item["url"]).hostname or ""
            when = view["fetched_at"].strftime("%Y-%m-%d %H:%M UTC")
            title = view["title"] or item["title"]
            head = (
                f"Official web page: {title} ({domain}), read {when}"
                f"{', stale copy' if view['stale'] else ''}"
                f"{'; this copy is over a month old' if view['old'] else ''}. "
            )
            out.append(
                {
                    "id": f"web:{sid}:{n}",
                    "label": "estimate",
                    "text": head + view["text"][:WEB_TEXT_CHARS],
                    "meta": {"title": title, "domain": domain, "when": when, "url": item["url"], "stale": view["stale"]},
                }
            )
        if not read:
            out.append({"id": f"web:{sid}:none", "label": "unknown", "text": WEB_UNREADABLE})
    return out


def named_schemes(schemes: list[dict], questions: list[str]) -> list[str]:
    """Schemes the questions name (by name or alias), so an explanation works even before a match exists."""
    text = " ".join(questions).casefold()
    found = []
    for s in schemes:
        names = [s["name"]["en"], *(s.get("aliases") or [])]
        if any(re.search(rf"(?<![a-z0-9]){re.escape(n.casefold())}(?![a-z0-9])", text) for n in names):
            found.append(s["id"])
    return found


def rule_sources(schemes: list[dict]) -> list[dict]:
    out = []
    for s in schemes:
        docs = ", ".join(d for d in (s.get("documents") or []) if "_if_" not in d) or "not listed yet"
        out.append(
            {
                "id": f"rulefile:{s['id']}",
                "label": "estimate",
                "text": f"{s['name']['en']} rule file (not yet checked against the official guideline; results are preliminary). Papers listed: {docs}.",
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
            "text": f"Your {r['doc_type'] or 'paper'}: check result {r['check_status']}.",
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


def gather_sources(ctx: Ctx, questions: list[str]) -> list[dict]:
    schemes = load_schemes()
    matches = ctx.conn.execute(
        "select scheme_id, result from matches where case_id = %s", (ctx.case_id,)
    ).fetchall()
    fit = [m["scheme_id"] for m in matches if m["result"] == "FIT"]
    relevant = [m["scheme_id"] for m in matches] or [s["id"] for s in schemes]
    relevant = list(dict.fromkeys(named_schemes(schemes, questions) + relevant))
    sources = [{"id": pid, "label": "verified", "text": text} for pid, text in PRIVACY_FACTS]
    sources += guideline_sources(ctx, relevant, questions)
    sources += web_sources(ctx, relevant)
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


def attach_web_sources(answers: list[dict], sources: list[dict]) -> list[dict]:
    """Code, not the model, says where web text came from: title, domain, read time, link and one fixed sentence."""
    by_id = {s["id"]: s for s in sources}
    out = []
    for a in answers:
        used = [by_id[i] for i in a["sources"] if i in by_id and by_id[i]["id"].startswith("web:")]
        if any(s["id"].endswith(":none") for s in used):
            out.append({**a, "answer": WEB_UNREADABLE, "label": "unknown", "sources": []})
            continue
        if not used:
            out.append(a)
            continue
        lines = [a["answer"]]
        for s in dict.fromkeys(s["id"] for s in used):
            m = by_id[s]["meta"]
            stale = ", stale copy" if m["stale"] else ""
            lines.append(f"Source: {m['title']} ({m['domain']}), read {m['when']}{stale}: {m['url']}")
        lines.append(WEB_FIXED_SENTENCE)
        out.append({**a, "answer": "\n".join(lines)})
    return out


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    sources = gather_sources(ctx, data.questions)
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
    answers = attach_web_sources(answers, sources)
    return {
        "answers": answers,
        "instruction": "Copy each answer into your reply, numbered, with its label and sources.",
    }
