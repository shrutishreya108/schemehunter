"""One run at a time per case, double-texts, visible failures, bounded runs, and the no-finish_run path.

FakeClaude and fake transports only. Each thread opens its own database connection, as the real server does.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest
from fake_claude import FakeClaude

from app.brain import runner
from app.brain.runner import run_brain
from app.config import get_settings
from app.db import connect

pytestmark = pytest.mark.db


class SlowFake(FakeClaude):
    """A FakeClaude whose every call takes `delay` seconds, so runs visibly overlap or do not."""

    def __init__(self, delay: float, scripts=None, fail: type[Exception] | None = None):
        super().__init__(scripts)
        self.delay = delay
        self.fail = fail

    def create(self, **kwargs):
        time.sleep(self.delay)
        if self.fail and kwargs.get("purpose") == "loop":
            raise self.fail("the API did not answer in time")
        return super().create(**kwargs)


def finish(text: str) -> dict:
    return {"content": [{"tool": "finish_run", "input": {"user_message": text}}]}


def inbound(url: str, case_id: str, text: str) -> str:
    with psycopg.connect(url) as c:
        row = c.execute(
            "insert into messages (case_id, direction, channel, body, processed) values (%s, 'in', 'web', %s, true) "
            "returning id",
            (case_id, text),
        ).fetchone()
        c.commit()
    return str(row[0])


def run_in_thread(case_id: str, task: str, trigger: str, client, message_id=None):
    with connect() as c:
        return run_brain(case_id, task, trigger, conn=c, client=client, message_id=message_id)


def interval(conn, run_id: str) -> tuple[datetime, datetime]:
    row = conn.execute("select started_at, ended_at from brain_runs where id = %s", (run_id,)).fetchone()
    return row["started_at"], row["ended_at"]


def overlaps(a: tuple, b: tuple) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def notices(conn, case_id: str) -> list[str]:
    rows = conn.execute(
        "select body from messages where case_id = %s and direction = 'out' and attachments->>'kind' = 'notice' "
        "order by created_at",
        (case_id,),
    ).fetchall()
    return [r["body"] for r in rows]


# ---------------------------------------------------------------- A: one run at a time per case


def test_two_runs_for_one_case_never_overlap_and_the_second_sees_the_first_answer(
    seeded, conn, settings_db
) -> None:
    """The second message arrives after the first run has started, so it is not skipped: two real runs."""
    case = seeded["meena"]
    m1 = inbound(settings_db, case, "first question")
    fake = SlowFake(0.4, {"loop": [finish("first reply"), finish("second reply")]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run_in_thread, case, "t", "chat", fake, m1)
        time.sleep(0.15)  # run A has started (and read its history) before the second message is sent
        m2 = inbound(settings_db, case, "second question")
        b = pool.submit(run_in_thread, case, "t", "chat", fake, m2)
        ra, rb = a.result(), b.result()
    assert ra.status == "DONE" and rb.status == "DONE"
    first, second = sorted([ra, rb], key=lambda r: interval(conn, r.run_id)[0])
    assert not overlaps(interval(conn, first.run_id), interval(conn, second.run_id))
    assert any("first reply" in str(c["messages"]) for c in fake.calls_for("loop"))  # the later run saw it


def test_runs_for_different_cases_overlap(seeded, conn, settings_db) -> None:
    fake = SlowFake(0.5, {"loop": [finish("for meena"), finish("for ravi")]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run_in_thread, seeded["meena"], "t", "chat", fake)
        b = pool.submit(run_in_thread, seeded["ravi"], "t", "chat", fake)
        ra, rb = a.result(), b.result()
    assert ra.status == "DONE" and rb.status == "DONE"
    assert overlaps(interval(conn, ra.run_id), interval(conn, rb.run_id))


def test_a_worker_job_and_a_chat_run_for_one_case_serialise(seeded, conn) -> None:
    case = seeded["meena"]
    fake = SlowFake(0.4, {"loop": [finish("job reply"), finish("chat reply")]})
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(run_in_thread, case, "job task", "clock", fake)
        b = pool.submit(run_in_thread, case, "chat task", "chat", fake)
        ra, rb = a.result(), b.result()
    assert ra.status == "DONE" and rb.status == "DONE"
    assert not overlaps(interval(conn, ra.run_id), interval(conn, rb.run_id))


# ---------------------------------------------------------------- B: double-texts


def test_three_quick_messages_give_one_or_two_runs_and_skip_the_rest(seeded, conn, settings_db) -> None:
    case = seeded["meena"]
    ids = [inbound(settings_db, case, f"message {i}") for i in (1, 2, 3)]
    fake = SlowFake(0.2, {"loop": [finish("reply A"), finish("reply B"), finish("reply C")]})
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(run_in_thread, case, "t", "chat", fake, mid) for mid in ids]
        results = [f.result() for f in futures]
    statuses = [r.status for r in results]
    ran = [r for r in results if r.status != "SKIPPED"]
    assert 1 <= len(ran) <= 2
    assert statuses.count("SKIPPED") >= 1
    skipped_rows = conn.execute(
        "select estimated_cost_usd from brain_runs where case_id = %s and status = 'SKIPPED'", (case,)
    ).fetchall()
    assert all(float(r["estimated_cost_usd"]) == 0 for r in skipped_rows)
    replies = conn.execute(
        "select count(*) as n from messages where case_id = %s and direction = 'out' and attachments->>'kind' = 'reply'",
        (case,),
    ).fetchone()
    assert replies["n"] <= len(ran)
    assert len(fake.calls_for("loop")) <= 2 * 12  # no more model runs than two runs can use


def test_skipped_runs_do_not_count_toward_the_case_cap(seeded, conn) -> None:
    from app.brain.runner import runs_today

    conn.execute(
        "insert into brain_runs (case_id, trigger, task, steps, status, error, started_at, ended_at) "
        "values (%s, 'chat', 't', 0, 'SKIPPED', 'coalesced', now(), now())",
        (seeded["meena"],),
    )
    conn.commit()
    assert runs_today(conn, seeded["meena"], get_settings().BUDGET_TZ) == 0


# ---------------------------------------------------------------- C: failures are visible


def test_an_api_timeout_ends_as_error_with_a_notice_and_the_page_stops_waiting(seeded, conn, settings_db) -> None:
    from app.routes.chat import is_pending, load_chat

    fake = SlowFake(0.0, {"loop": []}, fail=httpx.ReadTimeout)
    result = run_brain(seeded["meena"], "t", "chat", conn=conn, client=fake)
    assert result.status == "ERROR" and result.reason == "ReadTimeout"
    assert notices(conn, seeded["meena"]) == [runner.LATER["en"]]
    msgs = load_chat(conn, seeded["meena"])
    assert msgs[-1]["body"] == runner.LATER["en"]
    assert is_pending(conn, seeded["meena"], msgs) is False


def test_a_run_over_budget_saves_the_tomorrow_notice(seeded, conn, monkeypatch) -> None:
    monkeypatch.setattr(runner, "deferral_reason", lambda *a, **k: "budget")
    result = run_brain(seeded["meena"], "t", "chat", conn=conn, client=SlowFake(0.0))
    assert result.status == "DEFERRED"
    assert notices(conn, seeded["meena"]) == [runner.TOMORROW["en"]]


def test_a_lock_wait_longer_than_the_timeout_ends_as_error_with_a_notice(seeded, conn, settings_db, monkeypatch) -> None:
    case = seeded["meena"]
    holder = psycopg.connect(settings_db)
    holder.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"brain:{case}",))
    monkeypatch.setattr(get_settings(), "LOCK_TIMEOUT_S", 1, raising=False)
    try:
        result = run_brain(case, "t", "chat", conn=conn, client=SlowFake(0.0, {"loop": [finish("x")]}))
    finally:
        holder.rollback()
        holder.close()
    assert result.status == "ERROR"
    assert notices(conn, case) == [runner.LATER["en"]]


# ---------------------------------------------------------------- D: bounded runs


def test_a_model_that_never_finishes_stops_at_the_deadline_as_stuck(seeded, conn, monkeypatch) -> None:
    monkeypatch.setattr(runner, "alert_curator", lambda *a: None)
    monkeypatch.setattr(get_settings(), "CHAT_RUN_DEADLINE_S", 0.2, raising=False)
    looping = {"content": [{"tool": "match_schemes", "input": {}}]}
    fake = SlowFake(0.08, {"loop": [looping] * 30})
    result = run_brain(seeded["meena"], "t", "chat", conn=conn, client=fake)
    assert result.status == "STUCK"
    assert conn.execute("select error from brain_runs where id = %s", (result.run_id,)).fetchone()["error"] == "deadline"
    assert notices(conn, seeded["meena"]) == [runner.STUCK["en"]]


def test_a_stale_run_is_closed_once_with_one_notice(seeded, conn) -> None:
    conn.execute(
        "insert into brain_runs (case_id, trigger, task, steps, status, started_at) "
        "values (%s, 'chat', 't', 0, 'RUNNING', now() - interval '11 minutes')",
        (seeded["meena"],),
    )
    conn.commit()
    before = len(notices(conn, seeded["meena"]))
    assert runner.close_stale_runs(conn) == 1
    assert runner.close_stale_runs(conn) == 0
    row = conn.execute(
        "select status, error, ended_at from brain_runs where case_id = %s and error = 'stale'", (seeded["meena"],)
    ).fetchone()
    assert row["status"] == "ERROR" and row["ended_at"] is not None
    assert len(notices(conn, seeded["meena"])) == before + 1


def test_the_page_polls_while_a_run_is_open_and_stops_after(seeded, conn) -> None:
    from app.routes.chat import gave_up, is_pending, load_chat

    case = seeded["meena"]
    conn.execute(
        "insert into messages (case_id, direction, channel, body, processed) values (%s, 'in', 'web', 'hi', true)",
        (case,),
    )
    conn.execute(
        "insert into brain_runs (case_id, trigger, task, steps, status, started_at) "
        "values (%s, 'chat', 't', 0, 'RUNNING', now())",
        (case,),
    )
    conn.commit()
    msgs = load_chat(conn, case)
    assert is_pending(conn, case, msgs) is True and gave_up(conn, case, msgs) is False


def test_the_page_says_so_when_a_run_never_answered(seeded, conn) -> None:
    from app.routes.chat import gave_up, is_pending, load_chat

    case = seeded["meena"]
    old = datetime.now(UTC) - timedelta(minutes=30)
    conn.execute(
        "insert into messages (case_id, direction, channel, body, processed, created_at) "
        "values (%s, 'in', 'web', 'still waiting', true, %s)",
        (case, old),
    )
    conn.execute(
        "insert into brain_runs (case_id, trigger, task, steps, status, started_at, ended_at) "
        "values (%s, 'chat', 't', 0, 'ERROR', %s, %s)",
        (case, old, old),
    )
    conn.commit()
    msgs = load_chat(conn, case)
    assert is_pending(conn, case, msgs) is False and gave_up(conn, case, msgs) is True


# ---------------------------------------------------------------- H: a run that ends without finish_run


def test_finish_run_after_one_nudge_gives_one_reply(seeded, conn) -> None:
    fake = SlowFake(0.0, {"loop": [{"content": [{"text": "let me think"}]}, finish("the answer")]})
    result = run_brain(seeded["meena"], "t", "chat", conn=conn, client=fake)
    assert result.status == "DONE" and result.user_message == "the answer"
    assert conn.execute("select error from brain_runs where id = %s", (result.run_id,)).fetchone()["error"] == "nudged"
    replies = conn.execute(
        "select count(*) as n from messages where case_id = %s and body = 'the answer'", (seeded["meena"],)
    ).fetchone()
    assert replies["n"] == 1


def test_text_after_the_nudge_is_the_reply_once(seeded, conn) -> None:
    fake = SlowFake(0.0, {"loop": [{"content": [{"text": "Your answer is here."}]}, {"content": [{"text": "Your answer is here."}]}]})
    result = run_brain(seeded["meena"], "t", "chat", conn=conn, client=fake)
    assert result.status == "DONE_FALLBACK" and result.user_message == "Your answer is here."
    assert conn.execute("select error from brain_runs where id = %s", (result.run_id,)).fetchone()["error"] == "text_fallback"
    n = conn.execute(
        "select count(*) as n from messages where case_id = %s and body = 'Your answer is here.'", (seeded["meena"],)
    ).fetchone()
    assert n["n"] == 1


def test_a_promise_in_fallback_text_is_replaced_by_the_safe_sentence(seeded, conn) -> None:
    from app.brain.tools.finish_run import FALLBACK

    promise = "You will get the money for sure."
    fake = SlowFake(0.0, {"loop": [{"content": [{"text": promise}]}, {"content": [{"text": promise}]}]})
    result = run_brain(seeded["meena"], "t", "chat", conn=conn, client=fake)
    assert result.status == "DONE_FALLBACK" and result.user_message == FALLBACK["en"]
    assert conn.execute("select error from brain_runs where id = %s", (result.run_id,)).fetchone()["error"] == "text_blocked"
    assert conn.execute(
        "select count(*) as n from messages where case_id = %s and body = %s", (seeded["meena"], promise)
    ).fetchone()["n"] == 0
