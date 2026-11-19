"""Matching discovered schemes to the rulebook (integration §4). Exact names and aliases only; no guessing.

A discovered scheme gets an internal id only when exactly one rulebook scheme has that name or alias. Two schemes
that match the same rulebook scheme are duplicates: they are shown once, and the duplicate is counted.
"""

import re

from app.discovery.models import NormalizedScheme


def norm(text: str | None) -> str:
    t = re.sub(r"[^\w\s]", " ", (text or "").casefold())
    return re.sub(r"\s+", " ", t).strip()


def rulebook_names(scheme: dict) -> set[str]:
    names = {norm(scheme["name"].get("en")), norm(scheme["name"].get("hi")), norm(scheme["id"])}
    names |= {norm(a) for a in scheme.get("aliases") or []}
    return {n for n in names if n}


def match_internal(item: NormalizedScheme, rulebook: list[dict]) -> str | None:
    if item.internal_id:
        return item.internal_id
    candidates = {norm(item.name)} | {norm(a) for a in item.aliases}
    hits = [s["id"] for s in rulebook if candidates & rulebook_names(s)]
    return hits[0] if len(hits) == 1 else None


def dedupe(items: list[NormalizedScheme], rulebook: list[dict]) -> tuple[list[NormalizedScheme], int]:
    """Attach internal ids, then keep one entry per key and one per matched rulebook scheme."""
    seen_keys: set[str] = set()
    seen_internal: set[str] = set()
    kept: list[NormalizedScheme] = []
    duplicates = 0
    for item in items:
        internal = match_internal(item, rulebook)
        item = NormalizedScheme(**{**item.__dict__, "internal_id": internal})
        if item.key in seen_keys or (internal and internal in seen_internal):
            duplicates += 1
            continue
        seen_keys.add(item.key)
        if internal:
            seen_internal.add(internal)
        kept.append(item)
    return kept, duplicates
