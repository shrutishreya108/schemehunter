"""Verification status, source metadata and citations (reliability milestone 1, sections 2 and 3).

The eligibility results themselves are not changed by any of this; these tests check the status that is shown
next to them, and that an unverified or incomplete source is never cited as verified.
"""

import pytest

from app.brain.tools import answer_question as qa
from app.rules import verification as v
from app.rules.loader import load_schemes
from app.rules.sources import is_complete, sync_sources, verified_rule_ids

PMEGP = next(s for s in load_schemes() if s["id"] == "pmegp")


# ---------------------------------------------------------------- status (pure)


def test_unverified_rules_give_a_preliminary_status() -> None:
    assert v.status(PMEGP, "FIT", set()) == v.PRELIMINARY
    assert v.status(PMEGP, "NO_FIT", set()) == v.PRELIMINARY


def test_needs_info_is_insufficient_not_a_verdict() -> None:
    assert v.status(PMEGP, "NEEDS_INFO", set()) == v.INSUFFICIENT
    assert v.status(PMEGP, "NEEDS_INFO", set(v.material_rules(PMEGP))) == v.INSUFFICIENT


def test_every_material_rule_verified_gives_verified() -> None:
    assert v.status(PMEGP, "FIT", set(v.material_rules(PMEGP))) == v.VERIFIED


def test_one_unverified_rule_keeps_the_whole_result_preliminary() -> None:
    ids = set(v.material_rules(PMEGP)) - {"benefit"}
    assert v.status(PMEGP, "FIT", ids) == v.PRELIMINARY
    assert v.unverified_rules(PMEGP, ids) == ["benefit"]


def test_a_preliminary_fit_never_uses_the_plain_headline() -> None:
    assert v.result_label_key("FIT", v.PRELIMINARY) == "result_FIT_PRELIM"
    assert v.result_label_key("FIT", v.VERIFIED) == "result_FIT"
    assert v.result_label_key("NEEDS_INFO", v.INSUFFICIENT) == "result_NEEDS_INFO"


def test_warnings_exist_in_both_languages() -> None:
    assert "not yet been checked" in v.warning(v.PRELIMINARY, "en")
    assert v.warning(v.PRELIMINARY, "hi") and v.warning(v.VERIFIED, "en") is None


# ---------------------------------------------------------------- source metadata (database)


def write_sources(root, pdf_bytes: bytes | None, source_yaml: str, evidence_yaml: str) -> None:
    folder = root / "pmegp"
    folder.mkdir(parents=True)
    (folder / "source.yaml").write_text(source_yaml, encoding="utf-8")
    (folder / "evidence.yaml").write_text(evidence_yaml, encoding="utf-8")
    if pdf_bytes is not None:
        (folder / "guidelines.pdf").write_bytes(pdf_bytes)


COMPLETE_SOURCE = """
documents:
  - id: pmegp-test-doc
    title: "Test guideline"
    official_url: "https://example.org/guideline.pdf"
    issuing_authority: "Test authority"
    retrieval_date: 2026-01-01
    version: "1.0"
    local_path: guidelines.pdf
"""


def test_sync_stores_hash_and_metadata_and_never_invents_missing_fields(seeded, conn, tmp_path) -> None:
    import hashlib

    write_sources(tmp_path, b"%PDF-1.4 test", COMPLETE_SOURCE, "evidence: []\n")
    counts = sync_sources(conn, root=tmp_path)
    assert counts["documents"] == 1
    row = conn.execute("select * from source_documents where id = 'pmegp-test-doc'").fetchone()
    assert row["sha256"] == hashlib.sha256(b"%PDF-1.4 test").hexdigest()
    assert row["title"] == "Test guideline" and row["version"] == "1.0"
    assert row["publication_date"] is None and row["effective_date"] is None  # not given: stays unavailable
    assert is_complete(dict(row))


