"""Rules engine vs hand-labelled expectations (tests/rules/profiles.yaml). Must be 30/30."""

from datetime import date
from pathlib import Path

import pytest
import yaml

from app.rules.engine import FAIL, PASS, UNKNOWN, evaluate, evaluate_all
from app.rules.facts import build_facts
from app.rules.loader import load_schemes

DOC = yaml.safe_load((Path(__file__).parent / "profiles.yaml").read_text(encoding="utf-8"))
TODAY: date = DOC["today"]
SCHEMES = load_schemes()


def answers_for(profile: dict) -> dict:
    merged = {**DOC["base"], **profile.get("set", {})}
    return {k: v for k, v in merged.items() if k not in profile.get("unset", [])}


def facts_for(profile: dict) -> dict:
    answers = answers_for(profile)
    return build_facts(answers, state_code=answers.get("state_code"), today=TODAY)


def test_thirty_profiles() -> None:
    assert len(DOC["profiles"]) == 30
    assert len({p["key"] for p in DOC["profiles"]}) == 30


@pytest.mark.parametrize("profile", DOC["profiles"], ids=[p["key"] for p in DOC["profiles"]])
def test_profile(profile: dict) -> None:
    results = {r["scheme_id"]: r for r in evaluate_all(SCHEMES, facts_for(profile))}
    assert set(results) == set(profile["expect"]), "wrong set of schemes evaluated for this state"
    for scheme_id, (result, rule) in profile["expect"].items():
        got = results[scheme_id]
        assert (got["result"], got["deciding_rule"]) == (result, rule), f"{scheme_id}: rules={got['rules']}"


def test_every_rule_hits_pass_fail_unknown() -> None:
    """The 30 profiles exercise every top-level rule's PASS, FAIL and UNKNOWN branch."""
    seen: dict[tuple[str, str], set[str]] = {}
    for profile in DOC["profiles"]:
        facts = facts_for(profile)
        for scheme in SCHEMES:
            if scheme["id"] not in profile["expect"]:
                continue
            for rule_id, outcome in evaluate(scheme, facts)["rules"].items():
                seen.setdefault((scheme["id"], rule_id), set()).add(outcome)
    all_rules = {(s["id"], r["id"]) for s in SCHEMES for r in s["eligibility"]["all"]}
    assert set(seen) == all_rules
    missing = {k: {PASS, FAIL, UNKNOWN} - v for k, v in seen.items() if v != {PASS, FAIL, UNKNOWN}}
    assert not missing, f"branches not covered: {missing}"


def test_missing_facts_drive_next_question() -> None:
    profile = next(p for p in DOC["profiles"] if p["key"] == "p20_loan_unknown")
    results = {r["scheme_id"]: r for r in evaluate_all(SCHEMES, facts_for(profile))}
    assert results["stand_up_india"]["missing_facts"] == ["loan_amount"]
    assert results["pmegp"]["missing_facts"] == []


def test_irrelevant_unknowns_not_asked() -> None:
    """Mudra at 2.5L passes on the first branch, so previous_tarun_repaid is never asked."""
    profile = {"set": {"loan_amount": 250000, "project_cost": 300000}, "unset": ["previous_tarun_repaid"]}
    mudra = next(r for r in evaluate_all(SCHEMES, facts_for(profile)) if r["scheme_id"] == "mudra")
    assert mudra["result"] == "FIT" and mudra["missing_facts"] == []


@pytest.mark.parametrize(
    ("overrides", "unset", "scheme_id", "expected"),
    [
        (
            {"area": "rural", "category": "SC", "gender": "male"},
            [],
            "pmegp",
            {"subsidy_percent": 35, "own_contribution_percent": 5},
        ),
        ({}, [], "pmegp", {"subsidy_percent": 25, "own_contribution_percent": 5}),
        (
            {"gender": "male", "category": "GEN"},
            [],
            "pmegp",
            {"subsidy_percent": 15, "own_contribution_percent": 10},
        ),
        (
            {"gender": "male", "category": "GEN", "area": "rural"},
            [],
            "pmegp",
            {"subsidy_percent": 25, "own_contribution_percent": 10},
        ),
        ({"loan_amount": 50000}, [], "mudra", {"mudra_category": "Shishu"}),
        ({"loan_amount": 250000}, [], "mudra", {"mudra_category": "Kishore"}),
        ({"loan_amount": 1000000}, [], "mudra", {"mudra_category": "Tarun"}),
        ({}, [], "mudra", {"mudra_category": "Tarun Plus"}),
        ({}, ["loan_amount"], "mudra", {"mudra_category": None}),
        ({"category": "SC"}, [], "udyogini_ka", {"subsidy_percent": 50}),
        ({"special_circumstance": "disabled"}, [], "udyogini_ka", {"subsidy_percent": 50}),
        ({}, [], "udyogini_ka", {"subsidy_percent": 30}),
        ({}, ["special_circumstance"], "udyogini_ka", {"subsidy_percent": None}),
        ({}, [], "cgtmse", {}),
    ],
)
def test_benefit(overrides: dict, unset: list, scheme_id: str, expected: dict) -> None:
    facts = facts_for({"set": overrides, "unset": unset})
    scheme = next(s for s in SCHEMES if s["id"] == scheme_id)
    assert evaluate(scheme, facts)["benefit"] == expected


def test_unknown_when_asks_for_when_fact_not_then_fact() -> None:
    """Udyogini income_cap: when (category, special_circumstance) then family_income.

    With special_circumstance unknown the `when` is UNKNOWN, so only that fact is asked; family_income
    (the `then` fact) is not, even though it is also missing.
    """
    profile = {"set": {"loan_amount": 250000}, "unset": ["special_circumstance", "family_income"]}
    udy = next(r for r in evaluate_all(SCHEMES, facts_for(profile)) if r["scheme_id"] == "udyogini_ka")
    assert udy["result"] == "NEEDS_INFO" and udy["deciding_rule"] == "income_cap"
    assert "special_circumstance" in udy["missing_facts"]
    assert "family_income" not in udy["missing_facts"]


def test_then_fact_asked_once_when_passes() -> None:
    profile = {"set": {"loan_amount": 250000}, "unset": ["family_income"]}
    udy = next(r for r in evaluate_all(SCHEMES, facts_for(profile)) if r["scheme_id"] == "udyogini_ka")
    assert udy["missing_facts"] == ["family_income"]
