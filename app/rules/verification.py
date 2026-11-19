"""Verification status of a match (reliability §2). Pure code: it never changes an eligibility result.

- verified:      every material rule has current official evidence.
- preliminary:   the result comes from rules that are not yet checked against official guidelines.
- insufficient:  not enough information to decide (NEEDS_INFO).
"""

from app.rules.engine import leaves

VERIFIED = "verified"
PRELIMINARY = "preliminary"
INSUFFICIENT = "insufficient"

WARNINGS = {
    PRELIMINARY: {
        "en": "Preliminary: based on rules that have not yet been checked against current official guidelines.",
        "hi": "प्रारंभिक: ये नियम अभी वर्तमान आधिकारिक दिशानिर्देशों से जाँचे नहीं गए हैं।",
    },
    INSUFFICIENT: {
        "en": "Not enough information to decide yet.",
        "hi": "फ़ैसला करने के लिए अभी पर्याप्त जानकारी नहीं है।",
    },
}


def material_rules(scheme: dict) -> list[str]:
    """Every rule that can change a result: the top-level eligibility rules and the benefit."""
    ids = [r["id"] for r in scheme["eligibility"]["all"]]
    ids.append("benefit")
    return ids


def unverified_rules(scheme: dict, verified_ids: set[str]) -> list[str]:
    return [rid for rid in material_rules(scheme) if rid not in verified_ids]


def status(scheme: dict, result: str, verified_ids: set[str]) -> str:
    if result == "NEEDS_INFO":
        return INSUFFICIENT
    return PRELIMINARY if unverified_rules(scheme, verified_ids) else VERIFIED


def result_label_key(result: str, verification: str) -> str:
    """Translation key for the headline. A FIT or NO_FIT from unverified rules never reads as final."""
    if verification == PRELIMINARY and result in ("FIT", "NO_FIT"):
        return f"result_{result}_PRELIM"
    return f"result_{result}"


def warning(verification: str, lang: str) -> str | None:
    text = WARNINGS.get(verification)
    return None if text is None else text.get(lang) or text["en"]


def rule_ids_in(scheme: dict) -> set[str]:
    """Leaf rule ids, for checking that evidence names real rules."""
    return {leaf["id"] for leaf in leaves(scheme["eligibility"]) if "id" in leaf} | {"benefit"}
