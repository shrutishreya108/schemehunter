"""DB-backed tracker (slice 4): every legal transition, illegal ones leave no trace, effects → jobs/actions."""

from datetime import UTC, datetime

import pytest

from app.brain.runner import run_brain
from app.brain.tools import dispatch
from app.brain.tools.base import Ctx
from app.rules.loader import load_schemes
from app.tracker.states import TRANSITIONS, IllegalTransition, transition
from app.tracker.store import DbTrackerStore, create_application, load_application

pytestmark = pytest.mark.db


def make_app(conn, case_id: str, state: str, scheme_id: str = "pmegp", ref: str | None = "REF-1") -> str:
    row = conn.execute(
        "insert into applications (case_id, scheme_id, state, ref_number) values (%s, %s, %s, %s) returning id",
        (case_id, scheme_id, state, ref),
    ).fetchone()
    return str(row["id"])


def state_of(conn, app_id: str) -> str:
    return conn.execute("select state from applications where id = %s", (app_id,)).fetchone()["state"]


def count(conn, table: str, case_id: str, **where) -> int:
    sql = f"select count(*) as n from {table} where case_id = %s" + "".join(f" and {k} = %s" for k in where)
    return conn.execute(sql, (case_id, *where.values())).fetchone()["n"]


@pytest.fixture
def docs_ok(monkeypatch) -> None:
    monkeypatch.setattr(DbTrackerStore, "required_docs_ok", lambda self, app: True)


@pytest.mark.parametrize(
    ("pair", "rule"), list(TRANSITIONS.items()), ids=[f"{a}->{b}" for a, b in TRANSITIONS]
)
def test_every_legal_transition_through_the_db(pair, rule, seeded, conn, docs_ok) -> None:
    frm, to = pair
    case_id = seeded["meena"]
    app = load_application(conn, make_app(conn, case_id, frm))
    before = count(conn, "events", case_id)
    transition(app, to, rule.actors[0], "test", DbTrackerStore(conn))
    assert state_of(conn, app.id) == to
    assert count(conn, "events", case_id) == before + 1
    ev = conn.execute(
        "select actor, type, application_id, summary from events where case_id = %s order by id desc limit 1",
        (case_id,),
    ).fetchone()
    assert (ev["type"], ev["actor"], str(ev["application_id"])) == ("STATE_CHANGED", rule.actors[0], app.id)
    assert ev["summary"]["en"] and ev["summary"]["hi"]
    jobs = count(conn, "jobs", case_id, application_id=app.id)
    pendings = count(conn, "pending_actions", case_id, application_id=app.id)
    assert jobs == sum(e.kind == "schedule" for e in rule.effects)
    assert pendings == sum(e.kind == "pending" for e in rule.effects)


def test_illegal_transition_leaves_no_trace(seeded, conn) -> None:
    case_id = seeded["meena"]
    app = load_application(conn, make_app(conn, case_id, "DISCOVERED"))
    events = count(conn, "events", case_id)
    with pytest.raises(IllegalTransition):
        transition(app, "SUBMITTED", "agent", "skip", DbTrackerStore(conn))
    with pytest.raises(IllegalTransition):  # only the user presses submit
        transition(
            load_application(conn, make_app(conn, case_id, "AWAITING_HUMAN_SUBMIT")),
            "SUBMITTED",
            "agent",
            "x",
            DbTrackerStore(conn),
        )
    assert state_of(conn, app.id) == "DISCOVERED" and count(conn, "events", case_id) == events


def test_ref_number_guard(seeded, conn) -> None:
    app = load_application(conn, make_app(conn, seeded["meena"], "AWAITING_HUMAN_SUBMIT", ref=None))
    with pytest.raises(IllegalTransition, match="reference number"):
        transition(app, "SUBMITTED", "user", "pressed submit", DbTrackerStore(conn))
    assert state_of(conn, app.id) == "AWAITING_HUMAN_SUBMIT"


def test_docs_ready_guard_uses_real_document_statuses(seeded, conn) -> None:
    app = load_application(conn, make_app(conn, seeded["meena"], "DOCS_PENDING"))
    with pytest.raises(IllegalTransition, match="documents"):
        transition(app, "DOCS_READY", "agent", "x", DbTrackerStore(conn))


def test_demo_clock_scales_effects_to_minutes(seeded, conn) -> None:
    case_id = seeded["meena"]
    app = load_application(conn, make_app(conn, case_id, "SUBMITTED"))
    now = datetime.now(UTC)
    transition(app, "UNDER_REVIEW", "clock", "forwarded", DbTrackerStore(conn, scale=1440))
    rows = {
        r["type"]: (r["run_at"] - now).total_seconds() / 60
        for r in conn.execute("select type, run_at from jobs where application_id = %s", (app.id,))
    }
    assert rows["status_check"] == pytest.approx(3, abs=0.5)  # days=3 → +3 minutes
    assert rows["chase"] == pytest.approx(10, abs=0.5)  # days=10 → +10 minutes


def test_demo_clock_one_day_is_one_minute(seeded, conn) -> None:
    app = load_application(conn, make_app(conn, seeded["meena"], "AWAITING_HUMAN_SUBMIT"))
    now = datetime.now(UTC)
    transition(app, "SUBMITTED", "user", "pressed submit", DbTrackerStore(conn, scale=1440))
    run_at = conn.execute("select run_at from jobs where application_id = %s", (app.id,)).fetchone()["run_at"]
    assert (run_at - now).total_seconds() == pytest.approx(60, abs=30)


