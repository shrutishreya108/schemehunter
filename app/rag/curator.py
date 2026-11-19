"""Curator commands (make rulebook-suggest / make rulebook-report). Code only: they read, they never write.

  python -m app.rag.curator suggest <scheme>   top 3 guideline chunks for each material rule without verified evidence
  python -m app.rag.curator report             per scheme: verified / total rules, and each verified rule with its page
"""

import argparse
import re
import sys

from app.config import get_settings
from app.db import connect
from app.rag.search import search
from app.rules.loader import load_schemes
from app.rules.sources import verified_rule_ids
from app.rules.verification import material_rules

EXCERPT_CHARS = 300


def _fact_names(node) -> list[str]:
    if isinstance(node, dict):
        return ([str(node["fact"])] if "fact" in node else []) + [f for v in node.values() for f in _fact_names(v)]
    if isinstance(node, list):
        return [f for v in node for f in _fact_names(v)]
    return []


def rule_query(scheme: dict, rule_id: str) -> str:
    """The query for one rule: its source note and the fact names it reads (plus the rule id itself)."""
    if rule_id == "benefit":
        return " ".join([scheme["name"]["en"], scheme.get("benefit", {}).get("source_note", ""), "subsidy"])
    rule = next(r for r in scheme["eligibility"]["all"] if r["id"] == rule_id)
    words = [rule_id, rule.get("source_note", ""), *_fact_names(rule)]
    return " ".join(w for w in words if w)


def suggest(conn, scheme: dict) -> list[str]:
    verified = verified_rule_ids(conn, scheme["id"])
    lines = [f"Scheme {scheme['id']}: candidate pages for rules without verified evidence (nothing is written)."]
    for rule_id in material_rules(scheme):
        if rule_id in verified:
            continue
        lines.append(f"\n{rule_id}")
        hits = search(conn, rule_query(scheme, rule_id), [scheme["id"]], k=3)
        if not hits:
            lines.append("  no candidate pages")
        for h in hits:
            excerpt = re.sub(r"\s+", " ", h["text"]).strip()[:EXCERPT_CHARS]
            lines.append(f"  page {h['page']:>2}  {excerpt}")
    return lines


def report(conn, schemes: list[dict]) -> list[str]:
    lines = []
    for scheme in schemes:
        rules = material_rules(scheme)
        verified = verified_rule_ids(conn, scheme["id"])
        done = [r for r in rules if r in verified]
        lines.append(f"{scheme['id']}: {len(done)}/{len(rules)} material rules verified")
        for rule_id in done:
            row = conn.execute(
                "select page, passage from rule_evidence where scheme_id = %s and rule_id = %s and status = 'verified' "
                "order by page limit 1",
                (scheme["id"], rule_id),
            ).fetchone()
            lines.append(f"  {rule_id}: page {row['page']}: {row['passage']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sug = sub.add_parser("suggest")
    sug.add_argument("scheme")
    sub.add_parser("report")
    args = parser.parse_args(argv)
    schemes = {s["id"]: s for s in load_schemes()}
    with connect(get_settings().DATABASE_URL) as conn:
        if args.cmd == "suggest":
            if args.scheme not in schemes:
                print(f"unknown scheme: {args.scheme}", file=sys.stderr)
                return 2
            lines = suggest(conn, schemes[args.scheme])
        else:
            lines = report(conn, list(schemes.values()))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
