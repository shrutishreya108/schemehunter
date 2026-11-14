"""Slice 2: seed is idempotent and fast; build_case_file and GET /cases/{id} return the case file."""

import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.brain.casefile import assemble_case_file, benefit_summary, build_case_file
from app.db import connect
from app.seed import case_id_for, seed

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)


def test_assemble_case_file_shape_without_db() -> None:
    cf = assemble_case_file(
        case={"language": "hi", "state_code": "KA"},
        answers=[{"key": "name_latin", "value": "Meena"}, {"key": "note", "value": "aadhaar 1234 5678 9012"}],
        documents=[
            {"id": 1, "doc_type": "aadhaar", "check_status": "RETAKE", "check_result": {"issue": "blur"}}
        ],
        matches=[
            {
                "scheme_id": "pmegp",
                "result": "FIT",
                "missing_facts": [],
                "benefit": {"subsidy_percent": 25, "own_contribution_percent": 5},
            },
            {"scheme_id": "mudra", "result": "NEEDS_INFO", "missing_facts": ["loan_amount"], "benefit": {}},
        ],
        applications=[
            {
                "id": "a",
                "scheme_id": "pmegp",
                "state": "UNDER_REVIEW",
                "ref_number": "R",
                "deadline": None,
                "last_movement_at": NOW - timedelta(days=11),
            }
        ],
        pending=[],
        events=[
            {"actor": "agent", "summary": {"hi": "दूसरा", "en": "second"}, "created_at": NOW},
            {
                "actor": "user",
                "summary": {"hi": "पहला", "en": "first"},
                "created_at": NOW - timedelta(hours=1),
            },
        ],
        now=NOW,
    )
    assert cf["today"] == "2026-11-16" and cf["missing_facts"] == ["loan_amount"]
    assert cf["profile"]["note"] == "aadhaar XXXX XXXX 9012"  # never a full ID number
    assert cf["documents"][0]["issue"] == "blur"
    assert cf["matches"][0]["benefit_summary"] == "subsidy 25%, own 5%"
    assert cf["applications"][0]["days_since_movement"] == 11
    assert cf["recent_events"][0].endswith("user: पहला")  # oldest first, user's language


def test_benefit_summary() -> None:
    assert benefit_summary({"mudra_category": "Kishore"}) == "Kishore"
    assert benefit_summary({"subsidy_percent": None}) == ""


@pytest.mark.db
def test_seed_is_idempotent_and_fast(settings_db: str) -> None:
    started = time.monotonic()
    first = seed(settings_db, reset=True, now=NOW)
    assert time.monotonic() - started < 10
    counts = lambda: _counts(settings_db)
    before = counts()
    second = seed(settings_db, now=NOW)
    assert first == second and counts() == before
    assert before["cases"] == 3 and before["schemes"] == 5 and before["applications"] == 2
    assert before["events"] == 25 and before["jobs"] == 2  # Fatima's analysis is seeded, not a job
    assert before["artifacts"] == 5  # rejection analysis + report narrative + one cost sheet per persona (3)


def _counts(url: str) -> dict:
    with connect(url) as conn:
        return {
            t: conn.execute(f"select count(*) as n from {t}").fetchone()["n"]
            for t in (
                "cases",
                "schemes",
                "profile_answers",
                "matches",
                "applications",
                "events",
                "jobs",
                "artifacts",
            )
        }


@pytest.mark.db
def test_case_files_for_personas(settings_db: str) -> None:
    seed(settings_db, reset=True, now=NOW)
    with connect(settings_db) as conn:
        ravi = build_case_file(case_id_for("ravi"), conn, now=NOW)
        meena = build_case_file(case_id_for("meena"), conn, now=NOW)
        fatima = build_case_file(case_id_for("fatima"), conn, now=NOW)
        chase = conn.execute("select run_at from jobs where type = 'chase'").fetchone()["run_at"]
    assert chase == NOW + timedelta(minutes=2)
    assert ravi["applications"][0]["state"] == "UNDER_REVIEW"
    assert ravi["applications"][0]["days_since_movement"] == 11
    assert len(ravi["recent_events"]) == 10
    assert {m["scheme_id"]: m["result"] for m in ravi["matches"]}["pmegp"] == "FIT"
    assert meena["applications"] == [] and "loan_amount" in meena["missing_facts"]
    assert fatima["language"] == "en" and fatima["applications"][0]["state"] == "REJECTED"
    assert {m["scheme_id"]: m["result"] for m in fatima["matches"]}["mudra"] == "FIT"


@pytest.mark.db
def test_get_case_route(settings_db: str) -> None:
    from app.main import app

    seed(settings_db, reset=True, now=NOW)
    case_id = case_id_for("ravi")
    with TestClient(app) as client:
        assert client.get(f"/cases/{case_id}").status_code == 401
        ok = client.get(f"/cases/{case_id}", auth=("curator", "test-admin"))
        assert ok.status_code == 200 and ok.json()["state_code"] == "KA"
        missing = client.get("/cases/00000000-0000-0000-0000-000000000000", auth=("curator", "test-admin"))
        assert missing.status_code == 404


@pytest.mark.db
def test_seeded_artifacts_avoid_claude_calls(settings_db: str) -> None:
    from app.rules.loader import load_schemes

    seed(settings_db, reset=True, now=NOW)
    pmegp_hash = next(s["_hash"] for s in load_schemes() if s["id"] == "pmegp")
    with connect(settings_db) as conn:
        exp = conn.execute(
            "select explanation from matches where case_id = %s and scheme_id = 'pmegp'",
            (case_id_for("ravi"),),
        ).fetchone()["explanation"]
        kinds = {r["kind"]: r["content"] for r in conn.execute("select kind, content from artifacts")}
    assert exp["spec_hash"] == pmegp_hash and exp["result"] == "FIT"  # explain_match will treat as cached
    assert exp["citations"] == [] and exp["source_status"] == "pending"  # no invented guideline quotes
    assert {"en", "hi"} <= set(exp)
    assert kinds["rejection_analysis"]["fixable"] is True
    assert set(kinds["report_narrative"]) >= {"business_description", "market", "location"}


def test_open_question_stays_open_until_assistant_replies() -> None:
    from app.brain.casefile import open_question

    q = {
        "direction": "out",
        "body": "How much?",
        "attachments": {"kind": "question", "expected_fact": "loan_amount"},
    }
    answer = {"direction": "in", "body": "3 lakh", "attachments": None}
    reply = {"direction": "out", "body": "Thanks", "attachments": {"kind": "reply"}}
    assert open_question([answer, q]) == {"expected_fact": "loan_amount", "question": "How much?"}
    assert open_question([reply, answer, q]) is None
    assert open_question([]) is None
