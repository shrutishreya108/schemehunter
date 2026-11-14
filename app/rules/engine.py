"""Rules engine (LLD §6.3). Decides eligibility the same way every time. No AI here.

A node is one of:
  {"all": [node, ...]}                     every child must pass
  {"any": [node, ...]}                     at least one child must pass
  {"when": node, "then": node}             if `when` fails the node passes; else `then` decides
  {"fact": name, "op": op, "value": v}     a leaf comparison

Results are three-valued: PASS, FAIL, UNKNOWN (a fact the leaf needs is missing).
"""

from collections.abc import Iterator
from typing import Any

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
RESULT_NAMES = {PASS: "FIT", FAIL: "NO_FIT", UNKNOWN: "NEEDS_INFO"}

EDU_ORDER = ["none", "5th", "8th", "10th", "12th", "graduate"]

OPS = {
    "eq": lambda a, b: a == b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lte": lambda a, b: a <= b,
    "gte_level": lambda a, b: EDU_ORDER.index(a) >= EDU_ORDER.index(b),
}


class RuleError(ValueError):
    """A rule node is malformed or a fact has a value the operator cannot compare."""


def combine_all(rs: list[str]) -> str:
    return FAIL if FAIL in rs else (UNKNOWN if UNKNOWN in rs else PASS)


def combine_any(rs: list[str]) -> str:
    return PASS if PASS in rs else (UNKNOWN if UNKNOWN in rs else FAIL)


def eval_leaf(node: dict, facts: dict) -> str:
    v = facts.get(node["fact"])
    if v is None:
        return UNKNOWN
    try:
        return PASS if OPS[node["op"]](v, node["value"]) else FAIL
    except (TypeError, ValueError) as exc:
        raise RuleError(f"cannot apply {node['op']} to fact {node['fact']}={v!r}") from exc


def eval_node(node: dict, facts: dict) -> str:
    if "all" in node:
        return combine_all([eval_node(n, facts) for n in node["all"]])
    if "any" in node:
        return combine_any([eval_node(n, facts) for n in node["any"]])
    if "when" in node:
        c = eval_node(node["when"], facts)
        if c == FAIL:
            return PASS
        if c == UNKNOWN:
            return UNKNOWN
        return eval_node(node["then"], facts)
    if "fact" in node:
        return eval_leaf(node, facts)
    raise RuleError(f"node has no all/any/when/fact: {node!r}")


def missing_for(node: dict, facts: dict) -> set[str]:
    """Facts whose absence makes this node UNKNOWN, following only the branches that matter.

    all/any: only children that are UNKNOWN. when/then: if `when` is UNKNOWN, only its facts (the `then`
    facts may never be needed); if `when` passes, only `then`'s; if it fails, nothing.
    """
    if "fact" in node:
        return {node["fact"]} if facts.get(node["fact"]) is None else set()
    if "when" in node:
        c = eval_node(node["when"], facts)
        if c == UNKNOWN:
            return missing_for(node["when"], facts)
        return missing_for(node["then"], facts) if c == PASS else set()
    children = node.get("all") or node.get("any") or []
    return {f for child in children if eval_node(child, facts) == UNKNOWN for f in missing_for(child, facts)}


def leaves(node: Any) -> Iterator[dict]:
    """Every leaf comparison under a node (or a whole scheme)."""
    if isinstance(node, dict):
        if "fact" in node:
            yield node
            return
        for key in ("eligibility", "all", "any", "when", "then"):
            if key in node:
                yield from leaves(node[key])
    elif isinstance(node, list):
        for item in node:
            yield from leaves(item)


def first_rule_with(per_rule: dict[str, str], outcome: str) -> str | None:
    return next((rule_id for rule_id, r in per_rule.items() if r == outcome), None)


def deciding_rule(per_rule: dict[str, str]) -> str | None:
    """NO_FIT: first failing rule. NEEDS_INFO: first unknown rule. FIT: None (every rule passed)."""
    return first_rule_with(per_rule, FAIL) or first_rule_with(per_rule, UNKNOWN)


def calc_value(entries: list[dict], facts: dict) -> Any:
    """First entry whose `when` passes wins; `default` applies if reached.

    An UNKNOWN `when` before any match means the value can't be known yet → None.
    """
    for entry in entries:
        if "default" in entry:
            return entry["default"]
        c = eval_node(entry["when"], facts)
        if c == PASS:
            return entry["value"]
        if c == UNKNOWN:
            return None
    return None


def calc_benefit(benefit_calc: dict | None, facts: dict) -> dict[str, Any]:
    return {key: calc_value(entries, facts) for key, entries in (benefit_calc or {}).items()}


def evaluate(scheme: dict, facts: dict) -> dict:
    """Evaluate one scheme against facts.

    Returns result (FIT/NO_FIT/NEEDS_INFO), per-rule results, the deciding rule and its page,
    facts still needed (only on the branches that made a rule UNKNOWN; see missing_for), and the computed benefit.
    """
    rules = scheme["eligibility"]["all"]
    per_rule = {r["id"]: eval_node(r, facts) for r in rules}
    overall = combine_all(list(per_rule.values()))
    decider = deciding_rule(per_rule)
    pages = {r["id"]: r.get("page") for r in rules}
    return {
        "scheme_id": scheme["id"],
        "result": RESULT_NAMES[overall],
        "rules": per_rule,
        "deciding_rule": decider,
        "deciding_page": pages.get(decider) if decider else None,
        "missing_facts": sorted(
            {f for r in rules if per_rule[r["id"]] == UNKNOWN for f in missing_for(r, facts)}
        ),
        "benefit": calc_benefit(scheme.get("benefit_calc"), facts),
    }


def applies_to_state(scheme: dict, state_code: str | None) -> bool:
    """Central schemes apply everywhere; state schemes only in their state."""
    return scheme.get("level") == "central" or scheme.get("state") == state_code


def evaluate_all(schemes: list[dict], facts: dict) -> list[dict]:
    """match_schemes core: every central scheme plus the case's state schemes, in file order."""
    return [evaluate(s, facts) for s in schemes if applies_to_state(s, facts.get("state_code"))]
