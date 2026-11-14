"""Project report math vs hand-calculated values (worked by hand, written here as literals)."""

import pytest

from app.finance.project_report import ReportInputs, compute, emi_for, months_paid_in_year

# Worked example (Meena-like tailoring unit):
#   total   = 3,00,000 + 1,00,000 capex + 1,00,000 WC          = 5,00,000
#   subsidy = 25% of 5,00,000                                    = 1,25,000
#   own     = 5%  of 5,00,000                                    =    25,000
#   loan    = 5,00,000 − 1,25,000 − 25,000                       = 3,50,000
#   r = 12/12/100 = 0.01; n = 60; (1.01)^60 = 1.8166967
#   EMI = 3,50,000 × 0.01 × 1.8166967 / 0.8166967 = 3,500 × (1 + 1/0.8166967)
#       = 3,500 × 2.2244445 = 7,785.56
#   margin = 50 − 30 = 20/unit; break-even = 20,000 / 20 = 1,000 units/month
#   profit/month = 1,500 × 20 − 20,000 = 10,000
#   month-1 interest = 3,50,000 × 0.01 = 3,500; DSCR = (10,000 + 3,500) / 7,785.56 = 1.73
#   Year table (10% growth), debt service = 7,785.556 × 12 = 93,426.68
#     Y1: 1,500 u → rev 9,00,000, var 5,40,000, fixed 2,40,000, op 1,20,000, DSCR 1.28
#     Y2: 1,650 u → rev 9,90,000, var 5,94,000, op 1,56,000, DSCR 1.67
#     Y3: 1,815 u → rev 10,89,000, var 6,53,400, op 1,95,600, DSCR 2.09
EXAMPLE = ReportInputs(
    capex=[300000, 100000],
    working_capital=100000,
    own_pct=5,
    subsidy_pct=25,
    annual_rate=12,
    tenure_months=60,
    units_per_month=1500,
    price=50,
    variable_cost=30,
    fixed_costs=20000,
    growth_pct=10,
)


def test_split_of_project_cost() -> None:
    t = compute(EXAMPLE)
    assert (t.total_cost, t.subsidy, t.own, t.loan) == (500000, 125000, 25000, 350000)


def test_emi_to_the_rupee() -> None:
    assert compute(EXAMPLE).emi == pytest.approx(7785.56, abs=0.01)


def test_break_even_profit_dscr() -> None:
    t = compute(EXAMPLE)
    assert t.break_even_units == 1000
    assert t.profit_month == 10000
    assert t.dscr == 1.73


def test_three_year_table() -> None:
    rows = compute(EXAMPLE).table
    assert [r.units_per_month for r in rows] == [1500, 1650, 1815]
    assert [r.revenue for r in rows] == [900000, 990000, 1089000]
    assert [r.operating_profit for r in rows] == [120000, 156000, 195600]
    assert all(r.debt_service == pytest.approx(93426.68, abs=0.05) for r in rows)
    assert [r.dscr for r in rows] == [1.28, 1.67, 2.09]


def test_zero_interest_emi_is_straight_split() -> None:
    assert emi_for(120000, 0, 12) == 10000


def test_loss_making_unit_has_no_break_even() -> None:
    inp = ReportInputs(
        capex=[100000],
        working_capital=0,
        own_pct=10,
        subsidy_pct=0,
        annual_rate=10,
        tenure_months=12,
        units_per_month=100,
        price=20,
        variable_cost=25,
        fixed_costs=1000,
    )
    assert compute(inp).break_even_units is None


def test_debt_service_stops_after_tenure() -> None:
    assert [months_paid_in_year(y, 18) for y in (1, 2, 3)] == [12, 6, 0]
    inp = ReportInputs(**{**EXAMPLE.__dict__, "tenure_months": 18})
    assert compute(inp).table[2].dscr is None


def test_fully_subsidised_project_has_no_loan() -> None:
    inp = ReportInputs(**{**EXAMPLE.__dict__, "own_pct": 50, "subsidy_pct": 50})
    t = compute(inp)
    assert (t.loan, t.emi, t.dscr) == (0, 0, None)
