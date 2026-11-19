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

from pydantic import BaseModel

from app.brain import claude
from app.brain.tools.base import Ctx, ToolError, scan_text, tool_schema
from app.config import get_settings
from app.db import jsonb

MAX_PAGES = 20

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


def select_pages(scheme: dict, files: list[tuple[int, Path]]) -> list[tuple[int, str]]:
    """Pages named by the scheme's rules if any exist on disk; otherwise all pages (capped)."""
    wanted = {r.get("page") for r in scheme["eligibility"]["all"] if r.get("page")}
    chosen = [(n, p) for n, p in files if n in wanted] or files
    return [(n, p.read_text(encoding="utf-8")) for n, p in chosen[:MAX_PAGES]]


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
    pages = select_pages(scheme, page_files(scheme["id"]))
    if pages:
        out = claude.citations_call(pages, scheme["name"]["en"], text, ctx.meter, client=ctx.client)
        return {**out, "source_status": "cited"}
    system = [{"type": "text", "text": claude.prompt("explain_match.md"), "cache_control": claude.EPHEMERAL}]
    out = claude.forced_tool_call(
        EXPLANATION_TOOL,
        system,
        [{"role": "user", "content": text}],
        ctx.meter,
        purpose="explain",
        client=ctx.client,
    )
    return {"en": out["en"], "hi": out["hi"], "citations": [], "source_status": "pending"}


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
    if is_cached(match["explanation"], scheme, match["result"], verification):
        exp, cached = match["explanation"], True
    else:
        exp, cached = generate(scheme, match, ctx), False
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
    }
