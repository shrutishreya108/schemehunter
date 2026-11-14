"""15 portal messages → expected intents → the right (and only legal) effect (seed/portal_messages.yaml, LLD §8).

The model is FakeClaude: each message is answered with its labelled `expect`. So these tests prove what OUR code does
with each intent (confidence gate, application matching, legal transitions, no change for non-decisions), not how well
a real model classifies. That is the live smoke test T19b in BUILD_NOTES.
"""

from datetime import date
from pathlib import Path

import pytest
import yaml

from app.brain.tools import dispatch
from app.brain.tools.base import Ctx
from app.config import get_settings
from app.rules.loader import load_schemes

MESSAGES = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "seed" / "portal_messages.yaml").read_text(encoding="utf-8")
)
INTENTS = {"status_update", "query_raised", "rejected", "sanctioned", "question", "approval_reply", "other"}

pytestmark = pytest.mark.db


def test_fixture_has_15_messages_with_known_intents() -> None:
    assert len(MESSAGES) == 15
    assert {m["expect"]["intent"] for m in MESSAGES} == INTENTS


def ravi(conn, seeded) -> tuple[Ctx, str]:
    app_id = str(
        conn.execute("select id from applications where case_id = %s", (seeded["ravi"],)).fetchone()["id"]
    )
    return Ctx(case_id=seeded["ravi"], conn=conn, schemes=load_schemes(), language="en"), app_id


def script(fake, expect: dict, confidence: float = 0.92) -> None:
    fields = {k: v for k, v in expect.items() if k != "deadline_days"}
    fake.add(
        {
            "classify": [
                {"content": [{"tool": "interpret_response", "input": {**fields, "confidence": confidence}}]}
            ]
        }
    )


def state(conn, app_id: str) -> str:
    return conn.execute("select state from applications where id = %s", (app_id,)).fetchone()["state"]


EXPECTED_STATE = {  # from UNDER_REVIEW
    "query_raised": "DOCS_PENDING",
    "rejected": "REJECTED",
    "sanctioned": "SANCTIONED",
    "status_update": "UNDER_REVIEW",
    "question": "UNDER_REVIEW",
    "approval_reply": "UNDER_REVIEW",
    "other": "UNDER_REVIEW",
}


@pytest.mark.parametrize(
    "message", MESSAGES, ids=[f"{i:02d}-{m['expect']['intent']}" for i, m in enumerate(MESSAGES)]
)
def test_each_portal_message_has_the_right_effect(message, seeded, conn, fake_claude) -> None:
    ctx, app_id = ravi(conn, seeded)
    expect = message["expect"]
    script(fake_claude, expect)
    out = dispatch("interpret_response", {"text": message["text"], "application_id": app_id}, ctx)
    assert "error" not in out and not out.get("needs_clarification"), out
    assert out["intent"] == expect["intent"]
    assert state(conn, app_id) == EXPECTED_STATE[expect["intent"]]
    assert out["applied"] == (expect["intent"] in ("query_raised", "rejected", "sanctioned"))
    call = fake_claude.calls_for("classify")[0]
    assert call["model"] == get_settings().MODEL_CHEAP and call["tool_choice"]["name"] == "interpret_response"
    sent_to_model = call["messages"][0]["content"]
    assert message["text"][:80] in sent_to_model and date.today().isoformat() in sent_to_model
    if expect.get("deadline"):
        assert (
            conn.execute("select deadline from applications where id = %s", (app_id,))
            .fetchone()["deadline"]
            .isoformat()
            == expect["deadline"]
        )
    if expect["intent"] == "query_raised":
        ev = conn.execute(
            "select payload from events where application_id = %s and type = 'STATUS_CHECKED' and payload is not null order by id desc limit 1",
            (app_id,),
        ).fetchone()
        assert ev["payload"]["missing_docs"] == expect["missing_docs"]


def test_a_reference_number_is_filled_in_only_if_the_application_has_none(seeded, conn, fake_claude) -> None:
    ctx, app_id = ravi(conn, seeded)
    script(fake_claude, {"intent": "status_update", "ref_number": "SOMETHING/ELSE/1"})
    dispatch("interpret_response", {"text": "ref SOMETHING/ELSE/1 received", "application_id": app_id}, ctx)
    assert (
        conn.execute("select ref_number from applications where id = %s", (app_id,)).fetchone()["ref_number"]
        == "PMEGP/KA/2026/04471"
    )
    conn.execute("update applications set ref_number = null where id = %s", (app_id,))
    script(fake_claude, {"intent": "status_update", "ref_number": "MUD/2026/118"})
    dispatch("interpret_response", {"text": "ref MUD/2026/118 received", "application_id": app_id}, ctx)
    assert (
        conn.execute("select ref_number from applications where id = %s", (app_id,)).fetchone()["ref_number"]
        == "MUD/2026/118"
    )


def test_forwarded_to_bank_moves_submitted_to_under_review(seeded, conn, fake_claude) -> None:
    ctx, app_id = ravi(conn, seeded)
    conn.execute("update applications set state = 'SUBMITTED' where id = %s", (app_id,))
    script(fake_claude, {"intent": "status_update"})
    out = dispatch("interpret_response", {"text": "forwarded to bank", "application_id": app_id}, ctx)
    assert out["applied"] and state(conn, app_id) == "UNDER_REVIEW"
    assert {
        j["type"] for j in conn.execute("select type from jobs where application_id = %s", (app_id,))
    } >= {"status_check", "chase"}


