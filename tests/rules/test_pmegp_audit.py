"""PMEGP rule audit (guideline: Revisedguidelines07.12.2023). Each test names the guideline page it relies on.

Financial values are checked against the subsidy table on PDF page 4. No rule here is marked verified: that needs
curator confirmation, and the tests check that the status stays "unverified".
"""

from pathlib import Path

import pytest
import yaml

from app.docs.requirements import resolve_documents
from app.rules.engine import evaluate
from app.rules.facts import build_facts, pmegp_special_category
from app.rules.loader import load_schemes
from app.rules.sources import is_complete

PMEGP = next(s for s in load_schemes() if s["id"] == "pmegp")
FOLDER = Path(__file__).resolve().parents[2] / "rulebook" / "sources" / "pmegp"


def benefit_for(**answers):
    facts = build_facts({"age": 30, "business_stage": "new", "sector": "service", **answers}, state_code="KA")
    return evaluate(PMEGP, facts)["benefit"]


# ---- subsidy and own contribution (PDF page 4 table, read from the rendered page)


@pytest.mark.parametrize(
    ("answers", "subsidy", "own"),
    [
        ({"area": "urban", "gender": "male", "category": "GEN"}, 15, 10),  # general, urban
        ({"area": "rural", "gender": "male", "category": "GEN"}, 25, 10),  # general, rural
        ({"area": "urban", "gender": "male", "category": "OBC"}, 25, 5),  # OBC is special (page 4)
        ({"area": "rural", "gender": "male", "category": "OBC"}, 35, 5),
        ({"area": "urban", "gender": "male", "category": "SC"}, 25, 5),
        ({"area": "rural", "gender": "male", "category": "ST"}, 35, 5),
        ({"area": "urban", "gender": "female", "category": "GEN"}, 25, 5),  # women are special (page 4)
        ({"area": "urban", "gender": "male", "category": "GEN", "special_circumstance": "disabled"}, 25, 5),
    ],
)
def test_subsidy_and_own_contribution_follow_the_guideline_table(answers, subsidy, own) -> None:
    b = benefit_for(**answers)
    assert b["subsidy_percent"] == subsidy
    assert b["own_contribution_percent"] == own


# ---- special category


@pytest.mark.parametrize(
    ("gender", "category", "circumstance", "expected"),
    [
        ("male", "OBC", None, True),
        ("male", "SC", None, True),
        ("male", "ST", None, True),
        ("female", "GEN", None, True),
        ("male", "GEN", "disabled", True),
        ("male", "GEN", None, False),
        ("male", None, None, None),  # not decidable yet: the rules ask
        (None, "GEN", None, None),
    ],
)
def test_pmegp_special_category_is_tri_state(gender, category, circumstance, expected) -> None:
    assert pmegp_special_category(gender, category, circumstance) is expected


def test_the_general_special_category_fact_is_unchanged_for_other_schemes() -> None:
    from app.rules.facts import special_category

    assert special_category("male", "OBC") is False  # other schemes keep their own meaning


# ---- age wording and verification status


def test_age_wording_is_kept_and_its_ambiguity_is_recorded() -> None:
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "rulebook" / "schemes" / "pmegp.yaml").read_text(encoding="utf-8"))
    age = next(r for r in raw["eligibility"]["all"] if r["id"] == "age")
    assert age["op"] == "gte" and age["value"] == 18  # not changed without a documented decision
    assert "above 18 years of age" in age["source_note"]
    assert "OPEN" in age["source_note"]


def test_no_rule_or_benefit_is_marked_verified() -> None:
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "rulebook" / "schemes" / "pmegp.yaml").read_text(encoding="utf-8"))
    flags = [r.get("verified") for r in raw["eligibility"]["all"]]
    flags += [raw["benefit"]["verified"], raw["edp"]["verified"]]
    flags += [e.get("verified") for e in raw["benefit_calc"]["subsidy_percent"] + raw["benefit_calc"]["own_contribution_percent"]]
    assert flags and all(f is False for f in flags)


def test_only_the_four_checked_rules_are_verified_and_the_rest_stay_unverified() -> None:
    raw = yaml.safe_load((FOLDER / "evidence.yaml").read_text(encoding="utf-8"))
    verified = {e["rule_id"] for e in raw["evidence"] if e["status"] == "verified"}
    assert verified == {"age", "new_only", "cost_cap", "education"}  # verbatim passages, checked by test_evidence_passages
    assert all(e["status"] == "unverified" for e in raw["evidence"] if e["rule_id"] not in verified)


def test_source_document_is_complete_once_the_retrieval_date_is_recorded() -> None:
    doc = yaml.safe_load((FOLDER / "source.yaml").read_text(encoding="utf-8"))["documents"][0]
    assert doc["official_url"].startswith("https://www.kviconline.gov.in/")
    assert str(doc["retrieval_date"]) == "2026-11-19"  # YAML parses the date
    assert is_complete({**doc, "sha256": "x"})


# ---- documents: scheme papers vs bank papers


def test_bank_papers_are_separate_from_scheme_papers_and_still_required() -> None:
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "rulebook" / "schemes" / "pmegp.yaml").read_text(encoding="utf-8"))
    assert raw["bank_documents"] == ["passbook", "quotation"]
    assert "passbook" not in raw["documents"] and "quotation" not in raw["documents"]
    needed = resolve_documents(PMEGP, {"category": "GEN", "sector": "service", "project_cost": 500000})
    assert "passbook" in needed and "quotation" in needed  # kept as bank requirements


def test_rural_area_certificate_is_listed_for_rural_projects_only() -> None:
    assert "rural_area_cert" in resolve_documents(PMEGP, {"area": "rural", "project_cost": 500000})
    assert "rural_area_cert" not in resolve_documents(PMEGP, {"area": "urban", "project_cost": 500000})


def test_edp_certificate_is_after_sanction_and_does_not_block_applying() -> None:
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "rulebook" / "schemes" / "pmegp.yaml").read_text(encoding="utf-8"))
    assert all("edp" not in d for d in raw["documents"])
    assert "after sanction" in raw["edp"]["certificate_due"]
    assert not any("edp" in d for d in resolve_documents(PMEGP, {"project_cost": 500000}))


# ---- EDP rules (guideline pages 9, 13, 18)


def test_edp_figures_are_as_stated_in_the_guideline() -> None:
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "rulebook" / "schemes" / "pmegp.yaml").read_text(encoding="utf-8"))
    edp = raw["edp"]
    assert edp["mandatory_for_subsidy"] is True
    assert edp["not_mandatory_up_to_cost"] == 200000  # "up to Rs. 2 Lakh"
    assert edp["duration_days_up_to_5l"] == 5 and edp["duration_days_above_5l"] == 10
    assert "10 days" in edp["exempt_if_trained"] and "60 hours" in edp["exempt_if_trained"]  # exact quote: evidence.yaml
    assert "30 working days" in edp["timing"]
