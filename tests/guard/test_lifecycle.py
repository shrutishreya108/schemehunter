"""Guard lifecycle over the DB: approve once → send once, retries, FAILED, expiry, ownership, draft_message rewrites."""

import pytest

from app.brain.tools import dispatch
from app.brain.tools.base import Ctx
from app.guard import actions
from app.mouth.base import SendError
from app.rules.loader import load_schemes

pytestmark = pytest.mark.db

GOOD = "I am writing on behalf of Ravi Kumar about application PMEGP/KA/2026/04471. Could you please share the current status?"


def ravi_app(conn, seeded) -> str:
    conn.execute("update cases set email = 'ravi@example.test' where id = %s", (seeded["ravi"],))
    return str(
        conn.execute("select id from applications where case_id = %s", (seeded["ravi"],)).fetchone()["id"]
    )


def pending_email(conn, seeded, body: str = GOOD) -> str:
    app_id = ravi_app(conn, seeded)
    payload = {"to": "bank@example.test", "subject": "PMEGP/KA/2026/04471", "body": body, "audience": "bank"}
    return str(actions.create_pending(conn, seeded["ravi"], "send_email", payload, app_id)["id"])


def status(conn, action_id: str) -> dict:
    return conn.execute(
        "select status, attempts, error, executed_at from pending_actions where id = %s", (action_id,)
    ).fetchone()


def test_double_approve_sends_exactly_one_email_with_the_user_copied(seeded, conn, smtp_settings) -> None:
    action_id = pending_email(conn, seeded)
    actions.approve(conn, action_id, "web", case_id=seeded["ravi"])
    actions.approve(conn, action_id, "web", case_id=seeded["ravi"])  # the double tap
    assert len(smtp_settings.handler.messages) == 1
    sent = smtp_settings.handler.messages[0]
    assert "ravi@example.test" in sent.rcpt_tos and "bank@example.test" in sent.rcpt_tos  # CC'd
    row = status(conn, action_id)
    assert row["status"] == "EXECUTED" and row["executed_at"] and row["attempts"] == 1
    types = [r["type"] for r in conn.execute("select type from events where case_id = %s", (seeded["ravi"],))]
    assert types.count("ACTION_APPROVED") == 1 and types.count("MESSAGE_SENT") == 1


def test_identical_draft_twice_is_one_pending_action(seeded, conn) -> None:
    first = pending_email(conn, seeded)
    app_id = ravi_app(conn, seeded)
    payload = {"to": "bank@example.test", "subject": "PMEGP/KA/2026/04471", "body": GOOD, "audience": "bank"}
    assert actions.create_pending(conn, seeded["ravi"], "send_email", payload, app_id) is None
    assert (
        conn.execute("select count(*) as n from pending_actions where id = %s", (first,)).fetchone()["n"] == 1
    )


def test_the_row_is_claimed_before_the_provider_is_called(seeded, conn, monkeypatch) -> None:
    """A crash mid-send must not allow a resend: the claim (EXECUTED + executed_at) is already committed."""
    action_id = pending_email(conn, seeded)
    seen = {}

    def spy(conn_, action):
        from app.db import connect

        with connect() as other:  # a second connection sees only committed state
            seen["status"] = other.execute(
                "select status, executed_at from pending_actions where id = %s", (action["id"],)
            ).fetchone()

    monkeypatch.setattr("app.guard.executors.run_action", spy)
    actions.approve(conn, action_id, "web", case_id=seeded["ravi"])
    assert seen["status"]["status"] == "EXECUTED" and seen["status"]["executed_at"] is not None


def test_failed_send_is_retried_then_marked_failed_after_three_tries(seeded, conn, monkeypatch) -> None:
    action_id = pending_email(conn, seeded)
    calls = []

    def down(conn_, action):
        calls.append(1)
        raise SendError("smtp send failed: OSError")

    monkeypatch.setattr("app.guard.executors.run_action", down)
    actions.approve(conn, action_id, "web", case_id=seeded["ravi"])
    assert (
        status(conn, action_id)["status"] == "APPROVED" and len(calls) == 1
    )  # back to approved for another go
    assert actions.retry_failed_sends(conn) == 1 and status(conn, action_id)["status"] == "APPROVED"
    assert actions.retry_failed_sends(conn) == 1
    final = status(conn, action_id)
    assert (
        final["status"] == "FAILED"
        and final["attempts"] == 3
        and "SendError" in final["error"]
        and len(calls) == 3
    )
    assert actions.retry_failed_sends(conn) == 0  # no fourth try