def test_a_document_without_its_file_has_no_hash_and_is_not_complete(seeded, conn, tmp_path) -> None:
    write_sources(tmp_path, None, COMPLETE_SOURCE, "evidence: []\n")
    sync_sources(conn, root=tmp_path)
    row = conn.execute("select * from source_documents where id = 'pmegp-test-doc'").fetchone()
    assert row["sha256"] is None and not is_complete(dict(row))


def test_verified_evidence_without_a_complete_source_is_not_a_verified_rule(seeded, conn, tmp_path) -> None:
    evidence = """
evidence:
  - rule_id: age
    source_id: pmegp-test-doc
    page: 3
    passage: "Age 18 years or above"
    status: verified
    last_verified: 2026-01-02
"""
    write_sources(tmp_path, None, COMPLETE_SOURCE, evidence)  # no file, so no hash, so not complete
    sync_sources(conn, root=tmp_path)
    assert verified_rule_ids(conn, "pmegp") == set()


def test_verified_evidence_with_a_complete_source_counts(seeded, conn, tmp_path) -> None:
    evidence = """
evidence:
  - rule_id: age
    source_id: pmegp-test-doc
    page: 3
    passage: "Age 18 years or above"
    status: verified
    last_verified: 2026-01-02
  - rule_id: cost_cap
    source_id: pmegp-test-doc
    page: 4
    passage: "not checked"
    status: unverified
"""
    write_sources(tmp_path, b"%PDF-1.4 test", COMPLETE_SOURCE, evidence)
    sync_sources(conn, root=tmp_path)
    assert verified_rule_ids(conn, "pmegp") == {"age"}


def test_evidence_pointing_at_an_unknown_source_is_skipped(seeded, conn, tmp_path) -> None:
    evidence = """
evidence:
  - rule_id: age
    source_id: not-in-source-yaml
    page: 1
    passage: "x"
    status: verified
"""
    write_sources(tmp_path, b"%PDF-1.4 test", COMPLETE_SOURCE, evidence)
    counts = sync_sources(conn, root=tmp_path)
    assert counts["skipped_evidence"] == 1 and verified_rule_ids(conn, "pmegp") == set()


def test_guideline_text_without_source_details_is_an_estimate_never_verified(
    seeded, conn, tmp_path, monkeypatch
) -> None:
    page = tmp_path / "01.txt"
    page.write_text("Age must be 18 years or more.", encoding="utf-8")
    monkeypatch.setattr(qa, "page_files", lambda sid, root=None: [(1, page)])
    from app.brain.tools.base import Ctx

    ctx = Ctx(case_id=seeded["meena"], conn=conn, schemes=load_schemes(), language="en")
    sources = qa.guideline_sources(ctx, ["pmegp"])
    assert sources and all(s["label"] != "verified" for s in sources)
    assert "not cited as verified" in sources[0]["text"]


def test_guideline_text_with_complete_source_is_cited_with_version_and_hash(
    seeded, conn, tmp_path, monkeypatch
) -> None:
    page = tmp_path / "01.txt"
    page.write_text("Age must be 18 years or more.", encoding="utf-8")
    write_sources(tmp_path / "root", b"%PDF-1.4 test", COMPLETE_SOURCE, "evidence: []\n")
    sync_sources(conn, root=tmp_path / "root")
    monkeypatch.setattr(qa, "page_files", lambda sid, root=None: [(1, page)])
    from app.brain.tools.base import Ctx

    ctx = Ctx(case_id=seeded["meena"], conn=conn, schemes=load_schemes(), language="en")
    sources = qa.guideline_sources(ctx, ["pmegp"])
    assert sources[0]["label"] == "verified"
    assert "Test guideline, version 1.0" in sources[0]["text"] and "page 1" in sources[0]["text"]


@pytest.mark.parametrize(
    "field", ["title", "official_url", "issuing_authority", "version", "retrieval_date", "sha256"]
)
def test_each_required_source_field_is_needed_for_a_verified_citation(field) -> None:
    complete = {
        "title": "t",
        "official_url": "u",
        "issuing_authority": "a",
        "version": "1",
        "retrieval_date": "d",
        "sha256": "h",
    }
    assert is_complete(complete)
    assert not is_complete({**complete, field: None})
