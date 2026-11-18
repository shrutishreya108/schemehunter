"""The whole journey for Meena: onboarding → FIT → papers → report + pack → submit → forwarded SMS → query →
re-send → resubmitted → under review. Tools and handlers with FakeClaude; the person presses every real button.
"""

from datetime import date, timedelta
from pathlib import Path

import pytest

from app.brain.runner import run_brain
from app.brain.tools import dispatch
from app.brain.tools.base import Ctx
from app.clock import worker
from app.docs.pipeline import process_upload
from app.guard import actions
from app.rules.loader import load_schemes
from app.tracker.states import transition
from app.tracker.store import DbTrackerStore, load_application

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).resolve().parents[1] / "docs" / "fixtures"


def tool(name: str, **inp) -> dict:
    return {"content": [{"tool": name, "input": inp}]}


def upload(conn, case_id, fake, filename: str, doc_type: str, vision: dict | None = None) -> dict:
    if vision:
        fake.add({"vision": [tool("document_check_result", **vision)]})
    return process_upload(conn, case_id, (FIXTURES / filename).read_bytes(), "image/jpeg", doc_type)


def vision(kind: str) -> dict:
    return {
        "doc_type_detected": kind,
        "readable": True,
        "quality_issues": [],
        "confidence": 0.95,
        "fields": {"name_latin": "Meena Kumari", "id_last4": "9012"},
    }


def states(conn, app_id: str) -> list[str]:
    return [
        r["payload"]["to"]
        for r in conn.execute(
            "select payload from events where application_id = %s and type = 'STATE_CHANGED' "
            "and payload is not null order by id",
            (app_id,),
        )
    ]


def outgoing(conn, case_id: str) -> list[str]:
    return [
        r["body"]
        for r in conn.execute(
            "select body from messages where case_id = %s and direction = 'out'", (case_id,)
        )
    ]


