"""explain_match {scheme_id}: explain the engine's result in two plain sentences. (LLD §8)

- Guideline pages present (rulebook/sources/<id>/pages/NN.txt): citations call over those pages,
  stored as {hi, en, citations:[{page, cited_text}], source_status: "cited"}.
- No pages yet: a forced-schema call without citations, stored with citations: [] and
  source_status: "pending"; the schemes page shows a grey "source pending" badge.
- Cached: if the stored explanation was made for the same result and the same scheme-file hash, it is
  returned without calling Claude (this is also how seeded demo explanations avoid spending credit).
"""

from pathlib import Path
from typing import Any

import psycopg
from pydantic import BaseModel

from app.brain import claude
from app.brain.tools.base import Ctx, ToolError, scan_text, tool_schema
from app.config import get_settings
from app.db import jsonb
from app.rag.search import pages_for

MAX_PAGES = 4  # pages sent for one explanation, retrieved or verified
FALLBACK_PAGES = 6  # used only when retrieval finds nothing (the first pages of the guideline)

EXPLANATION_TOOL = {
    "name": "explanation",
    "description": "The explanation in English and simple Hindi.",
    "input_schema": {
        "type": "object",
        "properties": {"en": {"type": "string"}, "hi": {"type": "string"}},
        "required": ["en", "hi"],
    },
}


class Input(BaseModel):
    scheme_id: str


SCHEMA = tool_schema(
    "explain_match",
    "Explain why the user does or doesn't fit one scheme, in two plain sentences, with guideline citations "
    "when available. Call match_schemes first.",
    Input,
)


def page_files(scheme_id: str, root: Path | None = None) -> list[tuple[int, Path]]:
    root = root or get_settings().RULEBOOK_SOURCES_DIR
    files = [(int(p.stem), p) for p in (root / scheme_id / "pages").glob("*.txt") if p.stem.isdigit()]
    return sorted(files)


def _fact_names(node: Any) -> list[str]:
    if isinstance(node, dict):
        return ([str(node["fact"])] if "fact" in node else []) + [f for v in node.values() for f in _fact_names(v)]
    if isinstance(node, list):
        return [f for v in node for f in _fact_names(v)]
    return []


def retrieval_query(scheme: dict, match: dict) -> str:
    """Built in code: the scheme name, the deciding rule, and each failing or unknown rule's note and fact names."""
    rules = match["rule_results"]["rules"]
    by_id = {r["id"]: r for r in scheme["eligibility"]["all"]}
    decider = match["rule_results"].get("deciding_rule")
    parts = [scheme["name"]["en"], decider or ""]
    for rid, outcome in rules.items():
        if outcome == "PASS" or rid not in by_id:
            continue
        parts.append(by_id[rid].get("source_note", ""))
        parts += _fact_names(by_id[rid])
    return " ".join(p for p in parts if p)


def verified_pages(conn: psycopg.Connection, scheme_id: str) -> list[int]:
    """Pages that carry a verified evidence passage for this scheme."""
    rows = conn.execute(
        "select distinct page from rule_evidence where scheme_id = %s and status = 'verified' and page is not null "
        "order by page",
        (scheme_id,),
    ).fetchall()
    return [r["page"] for r in rows]


def select_pages(
    conn: psycopg.Connection, scheme: dict, match: dict, files: list[tuple[int, Path]]
) -> tuple[list[tuple[int, str]], str]:
    """Pages to send, and how they were chosen. The page hints in the scheme file are ignored on purpose.

    1. pages named by the scheme's verified evidence rows; 2. pages that retrieval ranks for the query built
    from the result; 3. only if both are empty, the first FALLBACK_PAGES pages. At most MAX_PAGES from 1 and 2.
    """
    on_disk = dict(files)
    chosen: list[int] = []
    for n in verified_pages(conn, scheme["id"]):
        if n in on_disk and n not in chosen and len(chosen) < MAX_PAGES:
            chosen.append(n)
    from_evidence = len(chosen)
    for n in pages_for(conn, retrieval_query(scheme, match), scheme["id"], max_pages=MAX_PAGES):
        if n in on_disk and n not in chosen and len(chosen) < MAX_PAGES:
            chosen.append(n)
    if not chosen:
        chosen = [n for n, _ in files[:FALLBACK_PAGES]]
        how = "fallback"
    elif from_evidence and len(chosen) > from_evidence:
        how = "evidence+retrieved"
    elif from_evidence:
        how = "evidence"
    else:
        how = "retrieved"
    return [(n, on_disk[n].read_text(encoding="utf-8")) for n in sorted(chosen)], how


