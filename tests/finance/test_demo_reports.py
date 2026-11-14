"""The three demo personas' numbers, to the rupee, checked against hand calculations; plus rupee formatting."""

from pathlib import Path

import pytest
import yaml

from app.finance.project_report import compute, emi_for, inputs_from, inr, monthly_rate

COSTS = Path(__file__).resolve().parents[2] / "seed" / "costs"


def totals(who: str, subsidy: float, own: float):
    return compute(
        inputs_from(yaml.safe_load((COSTS / f"{who}.yaml").read_text(encoding="utf-8")), subsidy, own)
    )


def test_meena_numbers_to_the_rupee() -> None:
    """₹5,00,000 urban woman: PMEGP 25% subsidy, 5% own. Loan ₹3,50,000 at 11% for 60 months."""
    t = totals("meena", 25, 5)
    assert (t.total_cost, t.subsidy, t.own, t.loan) == (500000.0, 125000.0, 25000.0, 350000.0)
    assert t.emi == 7609.85  # 350000 · r(1+r)^60 / ((1+r)^60 − 1), r = 11/1200: worked by hand ≈ 7,610
    assert t.break_even_units == 225.0  # fixed ₹45,000 / (₹450 − ₹250) margin
    assert t.profit_month == 35000.0  # 400 units × ₹200 − ₹45,000
    assert t.dscr == 5.02


def test_ravi_numbers_to_the_rupee() -> None:
    """₹12,00,000 rural SC: PMEGP 35% subsidy, 5% own."""
    t = totals("ravi", 35, 5)
    assert (t.subsidy, t.own, t.loan) == (420000.0, 60000.0, 720000.0)
    assert (
        t.emi == 15654.54 and t.break_even_units == 3000.0 and t.profit_month == 120000.0 and t.dscr == 8.09
    )


def test_fatima_numbers_to_the_rupee() -> None:
    t = totals("fatima", 25, 5)
    assert (t.subsidy, t.own, t.loan) == (75000.0, 15000.0, 210000.0)
    assert t.emi == 4565.91 and t.break_even_units == 178.57 and t.dscr == 7.87


@pytest.mark.parametrize("who", ["meena", "ravi", "fatima"])
def test_cost_sheet_adds_up_to_the_persona_project_cost(who) -> None:
    costs = yaml.safe_load((COSTS / f"{who}.yaml").read_text(encoding="utf-8"))
    personas = {
        p["key"]: p for p in yaml.safe_load((COSTS.parent / "personas.yaml").read_text(encoding="utf-8"))
    }
    assert (
        sum(c["amount"] for c in costs["capex"]) + costs["working_capital"]
        == personas[who]["profile"]["project_cost"]
    )


def test_emi_matches_an_independent_formula() -> None:
    r = monthly_rate(11)
    loan, n = 350000, 60
    independent = loan * (r * (1 + r) ** n) / ((1 + r) ** n - 1)
    assert emi_for(loan, r, n) == pytest.approx(independent, rel=1e-12)
    assert round(emi_for(loan, r, n) * n - loan) == 106591  # total interest over the loan, whole rupees


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (0, "₹0"),
        (7, "₹7"),
        (999, "₹999"),
        (1000, "₹1,000"),
        (12345, "₹12,345"),
        (100000, "₹1,00,000"),
        (1234567, "₹12,34,567"),
        (1234567.5, "₹12,34,567.50"),
        (50000000, "₹5,00,00,000"),
        (-2500, "-₹2,500"),
        (0.5, "₹0.50"),
        (7609.85, "₹7,609.85"),
        (None, "-"),
    ],
)
def test_inr_uses_indian_digit_grouping(value, text) -> None:
    assert inr(value) == text