def test_meena_from_first_message_to_under_review_again(
    seeded, conn, vault_env, fake_pdf, fake_claude
) -> None:
    case_id = seeded["meena"]
    conn.execute("update cases set email = 'meena@example.test' where id = %s", (case_id,))

    # 1. Onboarding: the answer comes in, the engine says FIT, an application starts (all code decisions)
    fake_claude.add(
        {
            "loop": [
                tool("update_profile", key="loan_amount", value=300000),
                tool("match_schemes"),
                tool("create_application", scheme_id="pmegp"),
                tool("finish_run", user_message="आप PMEGP के लिए आवेदन करने के योग्य हैं।"),
            ]
        }
    )
    assert run_brain(case_id, "continue onboarding", "chat", conn=conn).status == "DONE"
    app_id = str(conn.execute("select id from applications where case_id = %s", (case_id,)).fetchone()["id"])
    assert states(conn, app_id)[-1] == "ELIGIBLE"

    # 2. Papers: vision for Aadhaar/passbook, plain checks for the rest; DOCS_READY needs every one OK
    ctx = Ctx(
        case_id=case_id, conn=conn, schemes=load_schemes(), state_code="KA", language="en", client=fake_claude
    )

    def move(to_state: str) -> dict:
        return dispatch("transition_application", {"application_id": app_id, "to_state": to_state}, ctx)

    assert move("DOCS_PENDING")["state"] == "DOCS_PENDING"
    assert "documents" in move("DOCS_READY")["error"]
    upload(conn, case_id, fake_claude, "aadhaar_clear_4.jpg", "aadhaar", vision("aadhaar"))
    upload(conn, case_id, fake_claude, "passbook_clear_1.jpg", "passbook", vision("passbook"))
    upload(conn, case_id, fake_claude, "photo_clear.jpg", "photo")
    upload(conn, case_id, fake_claude, "quotation_clear.jpg", "quotation")
    assert "error" in move("DOCS_READY")  # the caste certificate is still missing
    upload(conn, case_id, fake_claude, "caste_cert_clear.jpg", "caste_cert")
    assert move("DOCS_READY")["state"] == "DOCS_READY"

    # 3. Report, pack, and the "press submit" gate (nothing is submitted by us)
    assert dispatch("build_project_report", {"scheme_id": "pmegp"}, ctx)["totals"]["emi"] == 7609.85
    conn.execute(
        "insert into profile_answers (case_id, key, value, source) values (%s, 'address_line', '\"12 MG Road\"', 'typed')",
        (case_id,),
    )
    pack = dispatch("prefill_form", {"application_id": app_id}, ctx)
    assert pack["missing_fields"] == []
    assert move("FORM_READY")["state"] == "FORM_READY"
    assert dispatch(
        "request_submit", {"application_id": app_id, "pack_document_id": pack["pack_document_id"]}, ctx
    )["ok"]
    action = conn.execute(
        "select id, kind from pending_actions where application_id = %s and status = 'PENDING'", (app_id,)
    ).fetchone()
    assert action["kind"] == "instruct_submit"
    actions.approve(
        conn, str(action["id"]), "web", case_id=case_id
    )  # she taps "I'll do it": the steps reach her chat
    assert any("OTP" in body for body in outgoing(conn, case_id))

    # 4. She presses submit on the real portal and tells us the number (only the user may make this move)
    assert "error" in move("SUBMITTED")  # the agent cannot
    conn.execute("update applications set ref_number = 'PMEGP/KA/2026/09999' where id = %s", (app_id,))
    app = load_application(conn, app_id)
    transition(app, "SUBMITTED", "user", "pressed submit", DbTrackerStore(conn))

    # 5. The portal SMS she forwards moves it to review, then a query arrives
    def inbound(text: str) -> None:
        conn.execute(
            "insert into messages (case_id, direction, channel, body, processed) values (%s, 'in', 'web', %s, false)",
            (case_id, text),
        )

    def run_status_check(**classified) -> None:
        fake_claude.add({"classify": [tool("interpret_response", **classified)]})
        conn.execute("delete from jobs where case_id = %s", (case_id,))  # leave only the job under test
        conn.execute(
            "insert into jobs (type, case_id, application_id, run_at) values ('status_check', %s, %s, now())",
            (case_id, app_id),
        )
        conn.commit()
        job = worker.claim_next_job(conn)
        assert job["type"] == "status_check" and worker.run_job(conn, job) == "DONE"

    inbound("Your application PMEGP/KA/2026/09999 has been forwarded to Bank for appraisal.")
    run_status_check(intent="status_update", confidence=0.9)
    assert states(conn, app_id)[-1] == "UNDER_REVIEW"

    inbound("PMEGP/KA/2026/09999: Documents required - Income proof. Upload within 7 days.")
    run_status_check(
        intent="query_raised",
        missing_docs=["income_proof"],
        confidence=0.93,
        deadline=(date.today() + timedelta(days=7)).isoformat(),
    )
    assert states(conn, app_id)[-2:] == ["QUERY_RAISED", "DOCS_PENDING"]
    assert any("income proof" in body for body in outgoing(conn, case_id))

    # 6. She sends the paper, presses "I sent the papers", and it is back under review
    transition(load_application(conn, app_id), "RESUBMITTED", "user", "sent the papers", DbTrackerStore(conn))
    inbound("Your application has been received again and is under appraisal.")
    run_status_check(intent="status_update", confidence=0.88)
    assert states(conn, app_id)[-3:] == ["DOCS_PENDING", "RESUBMITTED", "UNDER_REVIEW"]

    # The diary: one ordered timeline, and nothing left the system that she did not approve
    sent = conn.execute("select count(*) as n from pending_actions where status = 'EXECUTED'").fetchone()["n"]
    assert sent == 1  # only the instruct_submit she approved
    timeline = [
        r["type"] for r in conn.execute("select type from events where case_id = %s order by id", (case_id,))
    ]
    assert timeline.index("MATCHED") < timeline.index("DOC_UPLOADED") < timeline.index("ACTION_APPROVED")
    assert timeline.index("ACTION_APPROVED") < timeline.index("STATUS_CHECKED")
    assert conn.execute("select count(*) as n from brain_runs where trigger = 'clock'").fetchone()["n"] == 3
