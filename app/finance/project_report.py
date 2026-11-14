"""Project report money math (LLD §13.1). Code computes every number; Claude only writes narrative.

All money in rupees. Outputs are rounded to paise (2 decimals) at the end, never mid-calculation.
"""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class ReportInputs:
    capex: list[float]
    working_capital: float
    own_pct: float  # % of total cost the entrepreneur puts in
    subsidy_pct: float  # from the scheme's benefit_calc
    annual_rate: float  # % per year
    tenure_months: int
    units_per_month: float
    price: float  # per unit
    variable_cost: float  # per unit
    fixed_costs: float  # per month
    growth_pct: float = 0.0  # yearly growth in units


@dataclass(frozen=True)
class YearRow:
    year: int
    units_per_month: float
    revenue: float
    variable_costs: float
    fixed_costs: float
    operating_profit: float
    debt_service: float
    dscr: float | None


@dataclass(frozen=True)
class ReportTotals:
    total_cost: float
    subsidy: float
    own: float
    loan: float
    emi: float
    break_even_units: float | None
    profit_month: float
    dscr: float | None
    table: list[YearRow] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def monthly_rate(annual_rate: float) -> float:
    return annual_rate / 12 / 100


def emi_for(loan: float, r: float, n: int) -> float:
    """Equal monthly instalment. Zero interest → straight split."""
    if loan <= 0:
        return 0.0
    if r == 0:
        return loan / n
    growth = (1 + r) ** n
    return loan * r * growth / (growth - 1)


def interest_part(emi: float, loan: float, r: float) -> float:
    """Interest inside the first EMI (loan × monthly rate). Used for the month-1 DSCR."""
    return min(loan * r, emi)


def break_even_units(fixed_costs: float, price: float, variable_cost: float) -> float | None:
    """Units per month to cover fixed costs. None if each unit loses money."""
    margin = price - variable_cost
    return fixed_costs / margin if margin > 0 else None


def months_paid_in_year(year: int, tenure_months: int) -> int:
    return max(0, min(12, tenure_months - 12 * (year - 1)))


def project_3_years(inp: ReportInputs, emi: float) -> list[YearRow]:
    rows = []
    for year in (1, 2, 3):
        units = inp.units_per_month * (1 + inp.growth_pct / 100) ** (year - 1)
        revenue = units * inp.price * 12
        variable = units * inp.variable_cost * 12
        fixed = inp.fixed_costs * 12
        operating = revenue - variable - fixed
        debt = emi * months_paid_in_year(year, inp.tenure_months)
        rows.append(
            YearRow(
                year=year,
                units_per_month=round(units, 2),
                revenue=round(revenue, 2),
                variable_costs=round(variable, 2),
                fixed_costs=round(fixed, 2),
                operating_profit=round(operating, 2),
                debt_service=round(debt, 2),
                dscr=round(operating / debt, 2) if debt else None,
            )
        )
    return rows


def compute(inp: ReportInputs) -> ReportTotals:
    total_cost = sum(inp.capex) + inp.working_capital
    subsidy = total_cost * inp.subsidy_pct / 100
    own = total_cost * inp.own_pct / 100
    loan = total_cost - subsidy - own
    r = monthly_rate(inp.annual_rate)
    emi = emi_for(loan, r, inp.tenure_months)
    margin = inp.price - inp.variable_cost
    profit_m = inp.units_per_month * margin - inp.fixed_costs
    dscr = (profit_m + interest_part(emi, loan, r)) / emi if emi else None
    be = break_even_units(inp.fixed_costs, inp.price, inp.variable_cost)
    return ReportTotals(
        total_cost=round(total_cost, 2),
        subsidy=round(subsidy, 2),
        own=round(own, 2),
        loan=round(loan, 2),
        emi=round(emi, 2),
        break_even_units=round(be, 2) if be is not None else None,
        profit_month=round(profit_m, 2),
        dscr=round(dscr, 2) if dscr is not None else None,
        table=project_3_years(inp, emi),
    )


def inr(value: float | None) -> str:
    """Indian digit grouping with the rupee sign: 1234567.5 → ₹12,34,567.50 (whole rupees drop the paise)."""
    if value is None:
        return "-"
    negative = value < 0
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    text = ",".join([*groups, tail]) if groups else tail
    if frac != "00":
        text += "." + frac
    return ("-" if negative else "") + "₹" + text


def inputs_from(costs: dict, subsidy_pct: float, own_pct: float) -> ReportInputs:
    """Build ReportInputs from a cost-inputs mapping (seed/costs/*.yaml shape) and the scheme's percentages."""
    return ReportInputs(
        capex=[float(c["amount"]) for c in costs["capex"]],
        working_capital=float(costs["working_capital"]),
        own_pct=float(own_pct),
        subsidy_pct=float(subsidy_pct),
        annual_rate=float(costs["annual_rate"]),
        tenure_months=int(costs["tenure_months"]),
        units_per_month=float(costs["units_per_month"]),
        price=float(costs["price"]),
        variable_cost=float(costs["variable_cost"]),
        fixed_costs=float(costs["fixed_costs"]),
        growth_pct=float(costs.get("growth_pct", 0)),
    )


def report_context(
    costs: dict, totals: ReportTotals, narrative: dict, applicant: str, scheme_name: dict
) -> dict:
    return {
        "costs": costs,
        "totals": totals.to_dict(),
        "narrative": narrative,
        "applicant": applicant,
        "scheme_name": scheme_name,
    }


def render_pdf(context: dict, lang: str = "hi") -> tuple[bytes, bool]:
    """project_report.html → (PDF bytes, fell_back_to_english). Needs WeasyPrint (see app/pdf/render.py)."""
    from app.pdf.render import render

    return render("project_report", context, lang)
