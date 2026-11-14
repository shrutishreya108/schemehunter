"""Claude cost accounting: price lookup and a per-run usage meter. Pure code, no API calls."""

from dataclasses import dataclass, field
from typing import Any

from app.config import PRICE_TABLE

PER_MILLION = 1_000_000


def price_for(model: str) -> dict[str, float]:
    """Longest matching prefix in PRICE_TABLE. Unknown models are billed at the most expensive known rates."""
    matches = [k for k in PRICE_TABLE if model.startswith(k)]
    if matches:
        return PRICE_TABLE[max(matches, key=len)]
    return {
        kind: max(p[kind] for p in PRICE_TABLE.values())
        for kind in ("input", "output", "cache_write", "cache_read")
    }


def usage_tokens(usage: Any) -> dict[str, int]:
    """Normalise an SDK usage object (or dict) to our four token counts."""
    get = usage.get if isinstance(usage, dict) else lambda k, d=None: getattr(usage, k, d)
    return {
        "input": int(get("input_tokens", 0) or 0),
        "output": int(get("output_tokens", 0) or 0),
        "cache_read": int(get("cache_read_input_tokens", 0) or 0),
        "cache_write": int(get("cache_creation_input_tokens", 0) or 0),
    }


def cost_usd(model: str, tokens: dict[str, int]) -> float:
    """`input` is uncached input only (the API reports cache reads/writes separately)."""
    p = price_for(model)
    return sum(tokens.get(kind, 0) * p[kind] for kind in p) / PER_MILLION


@dataclass
class UsageMeter:
    """Accumulates every Claude call made during one run (loop steps, explain_match, scanner)."""

    calls: int = 0
    tokens: dict[str, int] = field(
        default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    )
    cost: float = 0.0
    by_model: dict[str, float] = field(default_factory=dict)

    def add(self, model: str, usage: Any) -> float:
        t = usage_tokens(usage)
        c = cost_usd(model, t)
        self.calls += 1
        for k, v in t.items():
            self.tokens[k] += v
        self.cost += c
        self.by_model[model] = self.by_model.get(model, 0.0) + c
        return c
