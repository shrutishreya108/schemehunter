"""match_schemes {}: evaluate() for every scheme in the case's state + central; upsert matches. (LLD §8)

The rules engine decides. This tool only loads facts, runs it, stores the result and reports it.
"""

from typing import Any

from pydantic import BaseModel

from app.brain.tools.base import Ctx, tool_schema
from app.db import jsonb
from app.events import log_event
from app.i18n import bilingual
from app.rules.engine import evaluate_all
from app.rules.facts import build_facts


class Input(BaseModel):
    pass


SCHEMA = tool_schema(
    "match_schemes",
    "Run the rules engine on the saved profile. Returns FIT / NO_FIT / NEEDS_INFO per scheme, the deciding "
    "rule, missing facts and the computed benefit. The only source of eligibility.",
    Input,
)

UPSERT = (
    "insert into matches (case_id, scheme_id, result, rule_results, missing_facts, benefit, computed_at) "
    "values (%s, %s, %s, %s, %s, %s, now()) on conflict (case_id, scheme_id) do update set "
    "result = excluded.result, rule_results = excluded.rule_results, missing_facts = excluded.missing_facts, "
    "benefit = excluded.benefit, computed_at = now(), "
    "explanation = case when matches.result = excluded.result then matches.explanation else null end"
)


def load_facts(ctx: Ctx) -> dict:
    rows = ctx.conn.execute(
        "select key, value from profile_answers where case_id = %s", (ctx.case_id,)
    ).fetchall()
    return build_facts({r["key"]: r["value"] for r in rows}, state_code=ctx.state_code)


def run_match(ctx: Ctx) -> list[dict]:
    results = evaluate_all(ctx.schemes, load_facts(ctx))
    for r in results:
        rule_results = {
            "rules": r["rules"],
            "deciding_rule": r["deciding_rule"],
            "deciding_page": r["deciding_page"],
        }
        ctx.conn.execute(
            UPSERT,
            (
                ctx.case_id,
                r["scheme_id"],
                r["result"],
                jsonb(rule_results),
                jsonb(r["missing_facts"]),
                jsonb(r["benefit"]),
            ),
        )
    return results


def handle(data: Input, ctx: Ctx) -> dict[str, Any]:
    results = run_match(ctx)
    fits = [r["scheme_id"] for r in results if r["result"] == "FIT"]
    log_event(
        ctx.conn,
        ctx.case_id,
        "agent",
        "MATCHED",
        bilingual(f"Rules engine: {len(fits)} scheme(s) fit", f"नियम जाँच: {len(fits)} योजना(एँ) फ़िट"),
        payload={"fit": fits},
    )
    next_fact = next((f for r in results if r["result"] == "NEEDS_INFO" for f in r["missing_facts"]), None)
    return {
        "results": [
            {k: r[k] for k in ("scheme_id", "result", "deciding_rule", "missing_facts", "benefit")}
            for r in results
        ],
        "next_fact_to_ask": next_fact,
    }