def test_instruct_submit_effect_creates_a_pending_action(seeded, conn) -> None:
    app = load_application(conn, make_app(conn, seeded["meena"], "FORM_READY"))
    transition(app, "AWAITING_HUMAN_SUBMIT", "agent", "pack ready", DbTrackerStore(conn))
    row = conn.execute(
        "select kind, status, short_code from pending_actions where application_id = %s", (app.id,)
    ).fetchone()
    assert row["kind"] == "instruct_submit" and row["status"] == "PENDING" and len(row["short_code"]) == 4


def test_rejection_schedules_analysis_job_with_task(seeded, conn) -> None:
    app = load_application(conn, make_app(conn, seeded["meena"], "UNDER_REVIEW"))
    transition(app, "REJECTED", "agent", "letter", DbTrackerStore(conn))
    job = conn.execute("select type, payload from jobs where application_id = %s", (app.id,)).fetchone()
    assert job["type"] == "brain_run" and job["payload"]["task"] == "analyze rejection"


def test_create_application_goes_to_eligible_and_schedules_reminders(seeded, conn) -> None:
    app = create_application(conn, seeded["meena"], "pmegp")
    assert state_of(conn, app.id) == "ELIGIBLE"
    types = [r["type"] for r in conn.execute("select type from events where application_id = %s", (app.id,))]
    assert types == ["STATE_CHANGED", "STATE_CHANGED"]  # created, then DISCOVERED → ELIGIBLE
    assert (
        conn.execute("select type from jobs where application_id = %s", (app.id,)).fetchone()["type"]
        == "deadline_reminders"
    )


# ---------------------------------------------------------------- the Brain's tools


def tool_ctx(conn, case_id: str) -> Ctx:
    return Ctx(case_id=case_id, conn=conn, schemes=load_schemes(), state_code="KA")


def test_create_application_tool_refuses_non_fit(seeded, conn) -> None:
    ctx = tool_ctx(conn, seeded["meena"])
    conn.execute("delete from matches where case_id = %s", (seeded["meena"],))
    assert "match_schemes" in dispatch("create_application", {"scheme_id": "pmegp"}, ctx)["error"]
    conn.execute(
        "insert into matches (case_id, scheme_id, result, rule_results) values (%s, 'cgtmse', 'NO_FIT', '{}')",
        (seeded["meena"],),
    )
    out = dispatch("create_application", {"scheme_id": "cgtmse"}, ctx)
    assert "NO_FIT" in out["error"]
    assert count(conn, "applications", seeded["meena"]) == 0


def test_create_application_tool_is_idempotent_and_validates_scheme(seeded, conn) -> None:
    ctx = tool_ctx(conn, seeded["meena"])
    conn.execute(
        "insert into matches (case_id, scheme_id, result, rule_results) values (%s, 'pmegp', 'FIT', '{}') "
        "on conflict (case_id, scheme_id) do update set result = 'FIT'",
        (seeded["meena"],),
    )
    first = dispatch("create_application", {"scheme_id": "pmegp"}, ctx)
    again = dispatch("create_application", {"scheme_id": "pmegp"}, ctx)
    assert (
        first["state"] == "ELIGIBLE"
        and again["already_exists"]
        and again["application_id"] == first["application_id"]
    )
    assert count(conn, "applications", seeded["meena"]) == 1
    assert "unknown scheme_id" in dispatch("create_application", {"scheme_id": "nope"}, ctx)["error"]


def test_transition_tool_enforces_actor_and_scope(seeded, conn) -> None:
    ctx = tool_ctx(conn, seeded["meena"])
    mine = make_app(conn, seeded["meena"], "AWAITING_HUMAN_SUBMIT")
    out = dispatch("transition_application", {"application_id": mine, "to_state": "SUBMITTED"}, ctx)
    assert (
        "error" in out
        and "allowed_next_for_agent" in out
        and "SUBMITTED" not in out["allowed_next_for_agent"]
    )
    assert state_of(conn, mine) == "AWAITING_HUMAN_SUBMIT"
    theirs = str(
        conn.execute("select id from applications where case_id = %s", (seeded["ravi"],)).fetchone()["id"]
    )
    assert (
        "no such application"
        in dispatch("transition_application", {"application_id": theirs, "to_state": "CLOSED"}, ctx)["error"]
    )
    assert (
        "unknown state"
        in dispatch("transition_application", {"application_id": mine, "to_state": "FLYING"}, ctx)["error"]
    )
    ok = make_app(conn, seeded["meena"], "UNDER_REVIEW")
    assert (
        dispatch(
            "transition_application",
            {"application_id": ok, "to_state": "QUERY_RAISED", "reason": "asked"},
            ctx,
        )["state"]
        == "QUERY_RAISED"
    )


def tool(name: str, **inp) -> dict:
    return {"content": [{"tool": name, "input": inp}]}


def test_brain_creates_application_for_meena_after_a_fit(seeded, conn, fake_claude) -> None:
    fake_claude.add(
        {
            "loop": [
                tool("update_profile", key="loan_amount", value=300000),
                tool("match_schemes"),
                tool("create_application", scheme_id="pmegp"),
                tool("finish_run", user_message="PMEGP के लिए आवेदन शुरू कर दिया है।"),
            ]
        }
    )
    result = run_brain(seeded["meena"], "start the application", "chat", conn=conn)
    assert result.status == "DONE"
    row = conn.execute(
        "select scheme_id, state from applications where case_id = %s", (seeded["meena"],)
    ).fetchone()
    assert (row["scheme_id"], row["state"]) == ("pmegp", "ELIGIBLE")
    case_file = fake_claude.calls_for("loop")[-1]["messages"][0]["content"]
    assert "CASE FILE" in case_file