def test_a_query_straight_after_submission_goes_through_review_first(seeded, conn, fake_claude) -> None:
    ctx, app_id = ravi(conn, seeded)
    conn.execute("update applications set state = 'SUBMITTED' where id = %s", (app_id,))
    script(fake_claude, {"intent": "query_raised", "missing_docs": ["income_proof"]})
    dispatch("interpret_response", {"text": "income proof needed", "application_id": app_id}, ctx)
    states = [
        r["payload"]["to"]
        for r in conn.execute(
            "select payload from events where application_id = %s and type = 'STATE_CHANGED' and payload is not null order by id",
            (app_id,),
        )
    ]
    assert states == ["UNDER_REVIEW", "QUERY_RAISED", "DOCS_PENDING"]


# ---------------------------------------------------------------- vague → clarification, never a guess


@pytest.mark.parametrize("intent", ["rejected", "sanctioned", "query_raised", "status_update"])
def test_low_confidence_changes_nothing_and_asks(intent, seeded, conn, fake_claude) -> None:
    ctx, app_id = ravi(conn, seeded)
    script(fake_claude, {"intent": intent}, confidence=0.69)
    out = dispatch("interpret_response", {"text": "update aaya hai", "application_id": app_id}, ctx)
    assert out["needs_clarification"] and out["question"]["hi"] and out["question"]["en"]
    assert state(conn, app_id) == "UNDER_REVIEW"


def test_confidence_exactly_at_the_gate_is_applied(seeded, conn, fake_claude) -> None:
    ctx, app_id = ravi(conn, seeded)
    script(fake_claude, {"intent": "rejected", "reason": "x"}, confidence=0.7)
    assert dispatch("interpret_response", {"text": "declined", "application_id": app_id}, ctx)["applied"]


def test_two_open_applications_and_no_reference_asks_which(seeded, conn, fake_claude) -> None:
    from app.tracker.store import create_application

    ctx, app_id = ravi(conn, seeded)
    create_application(conn, seeded["ravi"], "mudra")
    script(fake_claude, {"intent": "rejected", "reason": "declined"})
    out = dispatch("interpret_response", {"text": "Your application is declined."}, ctx)
    assert out["needs_clarification"] and "no unique application" in out["why"]
    assert state(conn, app_id) == "UNDER_REVIEW"


def test_reference_number_picks_the_right_application(seeded, conn, fake_claude) -> None:
    from app.tracker.store import create_application

    ctx, app_id = ravi(conn, seeded)
    other = create_application(conn, seeded["ravi"], "mudra")
    script(fake_claude, {"intent": "rejected", "reason": "declined", "ref_number": "PMEGP/KA/2026/04471"})
    out = dispatch("interpret_response", {"text": "PMEGP/KA/2026/04471 declined"}, ctx)
    assert (
        out["application_id"] == app_id
        and state(conn, app_id) == "REJECTED"
        and state(conn, other.id) == "ELIGIBLE"
    )


def test_a_model_guess_of_another_cases_application_is_ignored(seeded, conn, fake_claude) -> None:
    ctx, app_id = ravi(conn, seeded)
    theirs = str(
        conn.execute("select id from applications where case_id = %s", (seeded["fatima"],)).fetchone()["id"]
    )
    script(fake_claude, {"intent": "sanctioned", "application_id_guess": theirs})
    out = dispatch("interpret_response", {"text": "sanctioned"}, ctx)
    assert out["application_id"] == app_id  # fell back to this person's only open application
    assert state(conn, theirs) == "REJECTED"  # Fatima's application is untouched


def test_garbage_application_id_from_the_model_is_not_found_not_a_crash(seeded, conn, fake_claude) -> None:
    ctx, app_id = ravi(conn, seeded)
    script(fake_claude, {"intent": "rejected", "application_id_guess": "not-a-uuid; drop table x"})
    assert dispatch("interpret_response", {"text": "declined"}, ctx)["application_id"] == app_id


def test_an_illegal_move_is_reported_not_forced(seeded, conn, fake_claude) -> None:
    ctx, app_id = ravi(conn, seeded)
    conn.execute("update applications set state = 'SANCTIONED' where id = %s", (app_id,))
    script(fake_claude, {"intent": "rejected", "reason": "late letter"})
    out = dispatch("interpret_response", {"text": "rejected", "application_id": app_id}, ctx)
    assert out["applied"] is False and state(conn, app_id) == "SANCTIONED"


def test_message_id_reads_the_stored_message_and_marks_it_processed(seeded, conn, fake_claude) -> None:
    ctx, _ = ravi(conn, seeded)
    mid = conn.execute(
        "insert into messages (case_id, direction, channel, body, processed) values (%s, 'in', 'telegram', 'declined', false) returning id",
        (seeded["ravi"],),
    ).fetchone()["id"]
    script(fake_claude, {"intent": "rejected", "reason": "declined"})
    dispatch("interpret_response", {"message_id": str(mid)}, ctx)
    assert conn.execute("select processed from messages where id = %s", (mid,)).fetchone()["processed"]
    other = conn.execute(
        "insert into messages (case_id, direction, channel, body) values (%s, 'in', 'telegram', 'hi') returning id",
        (seeded["fatima"],),
    ).fetchone()["id"]
    assert (
        "no such inbound message" in dispatch("interpret_response", {"message_id": str(other)}, ctx)["error"]
    )
    assert "give message_id or text" in dispatch("interpret_response", {}, ctx)["error"]


def test_rejection_schedules_the_analysis_job(seeded, conn, fake_claude) -> None:
    ctx, app_id = ravi(conn, seeded)
    script(fake_claude, {"intent": "rejected", "reason": "project report not as per format"})
    dispatch(
        "interpret_response",
        {"text": "Application rejected: project report not as per format.", "application_id": app_id},
        ctx,
    )
    job = conn.execute(
        "select payload from jobs where application_id = %s and type = 'brain_run'", (app_id,)
    ).fetchone()
    assert job["payload"]["task"] == "analyze rejection"
