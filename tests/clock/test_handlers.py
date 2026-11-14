"""Job handlers through the real worker path, with FakeClaude: status_check, chase (Ravi), nudges, reminders, retry."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.clock import handlers, worker
from app.config import get_settings

pytestmark = pytest.mark.db

QUERY_SMS = "PMEGP Application PMEGP/KA/2026/04471: Documents required - Income proof. Upload within 7 days."


def app_of(conn, case_id: str) -> dict:
    return conn.execute(
        "select * from applications where case_id = %s order by created_at limit 1", (case_id,)
    ).fetchone()


def make_job(conn, type_: str, case_id: str, app_id=None, payload=None, run_in: float = -1) -> dict:
    from app.db import jsonb

    conn.execute("delete from jobs where case_id = %s", (case_id,))
    conn.execute(
        "insert into jobs (type, case_id, application_id, payload, run_at) "
        "values (%s, %s, %s, %s, now() + make_interval(secs => %s))",
        (type_, case_id, app_id, jsonb(payload or {}), run_in),
    )
    conn.commit()
    return worker.claim_next_job(conn)


def run(conn, job) -> str:
    return worker.run_job(conn, job)


def jobs_left(conn, case_id: str) -> list[dict]:
    return conn.execute(
        "select type, run_at, payload from jobs where case_id = %s and status = 'PENDING' order by run_at",
        (case_id,),
    ).fetchall()


def web_messages(conn, case_id: str) -> list[str]:
    return [
        r["body"]
        for r in conn.execute(
            "select body from messages where case_id = %s and direction = 'out' and channel = 'web' order by created_at",
            (case_id,),
        )
    ]


def classify(fake_claude, **fields) -> None:
    fake_claude.add({"classify": [{"content": [{"tool": "interpret_response", "input": fields}]}]})


# ---------------------------------------------------------------- status_check


def test_forwarded_sms_moves_ravi_to_docs_pending(seeded, conn, fake_claude) -> None:
    case_id = seeded["ravi"]
    app = app_of(conn, case_id)
    msg = conn.execute(
        "insert into messages (case_id, direction, channel, body, processed) values (%s, 'in', 'telegram', %s, false) returning id",
        (case_id, QUERY_SMS),
    ).fetchone()["id"]
    classify(
        fake_claude,
        intent="query_raised",
        ref_number="PMEGP/KA/2026/04471",
        missing_docs=["income_proof"],
        deadline=(date.today() + timedelta(days=7)).isoformat(),
        confidence=0.95,
    )
    assert run(conn, make_job(conn, "status_check", case_id, str(app["id"]))) == "DONE"
    after = conn.execute("select state, deadline from applications where id = %s", (app["id"],)).fetchone()
    assert after["state"] == "DOCS_PENDING" and after["deadline"] == date.today() + timedelta(days=7)
    assert conn.execute("select processed from messages where id = %s", (msg,)).fetchone()["processed"]
    assert any("income proof" in m or "आय" in m or "income" in m for m in web_messages(conn, case_id))
    left = [j["type"] for j in jobs_left(conn, case_id)]
    assert (
        "doc_nudge" in left and "status_check" not in left
    )  # the wait is on the user now: nudges, not status checks
    cost = conn.execute(
        "select estimated_cost_usd, trigger from brain_runs where case_id = %s", (case_id,)
    ).fetchone()
    assert (
        cost["trigger"] == "clock" and float(cost["estimated_cost_usd"]) > 0
    )  # metered like every other call
    call = fake_claude.calls_for("classify")[0]
    assert call["model"] == get_settings().MODEL_CHEAP  # classification is the cheap model


def test_vague_message_changes_nothing_and_asks(seeded, conn, fake_claude) -> None:
    case_id = seeded["ravi"]
    app = app_of(conn, case_id)
    conn.execute(
        "insert into messages (case_id, direction, channel, body, processed) values (%s, 'in', 'telegram', 'update aaya hai', false)",
        (case_id,),
    )
    classify(fake_claude, intent="status_update", confidence=0.4)
    run(conn, make_job(conn, "status_check", case_id, str(app["id"])))
    assert (
        conn.execute("select state from applications where id = %s", (app["id"],)).fetchone()["state"]
        == "UNDER_REVIEW"
    )


def test_two_quiet_checks_ask_the_user_to_forward_the_sms(seeded, conn, fake_claude) -> None:
    case_id = seeded["ravi"]
    app = app_of(conn, case_id)
    payload = {}
    for expected_nothing in (1, 2):
        assert run(conn, make_job(conn, "status_check", case_id, str(app["id"]), payload)) == "DONE"
        nxt = [j for j in jobs_left(conn, case_id) if j["type"] == "status_check"]
        assert len(nxt) == 1 and nxt[0]["payload"]["nothing_checks"] == expected_nothing
        payload = nxt[0]["payload"]
    assert any("forward the latest SMS" in m or "फ़ॉरवर्ड" in m for m in web_messages(conn, case_id))
    assert fake_claude.calls_for("classify") == []  # nothing to read, nothing spent


def test_status_check_stops_when_application_is_no_longer_waiting(seeded, conn) -> None:
    case_id = seeded["fatima"]  # REJECTED
    app = app_of(conn, case_id)
    run(conn, make_job(conn, "status_check", case_id, str(app["id"])))
    assert jobs_left(conn, case_id) == []


# ---------------------------------------------------------------- chase (Ravi's demo moment)


def test_ravis_seeded_chase_drafts_a_followup_for_approval(seeded, conn, fake_claude) -> None:
    case_id = seeded["ravi"]
    conn.execute("update cases set email = 'ravi@example.test' where id = %s", (case_id,))
    app = app_of(conn, case_id)
    seeded_chase = conn.execute(
        "select run_at from jobs where case_id = %s and type = 'chase'", (case_id,)
    ).fetchone()
    assert (
        0 < (seeded_chase["run_at"] - datetime.now(UTC)).total_seconds() <= 125
    )  # fires ~2 minutes after reset
    conn.execute(
        "update jobs set run_at = now() - interval '1 second' where case_id = %s and type = 'chase'",
        (case_id,),
    )
    conn.commit()
    fake_claude.add(
        {
            "loop": [
                {
                    "content": [
                        {
                            "tool": "draft_message",
                            "input": {
                                "purpose": "ask for the status",
                                "audience": "bank",
                                "application_id": str(app["id"]),
                                "points": ["no news for 11 days"],
                            },
                        }
                    ]
                },
                {
                    "content": [
                        {
                            "tool": "finish_run",
                            "input": {"user_message": "बैंक के लिए एक ड्राफ़्ट आपकी मंज़ूरी का इंतज़ार कर रहा है।"},
                        }
                    ]
                },
            ],
            "draft": [
                {
                    "content": [
                        {
                            "tool": "message_draft",
                            "input": {
                                "channel": "email",
                                "to": "ignored@evil.test",
                                "subject": "PMEGP/KA/2026/04471: status request",
                                "body": "I am writing on behalf of Ravi Kumar about application PMEGP/KA/2026/04471. Could you please share the current status?",
                                "language": "en",
                            },
                        }
                    ]
                }
            ],
        }
    )
    claimed = worker.claim_next_job(conn)
    assert claimed["type"] == "chase" and run(conn, claimed) == "DONE"
    action = conn.execute(
        "select kind, status, payload from pending_actions where case_id = %s", (case_id,)
    ).fetchone()
    assert (action["kind"], action["status"]) == ("send_email", "PENDING")
    assert action["payload"]["to"] == "you+bank@example.com"  # from contacts.yaml, never the model's address
    assert "Ravi Kumar" in action["payload"]["body"] and "Scheme Hunter" in action["payload"]["body"]
    assert (
        conn.execute(
            "select count(*) as n from events where case_id = %s and type = 'DRAFT_CREATED'", (case_id,)
        ).fetchone()["n"]
        == 1
    )
    chases = [j for j in jobs_left(conn, case_id) if j["type"] == "chase"]
    assert len(chases) == 1 and chases[0]["run_at"] > datetime.now(UTC) + timedelta(days=6)  # again in 7 days


def test_chase_waits_if_not_idle_long_enough(seeded, conn, fake_claude) -> None:
    case_id = seeded["ravi"]
    app = app_of(conn, case_id)
    conn.execute("update applications set last_movement_at = now() where id = %s", (app["id"],))
    run(conn, make_job(conn, "chase", case_id, str(app["id"])))
    assert fake_claude.calls_for("loop") == []
    nxt = jobs_left(conn, case_id)
    assert [j["type"] for j in nxt] == ["chase"] and nxt[0]["run_at"] > datetime.now(UTC) + timedelta(days=9)


def test_chase_idle_time_follows_the_demo_clock(seeded, conn, fake_claude, monkeypatch) -> None:
    monkeypatch.setenv("DEMO_CLOCK_SCALE", "1440")
    get_settings.cache_clear()
    app = app_of(conn, seeded["ravi"])
    conn.execute(
        "update applications set last_movement_at = now() - interval '11 minutes' where id = %s", (app["id"],)
    )
    assert handlers.elapsed_days(datetime.now(UTC) - timedelta(minutes=11)) == pytest.approx(11, abs=0.1)
    get_settings.cache_clear()


# ---------------------------------------------------------------- doc_nudge


def test_doc_nudge_lists_missing_papers_at_most_once_a_day(seeded, conn) -> None:
    case_id = seeded["ravi"]
    app = app_of(conn, case_id)
    conn.execute("update applications set state = 'DOCS_PENDING' where id = %s", (app["id"],))
    for _ in range(2):
        run(conn, make_job(conn, "doc_nudge", case_id, str(app["id"])))
    nudges = [m for m in web_messages(conn, case_id) if "आधार" in m or "Aadhaar" in m]
    assert len(nudges) == 1  # a second run the same day is silent
    assert [j["type"] for j in jobs_left(conn, case_id)] == ["doc_nudge"]  # but it keeps the daily alarm


def test_doc_nudge_stops_once_everything_is_ok(seeded, conn, monkeypatch) -> None:
    case_id = seeded["ravi"]
    app = app_of(conn, case_id)
    conn.execute("update applications set state = 'DOCS_PENDING' where id = %s", (app["id"],))
    monkeypatch.setattr(handlers, "missing_docs", lambda *a: [])
    run(conn, make_job(conn, "doc_nudge", case_id, str(app["id"])))
    assert jobs_left(conn, case_id) == [] and not web_messages(conn, case_id)


# ---------------------------------------------------------------- deadline_reminders


def test_reminders_at_7_and_2_days_then_missed(seeded, conn) -> None:
    case_id = seeded["meena"]
    from app.tracker.store import create_application

    app = create_application(conn, case_id, "pmegp")
    conn.execute(
        "update applications set deadline = %s where id = %s", (date.today() + timedelta(days=7), app.id)
    )
    job = lambda: make_job(conn, "deadline_reminders", case_id, app.id)
    run(conn, job())
    assert len([m for m in web_messages(conn, case_id) if "7" in m]) == 1
    run(conn, job())
    assert len(web_messages(conn, case_id)) == 1  # same threshold: not repeated
    conn.execute(
        "update applications set deadline = %s where id = %s", (date.today() + timedelta(days=2), app.id)
    )
    run(conn, job())
    assert len(web_messages(conn, case_id)) == 2
    conn.execute(
        "update applications set deadline = %s where id = %s", (date.today() - timedelta(days=1), app.id)
    )
    run(conn, job())
    assert (
        conn.execute("select state from applications where id = %s", (app.id,)).fetchone()["state"]
        == "MISSED"
    )
    assert "retry_cycle" in [j["type"] for j in jobs_left(conn, case_id)]  # MISSED → retry next cycle


def test_rolling_window_has_nothing_to_remind(seeded, conn) -> None:
    from app.tracker.store import create_application

    app = create_application(conn, seeded["meena"], "pmegp")
    run(conn, make_job(conn, "deadline_reminders", seeded["meena"], app.id))
    assert web_messages(conn, seeded["meena"]) == []
    assert "deadline_reminders" not in [j["type"] for j in jobs_left(conn, seeded["meena"])]


# ---------------------------------------------------------------- retry_cycle


def test_retry_cycle_starts_a_new_application_and_reuses_papers(seeded, conn) -> None:
    case_id = seeded["fatima"]
    app = app_of(conn, case_id)
    conn.execute("update applications set state = 'RETRY_NEXT_CYCLE' where id = %s", (app["id"],))
    conn.execute(
        "insert into documents (case_id, doc_type, file_path, sha256, mime, check_status) values (%s, 'aadhaar', '/x', 'abc', 'image/jpeg', 'OK')",
        (case_id,),
    )
    run(conn, make_job(conn, "retry_cycle", case_id, str(app["id"])))
    rows = conn.execute(
        "select state, scheme_id from applications where case_id = %s order by created_at", (case_id,)
    ).fetchall()
    assert [r["state"] for r in rows] == ["RETRY_NEXT_CYCLE", "ELIGIBLE"] and rows[1]["scheme_id"] == "mudra"
    assert (
        conn.execute("select count(*) as n from documents where case_id = %s", (case_id,)).fetchone()["n"]
        == 1
    )


def test_retry_cycle_ignores_applications_not_waiting_for_a_retry(seeded, conn) -> None:
    app = app_of(conn, seeded["ravi"])  # UNDER_REVIEW
    run(conn, make_job(conn, "retry_cycle", seeded["ravi"], str(app["id"])))
    assert (
        conn.execute(
            "select count(*) as n from applications where case_id = %s", (seeded["ravi"],)
        ).fetchone()["n"]
        == 1
    )


# ---------------------------------------------------------------- brain_run, retention


def test_deferred_brain_run_job_executes(seeded, conn, fake_claude) -> None:
    case_id = seeded["meena"]
    fake_claude.add({"loop": [{"content": [{"tool": "finish_run", "input": {"user_message": "ठीक है।"}}]}]})
    job = make_job(
        conn,
        "brain_run",
        case_id,
        payload={"task": "continue onboarding", "trigger": "chat", "deferred": "budget"},
    )
    assert run(conn, job) == "DONE"
    assert (
        conn.execute("select status from brain_runs where case_id = %s", (case_id,)).fetchone()["status"]
        == "DONE"
    )


def test_brain_run_api_failure_is_retried_with_backoff(seeded, conn, fake_claude, monkeypatch) -> None:
    from app.brain import claude

    def down(**kw):
        raise ConnectionError("api down")

    monkeypatch.setattr(fake_claude, "create", down)
    claude.set_client(fake_claude)
    job = make_job(conn, "brain_run", seeded["meena"], payload={"task": "continue onboarding"})
    assert run(conn, job) == "PENDING"  # not lost: the job goes back with a 1-minute delay
    row = conn.execute("select attempts, last_error from jobs where id = %s", (job["id"],)).fetchone()
    assert row["attempts"] == 1 and "ConnectionError" in row["last_error"]


def test_brain_run_without_a_task_fails_loudly(seeded, conn) -> None:
    assert run(conn, make_job(conn, "brain_run", seeded["meena"], payload={})) == "PENDING"


def test_retention_deletes_only_old_closed_cases(seeded, conn, tmp_path) -> None:
    old_file, new_file = tmp_path / "old.bin", tmp_path / "new.bin"
    old_file.write_bytes(b"x")
    new_file.write_bytes(b"y")
    for case_key, f, closed_days in (("fatima", old_file, 120), ("ravi", new_file, 5)):
        cid = seeded[case_key]
        conn.execute("update cases set state = 'CLOSED' where id = %s", (cid,))
        conn.execute(
            "update events set created_at = now() - make_interval(days => %s) where case_id = %s",
            (closed_days, cid),
        )
        conn.execute(
            "insert into documents (case_id, doc_type, file_path, sha256, mime) values (%s, 'aadhaar', %s, %s, 'image/jpeg')",
            (cid, str(f), case_key),
        )
    assert handlers.retention_cleanup(conn) == 1
    assert not old_file.exists() and new_file.exists()
    assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 1
    active = seeded["meena"]
    assert conn.execute("select state from cases where id = %s", (active,)).fetchone()["state"] != "CLOSED"


def test_offer_domain_finishes_quietly_when_flag_is_off(seeded, conn) -> None:
    assert run(conn, make_job(conn, "offer_domain", seeded["meena"])) == "DONE"
