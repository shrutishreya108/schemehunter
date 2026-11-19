from datetime import date
from pathlib import Path

import yaml

from app.finance.project_report import ReportInputs, compute
from app.rules.cli import main
from app.rules.engine import evaluate_all
from app.rules.facts import build_facts
from app.rules.loader import load_schemes

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 16)
PERSONAS = {
    p["key"]: p for p in yaml.safe_load((ROOT / "seed" / "personas.yaml").read_text(encoding="utf-8"))
}


def results_for(answers: dict, state: str = "KA") -> dict:
    facts = build_facts(answers, state_code=state, today=TODAY)
    return {r["scheme_id"]: (r["result"], r["deciding_rule"]) for r in evaluate_all(load_schemes(), facts)}


def test_cli_prints_table(capsys) -> None:
    assert main(["--profile", "meena", "--today", "2026-11-16"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["scheme", "result", "deciding", "rule", "page"]
    rows = {line.split()[0]: line.split()[1:] for line in out[2:]}
    assert rows["pmegp"] == ["FIT", "-", "-"]
    assert rows["stand_up_india"] == ["NEEDS_INFO", "loan_range", "-"]


def test_cli_finds_test_profiles(capsys) -> None:
    main(["--profile", "p12_service_low_education", "--today", "2026-11-16"])
    rows = {line.split()[0]: line.split()[1:] for line in capsys.readouterr().out.splitlines()[2:]}
    assert rows["pmegp"] == ["NO_FIT", "education", "6"]  # PDF page 6 of the guideline (was 4 before the audit)


def test_personas() -> None:
    assert results_for(PERSONAS["ravi"]["profile"])["pmegp"] == ("FIT", None)
    assert results_for(PERSONAS["fatima"]["profile"])["mudra"] == ("FIT", None)


def test_meena_rehearsal_script_outcome() -> None:
    """seed/conversations/meena.md: expected engine state after her three answers."""
    answers = {
        **PERSONAS["meena"]["profile"],
        "loan_amount": 300000,
        "family_income": 120000,
        "special_circumstance": "none",
    }
    assert results_for(answers) == {
        "cgtmse": ("NEEDS_INFO", "enterprise_size"),
        "mudra": ("FIT", None),
        "pmegp": ("FIT", None),
        "stand_up_india": ("NO_FIT", "loan_range"),
        "udyogini_ka": ("FIT", None),
    }


def test_seed_costs_add_up_to_project_cost() -> None:
    for key, persona in PERSONAS.items():
        costs = yaml.safe_load((ROOT / "seed" / "costs" / f"{key}.yaml").read_text(encoding="utf-8"))
        inp = ReportInputs(
            capex=[c["amount"] for c in costs["capex"]],
            working_capital=costs["working_capital"],
            own_pct=5,
            subsidy_pct=25,
            annual_rate=costs["annual_rate"],
            tenure_months=costs["tenure_months"],
            units_per_month=costs["units_per_month"],
            price=costs["price"],
            variable_cost=costs["variable_cost"],
            fixed_costs=costs["fixed_costs"],
            growth_pct=costs["growth_pct"],
        )
        totals = compute(inp)
        assert totals.total_cost == persona["profile"]["project_cost"], key
        assert totals.break_even_units and totals.break_even_units < inp.units_per_month, key
