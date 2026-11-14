"""build_facts(): profile answers + derived facts (LLD §6.2). Pure code, no AI."""

from datetime import date
from typing import Any

# ISO 3166-2:IN codes (2023 revision): 28 states + 8 union territories.
STATE_NAMES: dict[str, str] = {
    # States
    "AP": "Andhra Pradesh",
    "AR": "Arunachal Pradesh",
    "AS": "Assam",
    "BR": "Bihar",
    "CG": "Chhattisgarh",
    "GA": "Goa",
    "GJ": "Gujarat",
    "HR": "Haryana",
    "HP": "Himachal Pradesh",
    "JH": "Jharkhand",
    "KA": "Karnataka",
    "KL": "Kerala",
    "MP": "Madhya Pradesh",
    "MH": "Maharashtra",
    "MN": "Manipur",
    "ML": "Meghalaya",
    "MZ": "Mizoram",
    "NL": "Nagaland",
    "OD": "Odisha",
    "PB": "Punjab",
    "RJ": "Rajasthan",
    "SK": "Sikkim",
    "TN": "Tamil Nadu",
    "TG": "Telangana",
    "TR": "Tripura",
    "UP": "Uttar Pradesh",
    "UK": "Uttarakhand",
    "WB": "West Bengal",
    # Union territories
    "AN": "Andaman and Nicobar Islands",
    "CH": "Chandigarh",
    "DH": "Dadra and Nagar Haveli and Daman and Diu",
    "DL": "Delhi",
    "JK": "Jammu and Kashmir",
    "LA": "Ladakh",
    "LD": "Lakshadweep",
    "PY": "Puducherry",
}

SPECIAL_CATEGORY_CASTES = {"SC", "ST"}


def years_between(start: date | str, end: date) -> int:
    """Whole years from start to end (age on `end`)."""
    if isinstance(start, str):
        start = date.fromisoformat(start)
    years = end.year - start.year
    if (end.month, end.day) < (start.month, start.day):
        years -= 1
    return years


def special_category(gender: str | None, category: str | None) -> bool | None:
    """Woman or SC/ST. Tri-state: None when it cannot be decided yet."""
    if gender == "female" or category in SPECIAL_CATEGORY_CASTES:
        return True
    if gender is None or category is None:
        return None
    return False


def build_facts(answers: dict[str, Any], state_code: str | None = None, today: date | None = None) -> dict:
    """Copy answers and add derived facts: age, special_category, state_code, state_name.

    state_code comes from cases.state_code (preferred) or an answer of the same name.
    """
    today = today or date.today()
    f = dict(answers)
    dob = f.get("dob")
    f["age"] = years_between(dob, today) if dob else None
    f["special_category"] = special_category(f.get("gender"), f.get("category"))
    code = state_code or f.get("state_code")
    f["state_code"] = code
    f["state_name"] = STATE_NAMES.get(code) if code else None
    return f
