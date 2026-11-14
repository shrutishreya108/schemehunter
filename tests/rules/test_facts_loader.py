from datetime import date
from pathlib import Path

import pytest

from app.rules.facts import STATE_NAMES, build_facts, special_category, years_between
from app.rules.loader import SchemeError, load_scheme, load_schemes


def test_state_names_cover_all_states_and_uts() -> None:
    assert len(STATE_NAMES) == 36
    assert STATE_NAMES["KA"] == "Karnataka"


def test_state_name_is_derived_from_case_state_code() -> None:
    f = build_facts({"dob": "1991-04-12"}, state_code="KA", today=date(2026, 11, 16))
    assert (f["state_code"], f["state_name"], f["age"]) == ("KA", "Karnataka", 35)
    assert build_facts({})["state_name"] is None


def test_age_birthday_boundary() -> None:
    assert years_between("1971-11-16", date(2026, 11, 16)) == 55
    assert years_between("1971-11-17", date(2026, 11, 16)) == 54


@pytest.mark.parametrize(
    ("gender", "category", "expected"),
    [
        ("female", None, True),
        (None, "ST", True),
        ("male", "GEN", False),
        ("male", None, None),
        (None, None, None),
    ],
)
def test_special_category_is_tri_state(gender, category, expected) -> None:
    assert special_category(gender, category) is expected


def test_all_five_schemes_load_and_every_rule_is_unverified() -> None:
    schemes = load_schemes()
    assert [s["id"] for s in schemes] == ["cgtmse", "mudra", "pmegp", "stand_up_india", "udyogini_ka"]
    for s in schemes:
        assert all(r["verified"] is False and r["source_note"] for r in s["eligibility"]["all"])
        for entries in (s.get("benefit_calc") or {}).values():
            assert all(e["verified"] is False and e["source_note"] for e in entries)
        assert len(s["_hash"]) == 64


BAD_SCHEMES = {
    "unknown_op": "{ id: age, fact: age, op: approx, value: 18, verified: false, source_note: x }",
    "no_verified": "{ id: age, fact: age, op: gte, value: 18, source_note: x }",
    "bad_level": "{ id: e, fact: education_level, op: gte_level, value: '9th', verified: false, source_note: x }",
}


@pytest.mark.parametrize("name", list(BAD_SCHEMES))
def test_loader_rejects_bad_rules(tmp_path: Path, name: str) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "id: bad\nname: {en: Bad, hi: Bad}\nlevel: central\nbenefit: {type: loan}\n"
        f"apply_mode: bank\nstatus_mode: manual\neligibility:\n  all:\n    - {BAD_SCHEMES[name]}\n"
    )
    with pytest.raises(SchemeError):
        load_scheme(path)
