"""Expected Claude spend for one persona journey and for the 6-minute demo, from app.config.PRICE_TABLE.

    python scripts/estimate_costs.py

Token counts below are planning assumptions (not measurements). Replace them with real numbers from
/admin/runs after the first real runs; the structure (what is cached, what isn't) follows app/brain/claude.py.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import PRICE_TABLE, get_settings
from app.costs import cost_usd

CREDIT_USD = 100.0

# Cached prefix sent with every loop step: system text (~700) + Rule Book summary (~600) + 5 tool schemas (~1,400).
LOOP_PREFIX = 2_700
CASE_FILE = 1_500  # per-run, uncached
STEP_GROWTH = 400  # each step re-sends the previous assistant turn + tool results (uncached)
STEP_OUTPUT = 200
PAGES_PER_EXPLAIN = 6
PAGE_TOKENS = 700


@dataclass
class Call:
    label: str
    model: str
    input: int
    output: int
    cache_read: int = 0
    cache_write: int = 0
    count: int = 1

    @property
    def cost(self) -> float:
        one = cost_usd(
            self.model,
            {
                "input": self.input,
                "output": self.output,
                "cache_read": self.cache_read,
                "cache_write": self.cache_write,
            },
        )
        return one * self.count


def run_calls(label: str, steps: int, runs: int, reason: str, first_writes_cache: bool = True) -> list[Call]:
    """`runs` agent runs of `steps` steps. The first step of the first run writes the cache; the rest read it."""
    calls = []
    for step in range(steps):
        uncached = CASE_FILE + STEP_GROWTH * step
        if step == 0 and first_writes_cache:
            calls.append(
                Call(f"{label} step 1 (cache write)", reason, uncached, STEP_OUTPUT, cache_write=LOOP_PREFIX)
            )
            calls.append(
                Call(
                    f"{label} step 1 (later runs)",
                    reason,
                    uncached,
                    STEP_OUTPUT,
                    cache_read=LOOP_PREFIX,
                    count=runs - 1,
                )
            )
        else:
            calls.append(
                Call(
                    f"{label} step {step + 1}",
                    reason,
                    uncached,
                    STEP_OUTPUT,
                    cache_read=LOOP_PREFIX,
                    count=runs,
                )
            )
    return [c for c in calls if c.count > 0]


def explain_calls(n: int, reason: str, cited: bool) -> list[Call]:
    if not cited:
        return [Call("explain_match (no pages yet)", reason, 700, 400, cache_read=300, count=n)]
    docs = PAGES_PER_EXPLAIN * PAGE_TOKENS
    return [
        Call("explain_match cited (first scheme)", reason, 500, 500, cache_write=docs + 300),
        Call(
            "explain_match cited (other schemes)",
            reason,
            500,
            500,
            cache_write=docs,
            cache_read=300,
            count=n - 1,
        ),
    ]


def journey(reason: str, cheap: str, cited: bool = True) -> list[Call]:
    """One persona from first message to a resubmitted application."""
    return [
        *run_calls("onboarding run", steps=3, runs=5, reason=reason),
        *explain_calls(3, reason, cited),
        Call("document vision check (Aadhaar/passbook)", reason, 2_100, 300, count=2),
        Call("interpret forwarded SMS", cheap, 600, 150, count=3),
        *run_calls("chase / follow-up run", steps=3, runs=2, reason=reason, first_writes_cache=False),
        Call("draft_message (forced schema)", reason, 1_500, 400, count=2),
        Call("project report narrative", reason, 2_000, 1_200),
        Call("outbound unknown-scheme scan", cheap, 150, 10, count=15),
    ]


def demo(reason: str, cheap: str, cited: bool = True) -> list[Call]:
    """LLD §23 demo from make demo-reset: only live onboarding + live document checks hit the API.

    Ravi's explanation, Fatima's analysis and Meena's narrative are seeded (seed/artifacts/).
    """
    return [
        *run_calls("Meena live turn", steps=3, runs=3, reason=reason),
        *explain_calls(2, reason, cited),
        Call("live document check (blurry + clean)", reason, 2_100, 300, count=2),
        Call("outbound unknown-scheme scan", cheap, 150, 10, count=6),
    ]


def table(title: str, calls: list[Call]) -> float:
    print(f"\n{title}")
    print(f"  {'call':<44} {'model':<18} {'n':>3} {'USD':>9}")
    for c in calls:
        print(f"  {c.label:<44} {c.model:<18} {c.count:>3} {c.cost:>9.4f}")
    total = sum(c.cost for c in calls)
    print(f"  {'TOTAL':<44} {'':<18} {'':>3} {total:>9.4f}")
    return total


def main() -> int:
    s = get_settings()
    reason, cheap = s.MODEL_REASON, s.MODEL_CHEAP
    print("Price table (USD per million tokens):")
    for model, p in PRICE_TABLE.items():
        print(
            f"  {model:<20} in {p['input']:.2f}  out {p['output']:.2f}  cache-write {p['cache_write']:.2f}  "
            f"cache-read {p['cache_read']:.2f}"
        )
    j = table("One full persona journey (guideline pages present, citations on)", journey(reason, cheap))
    j0 = table(
        "Same journey before PDFs land (explanations without citations)", journey(reason, cheap, cited=False)
    )
    d = table(
        "6-minute demo script (seeded artifacts; live onboarding + live doc checks only)", demo(reason, cheap)
    )
    print(f"\nSummary: journey ≈ ${j:.2f} (≈ ${j0:.2f} without pages) · demo ≈ ${d:.2f} per run-through")
    print(
        f"  ${CREDIT_USD:.0f} credit ≈ {int(CREDIT_USD // j)} journeys, or {int(CREDIT_USD // d)} demo rehearsals"
    )
    print(
        f"  Daily guard: DAILY_BUDGET_USD={s.DAILY_BUDGET_USD:.2f}, MAX_RUNS_PER_CASE_PER_DAY={s.MAX_RUNS_PER_CASE_PER_DAY}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
