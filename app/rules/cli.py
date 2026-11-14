"""python -m app.rules.cli --profile meena

Looks the profile up in seed/personas.yaml, then tests/rules/profiles.yaml.
Prints scheme / result / deciding rule / page for every scheme that applies to the profile's state.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

from app.config import ROOT
from app.rules.engine import evaluate_all
from app.rules.facts import build_facts
from app.rules.loader import load_schemes

PERSONAS = ROOT / "seed" / "personas.yaml"
TEST_PROFILES = ROOT / "tests" / "rules" / "profiles.yaml"


def persona_answers(key: str, path: Path = PERSONAS) -> tuple[dict, str | None] | None:
    for p in yaml.safe_load(path.read_text(encoding="utf-8")):
        if p["key"] == key:
            return dict(p["profile"]), p.get("state_code")
    return None


def test_profile_answers(key: str, path: Path = TEST_PROFILES) -> tuple[dict, str | None] | None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    for p in doc["profiles"]:
        if p["key"] == key:
            answers = {
                k: v for k, v in {**doc["base"], **p.get("set", {})}.items() if k not in p.get("unset", [])
            }
            return answers, answers.get("state_code")
    return None


def find_profile(key: str) -> tuple[dict, str | None]:
    found = persona_answers(key) or test_profile_answers(key)
    if not found:
        raise SystemExit(f"no profile {key!r} in {PERSONAS.name} or {TEST_PROFILES.name}")
    return found


def format_rows(results: list[dict]) -> str:
    header = ("scheme", "result", "deciding rule", "page")
    rows = [header] + [
        (r["scheme_id"], r["result"], r["deciding_rule"] or "-", str(r["deciding_page"] or "-"))
        for r in results
    ]
    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate a profile against the Rule Book.")
    parser.add_argument("--profile", required=True, help="persona key (e.g. meena) or test profile key")
    parser.add_argument("--today", type=date.fromisoformat, default=None, help="YYYY-MM-DD, for age")
    parser.add_argument("--missing", action="store_true", help="also print missing facts")
    args = parser.parse_args(argv)

    answers, state_code = find_profile(args.profile)
    facts = build_facts(answers, state_code=state_code, today=args.today)
    results = evaluate_all(load_schemes(), facts)
    print(format_rows(results))
    if args.missing:
        for r in results:
            if r["missing_facts"]:
                print(f"{r['scheme_id']}: needs {', '.join(r['missing_facts'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