def test_official_mail_without_a_user_copy_is_refused_and_fails_visibly(seeded, conn, smtp_settings) -> None:
    app_id = str(
        conn.execute("select id from applications where case_id = %s", (seeded["ravi"],)).fetchone()["id"]
    )
    payload = {"to": "bank@example.test", "subject": "s", "body": GOOD, "audience": "bank"}
    action_id = str(actions.create_pending(conn, seeded["ravi"], "send_email", payload, app_id)["id"])
    for _ in range(3):
        actions.approve(conn, action_id, "web", case_id=seeded["ravi"])
        actions.retry_failed_sends(conn)
    assert status(conn, action_id)["status"] == "FAILED" and smtp_settings.handler.messages == []


def test_decline_never_sends(seeded, conn, smtp_settings) -> None:
    action_id = pending_email(conn, seeded)
    actions.reject(conn, action_id, "web", case_id=seeded["ravi"])
    actions.approve(
        conn, action_id, "web", case_id=seeded["ravi"]
    )  # approving after declining changes nothing
    assert status(conn, action_id)["status"] == "REJECTED" and smtp_settings.handler.messages == []


def test_another_cases_action_looks_like_it_does_not_exist(seeded, conn) -> None:
    action_id = pending_email(conn, seeded)
    with pytest.raises(actions.GuardError):
        actions.approve(conn, action_id, "web", case_id=seeded["meena"])
    assert status(conn, action_id)["status"] == "PENDING"


def test_old_pending_actions_expire_with_a_timeline_entry(seeded, conn) -> None:
    old = pending_email(conn, seeded)
    conn.execute("update pending_actions set created_at = now() - interval '8 days' where id = %s", (old,))
    fresh = str(actions.create_pending(conn, seeded["ravi"], "send_chat", {"body": "hello"})["id"])
    assert actions.expire(conn) == 1
    assert status(conn, old)["status"] == "EXPIRED" and status(conn, fresh)["status"] == "PENDING"
    assert (
        conn.execute("select count(*) as n from events where payload->>'expired' = 'true'").fetchone()["n"]
        == 1
    )
    actions.approve(conn, old, "web", case_id=seeded["ravi"])  # an expired action can't be revived
    assert status(conn, old)["status"] == "EXPIRED"


def test_short_codes_are_unique_among_one_cases_pending(seeded, conn) -> None:
    ids = [actions.create_pending(conn, seeded["ravi"], "send_chat", {"body": f"m{i}"}) for i in range(40)]
    codes = [r["short_code"] for r in ids]
    assert len(set(codes)) == 40


def test_instruct_submit_sends_the_steps_to_the_user(seeded, conn) -> None:
    app_id = str(
        conn.execute("select id from applications where case_id = %s", (seeded["ravi"],)).fetchone()["id"]
    )
    action_id = str(
        actions.create_pending(
            conn, seeded["ravi"], "instruct_submit", {"application_id": app_id, "scheme_id": "pmegp"}, app_id
        )["id"]
    )
    actions.approve(conn, action_id, "web", case_id=seeded["ravi"])
    msgs = [
        r["body"]
        for r in conn.execute(
            "select body from messages where case_id = %s and direction = 'out' and channel = 'web'",
            (seeded["ravi"],),
        )
    ]
    assert any("OTP" in m for m in msgs) and any("1." in m for m in msgs)
    assert status(conn, action_id)["status"] == "EXECUTED"


def test_register_domain_is_not_enabled(seeded, conn) -> None:
    action_id = str(
        actions.create_pending(conn, seeded["ravi"], "register_domain", {"names": ["x.in"]})["id"]
    )
    for _ in range(3):
        actions.approve(conn, action_id, "web", case_id=seeded["ravi"])
        actions.retry_failed_sends(conn)
    assert status(conn, action_id)["status"] == "FAILED"


# ---------------------------------------------------------------- draft_message: guard + rewrites


def ctx_for(conn, seeded, fake) -> Ctx:
    return Ctx(
        case_id=seeded["ravi"], conn=conn, schemes=load_schemes(), state_code="KA", language="en", client=fake
    )


def draft(body: str, subject: str = "PMEGP/KA/2026/04471: status request") -> dict:
    return {
        "content": [
            {
                "tool": "message_draft",
                "input": {
                    "channel": "email",
                    "to": "x@y.z",
                    "subject": subject,
                    "body": body,
                    "language": "en",
                },
            }
        ]
    }