def instruction(scheme: dict, match: dict, language: str) -> str:
    rules = match["rule_results"]["rules"]
    notes = {r["id"]: r.get("source_note", "") for r in scheme["eligibility"]["all"]}
    lines = [f"- {rid}: {outcome} ({notes.get(rid, '')})" for rid, outcome in rules.items()]
    return (
        f"Scheme: {scheme['name']['en']} ({scheme['id']}).\n"
        f"Rules-engine result: {match['result']} (deciding rule: {match['rule_results'].get('deciding_rule')}).\n"
        f"Per-rule results:\n" + "\n".join(lines) + "\n"
        f"Computed benefit: {match.get('benefit') or {}}.\n"
        f"Missing facts: {match.get('missing_facts') or []}.\n"
        f"Verification: {match['rule_results'].get('verification', 'preliminary')}. "
        f"Rules not yet checked against official guidelines: {match['rule_results'].get('unverified') or []}. "
        "If any are listed, say the result is preliminary; never say the person qualifies without that caveat.\n"
        f"The user's language is {'Hindi' if language == 'hi' else 'English'}.\n"
        "Explain this result. Reply as `EN:` … then `HI:` …"
    )


def fallback_text(scheme: dict, result: str, verification: str = "verified") -> dict[str, str]:
    """Deterministic, promise-free text used if the generated one fails the guard scan."""
    if verification == "preliminary" and result == "FIT":
        return {
            "en": f"{scheme['name']['en']}: Preliminary: you may qualify to apply. The rules are not yet checked against official guidelines.",
            "hi": f"{scheme['name']['hi']}: प्रारंभिक: आप आवेदन कर सकते हैं। ये नियम अभी आधिकारिक दिशानिर्देशों से जाँचे नहीं गए हैं।",
        }
    en = {
        "FIT": "You qualify to apply.",
        "NO_FIT": "You do not meet one of the rules right now.",
        "NEEDS_INFO": "I need a little more information to check this.",
    }[result]
    hi = {
        "FIT": "आप आवेदन करने के योग्य हैं।",
        "NO_FIT": "अभी आप एक नियम पूरा नहीं करतीं।",
        "NEEDS_INFO": "जाँच के लिए मुझे थोड़ी और जानकारी चाहिए।",
    }[result]
    return {"en": f"{scheme['name']['en']}: {en}", "hi": f"{scheme['name']['hi']}: {hi}"}


def is_cached(explanation: dict | None, scheme: dict, result: str, verification: str = "verified") -> bool:
    return (
        bool(explanation)
        and explanation.get("spec_hash") == scheme["_hash"]
        and explanation.get("result") == result
        and explanation.get("verification", "verified") == verification
    )


def generate(scheme: dict, match: dict, ctx: Ctx) -> dict:
    text = instruction(scheme, match, ctx.language)
    files = page_files(scheme["id"])
    pages, how = select_pages(ctx.conn, scheme, match, files) if files else ([], "none")
    if pages:
        out = claude.citations_call(pages, scheme["name"]["en"], text, ctx.meter, client=ctx.client)
        return {**out, "source_status": "cited", "pages": [n for n, _ in pages], "page_selection": how}
    system = [{"type": "text", "text": claude.prompt("explain_match.md"), "cache_control": claude.EPHEMERAL}]
    out = claude.forced_tool_call(
        EXPLANATION_TOOL,
        system,
        [{"role": "user", "content": text}],
        ctx.meter,
        purpose="explain",
        client=ctx.client,
    )
    return {"en": out["en"], "hi": out["hi"], "citations": [], "source_status": "pending", "pages": [], "page_selection": how}


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    scheme = ctx.scheme(data.scheme_id)
    match = ctx.conn.execute(
        "select result, rule_results, missing_facts, benefit, explanation from matches "
        "where case_id = %s and scheme_id = %s",
        (ctx.case_id, scheme["id"]),
    ).fetchone()
    if not match:
        raise ToolError("no match stored for this scheme; call match_schemes first")
    verification = (match["rule_results"] or {}).get("verification", "verified")
    pages_used, page_how = [], None
    if is_cached(match["explanation"], scheme, match["result"], verification):
        exp, cached = match["explanation"], True
    else:
        exp, cached = generate(scheme, match, ctx), False
        pages_used, page_how = exp.pop("pages", []), exp.pop("page_selection", None)
        check = scan_text(ctx, exp["en"], exp["hi"])
        if not check["ok"]:
            exp = {**exp, **fallback_text(scheme, match["result"], verification), "scan_failed": check}
        exp = {
            **exp,
            "spec_hash": scheme["_hash"],
            "result": match["result"],
            "generated_by": "claude",
            "verification": verification,
        }
        ctx.conn.execute(
            "update matches set explanation = %s where case_id = %s and scheme_id = %s",
            (jsonb(exp), ctx.case_id, scheme["id"]),
        )
    lang = ctx.language if ctx.language in ("hi", "en") else "en"
    return {
        "scheme_id": scheme["id"],
        "result": match["result"],
        "explanation": exp[lang],
        "citations": len(exp.get("citations") or []),
        "source_status": exp.get("source_status"),
        "cached": cached,
        "pages": pages_used,  # numbers only; the runner logs them in TOOL_CALLED
        "page_selection": page_how,
    }
