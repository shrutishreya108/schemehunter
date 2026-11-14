"""ID masking and name matching (LLD §12.2–12.3). Second layer after the prompt's "last 4 digits only"."""

import re
from typing import Any

from rapidfuzz import fuzz

# 12 digits as 4-4-4, with optional single space or hyphen separators. Keeps the last four.
AADHAAR_LIKE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?(\d{4})\b")
NAME_MATCH_THRESHOLD = 85


def mask_ids(s: str) -> str:
    return AADHAAR_LIKE.sub(r"XXXX XXXX \1", s)


def mask_fields(value: Any) -> Any:
    """Apply mask_ids to every string inside dicts/lists."""
    if isinstance(value, str):
        return mask_ids(value)
    if isinstance(value, dict):
        return {k: mask_fields(v) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_fields(v) for v in value]
    return value


def name_mismatch(known_names: list[str], fields: dict) -> bool:
    """True if this document's Latin name differs (token_set_ratio < 85) from any name already on file."""
    name = (fields.get("name_latin") or "").strip()
    if not name:
        return False
    return any(
        fuzz.token_set_ratio(name.casefold(), other.casefold()) < NAME_MATCH_THRESHOLD
        for other in known_names
        if other and other.strip()
    )
