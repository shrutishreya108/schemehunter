"""Profile keys the Brain may write, with the type each is coerced to. Matches rulebook/README.md."""

import re
from datetime import date
from typing import Any

from app.rules.engine import EDU_ORDER

MONEY = {"project_cost", "loan_amount", "family_income"}
BOOLS = {"in_default", "previous_tarun_repaid"}
ENUMS: dict[str, set[str]] = {
    "gender": {"female", "male", "other"},
    "category": {"SC", "ST", "OBC", "GEN"},
    "business_stage": {"new", "existing"},
    "sector": {"manufacturing", "service", "trading", "agri_allied", "education_institution", "shg"},
    "education_level": set(EDU_ORDER),
    "area": {"rural", "urban"},
    "enterprise_size": {"micro", "small", "medium"},
    "special_circumstance": {"widow", "disabled", "none"},
}
DATES = {"dob"}
TEXT = {"name_latin", "name_native", "district", "address_line", "business_description"}
EMAIL = {"email"}  # stored on the case (not in profile_answers): official mail CCs it
ALLOWED = MONEY | BOOLS | set(ENUMS) | DATES | TEXT | EMAIL


class VocabError(ValueError):
    pass


def coerce(key: str, value: Any) -> Any:
    """Normalise one answer; raises VocabError with a message the model can act on."""
    if key not in ALLOWED:
        raise VocabError(f"unknown profile key {key!r}; allowed: {', '.join(sorted(ALLOWED))}")
    if key in MONEY:
        if isinstance(value, bool):
            raise VocabError(f"{key} must be a whole number of rupees")
        try:
            amount = int(str(value).replace(",", "").strip()) if not isinstance(value, int) else value
        except ValueError:
            raise VocabError(f"{key} must be a whole number of rupees, e.g. 300000 for 3 lakh") from None
        if amount < 0:
            raise VocabError(f"{key} cannot be negative")
        return amount
    if key in BOOLS:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "yes", "haan", "हाँ", "y"}:
            return True
        if text in {"false", "no", "nahin", "नहीं", "n"}:
            return False
        raise VocabError(f"{key} must be true or false")
    if key in ENUMS:
        options = ENUMS[key]
        match = next((o for o in options if o.lower() == str(value).strip().lower()), None)
        if match is None:
            raise VocabError(f"{key} must be one of: {', '.join(sorted(options))}")
        return match
    if key in DATES:
        try:
            return date.fromisoformat(str(value)).isoformat()
        except ValueError:
            raise VocabError(f"{key} must be YYYY-MM-DD") from None
    text = str(value).strip()
    if key in EMAIL and not re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,200}\.[^@\s.]{2,}", text):
        raise VocabError("email must look like name@example.com")
    if not text or len(text) > 200:
        raise VocabError(f"{key} must be 1-200 characters")
    return text