def call(conn, seeded, fake, audience="bank") -> dict:
    app_id = ravi_app(conn, seeded)
    return dispatch(
        "draft_message",
        {
            "purpose": "ask for status",
            "audience": audience,
            "application_id": app_id,
            "points": ["no news for 11 days"],
        },
        ctx_for(conn, seeded, fake),
    )


def test_a_promise_is_bounced_and_rewritten(seeded, conn, fake_claude) -> None:
    fake_claude.add({"draft": [draft("We guarantee the loan will be approved soon."), draft(GOOD)]})
    out = call(conn, seeded, fake_claude)
    assert "pending_action_id" in out and len(fake_claude.calls_for("draft")) == 2
    retry_prompt = fake_claude.calls_for("draft")[1]["messages"][-1]["content"]
    assert "Blocked by the guard" in retry_prompt and "guarantee" in retry_prompt
    row = conn.execute(
        "select payload, status from pending_actions where id = %s", (out["pending_action_id"],)
    ).fetchone()
    assert "guarantee" not in row["payload"]["body"].lower() and row["status"] == "PENDING"


def test_an_invented_scheme_is_bounced(seeded, conn, fake_claude) -> None:
    fake_claude.add(
        {"draft": [draft("Please also consider the Mukhyamantri Rojgar Yojana for Ravi Kumar."), draft(GOOD)]}
    )
    out = call(conn, seeded, fake_claude)
    assert "pending_action_id" in out
    assert "Rojgar" not in conn.execute("select payload from pending_actions").fetchone()["payload"]["body"]


def test_three_bad_drafts_are_parked_for_the_curator_not_offered_to_the_user(
    seeded, conn, fake_claude
) -> None:
    fake_claude.add({"draft": [draft("We guarantee approval.")] * 3})
    out = call(conn, seeded, fake_claude)
    assert "error" in out and len(fake_claude.calls_for("draft")) == 3  # first try + 2 rewrites
    row = conn.execute("select status, error from pending_actions").fetchone()
    assert row["status"] == "FAILED" and row["error"].startswith("guard:")
    assert (
        conn.execute("select count(*) as n from pending_actions where status = 'PENDING'").fetchone()["n"]
        == 0
    )


def test_the_recipient_comes_from_contacts_not_the_model(seeded, conn, fake_claude) -> None:
    fake_claude.add({"draft": [draft(GOOD)]})
    out = call(conn, seeded, fake_claude)
    row = conn.execute(
        "select payload from pending_actions where id = %s", (out["pending_action_id"],)
    ).fetchone()
    assert row["payload"]["to"] == "you+bank@example.com" and "x@y.z" not in str(row["payload"])


def test_official_draft_needs_the_users_email_first(seeded, conn, fake_claude) -> None:
    app_id = str(
        conn.execute("select id from applications where case_id = %s", (seeded["ravi"],)).fetchone()["id"]
    )
    out = dispatch(
        "draft_message",
        {"purpose": "status", "audience": "bank", "application_id": app_id},
        ctx_for(conn, seeded, fake_claude),
    )
    assert (
        "email" in out["error"] and fake_claude.calls_for("draft") == []
    )  # no spend before the precondition holds


def test_draft_message_needs_an_application_for_official_audiences(seeded, conn, fake_claude) -> None:
    out = dispatch(
        "draft_message", {"purpose": "hi", "audience": "officer"}, ctx_for(conn, seeded, fake_claude)
    )
    assert "application_id" in out["error"]


def test_the_draft_is_never_sent_by_the_tool(seeded, conn, fake_claude, smtp_settings) -> None:
    fake_claude.add({"draft": [draft(GOOD)]})
    call(conn, seeded, fake_claude)
    assert smtp_settings.handler.messages == []
    assert conn.execute("select count(*) as n from messages where channel = 'email'").fetchone()["n"] == 0


def test_user_email_can_be_saved_through_update_profile(seeded, conn, fake_claude) -> None:
    ctx = ctx_for(conn, seeded, fake_claude)
    assert dispatch("update_profile", {"key": "email", "value": "meena@example.test"}, ctx)["ok"]
    assert (
        conn.execute("select email from cases where id = %s", (seeded["ravi"],)).fetchone()["email"]
        == "meena@example.test"
    )
    assert "email must" in dispatch("update_profile", {"key": "email", "value": "not-an-email"}, ctx)["error"]
    events = conn.execute(
        "select payload, summary from events where case_id = %s and payload->>'key' = 'email'",
        (seeded["ravi"],),
    ).fetchall()
    assert events and "meena@example.test" not in str(events)  # the address stays out of the timeline
