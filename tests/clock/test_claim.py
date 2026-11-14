"""Alarm Clock worker (LLD §10.2, §20): claim loop, no double-claims, backoff, DEAD + alert, sweeper."""

import threading
from datetime import UTC, datetime

import pytest

from app.clock import worker
from app.db import connect

pytestmark = pytest.mark.db


def add_jobs(conn, n: int, type_: str = "doc_nudge", case_id=None, run_in: float = -1) -> list[int]:
    ids = []
    for _ in range(n):
        row = conn.execute(
            "insert into jobs (type, case_id, run_at) values (%s, %s, now() + make_interval(secs => %s)) returning id",
            (type_, case_id, run_in),
        ).fetchone()
        ids.append(row["id"])
    conn.commit()
    return ids


def status_of(conn, job_id: int) -> dict:
    return conn.execute(
        "select status, attempts, last_error, run_at from jobs where id = %s", (job_id,)
    ).fetchone()


def test_two_workers_never_double_claim(settings_db, seeded) -> None:
    with connect(settings_db) as setup:
        setup.execute("delete from jobs")
        ids = add_jobs(setup, 60)
    claimed: list[list[int]] = [[], []]
    barrier = threading.Barrier(2)

    def work(slot: int) -> None:
        with connect(settings_db) as conn:
            barrier.wait()
            while (job := worker.claim_next_job(conn)) is not None:
                claimed[slot].append(job["id"])

    threads = [threading.Thread(target=work, args=(i,)) for i in range(2)]
    [t.start() for t in threads]
    [t.join(timeout=30) for t in threads]
    both = claimed[0] + claimed[1]
    assert sorted(both) == sorted(ids)  # every job claimed...
    assert len(both) == len(set(both))  # ...exactly once
    assert claimed[0] and claimed[1]  # and the work really was shared


def test_claim_takes_oldest_due_job_and_ignores_future_ones(settings_db, seeded, conn) -> None:
    conn.execute("delete from jobs")
    future = add_jobs(conn, 1, run_in=3600)[0]
    older = conn.execute(
        "insert into jobs (type, run_at) values ('doc_nudge', now() - interval '2 hours') returning id"
    ).fetchone()["id"]
    newer = add_jobs(conn, 1, run_in=-10)[0]
    first = worker.claim_next_job(conn)
    assert first["id"] == older and first["status"] == "RUNNING" and first["attempts"] == 1
    assert worker.claim_next_job(conn)["id"] == newer
    assert worker.claim_next_job(conn) is None  # the future job is not due
    assert status_of(conn, future)["status"] == "PENDING"


def test_backoff_sequence_then_dead_and_alert(settings_db, seeded, conn, monkeypatch) -> None:
    conn.execute("delete from jobs")
    job_id = add_jobs(conn, 1, type_="boom")[0]
    alerts = []
    monkeypatch.setattr(worker, "alert_dead_job", lambda job, error: alerts.append((job["id"], error)))

    def boom(conn, job):
        raise RuntimeError("provider is down")

    delays = []
    for attempt in range(1, worker.MAX_ATTEMPTS + 1):
        conn.execute(
            "update jobs set run_at = now() - interval '1 second' where id = %s and status = 'PENDING'",
            (job_id,),
        )
        conn.commit()
        job = worker.claim_next_job(conn)
        assert job["attempts"] == attempt
        new_status = worker.run_job(conn, job, handlers={"boom": boom})
        row = status_of(conn, job_id)
        if new_status == "PENDING":
            delays.append(round((row["run_at"] - datetime.now(UTC)).total_seconds() / 60))
    assert delays == [1, 10, 60, 360]  # 1 min, 10 min, 1 h, 6 h between tries 1→5
    final = status_of(conn, job_id)
    assert final["status"] == "DEAD" and "provider is down" in final["last_error"]
    assert alerts == [(job_id, "RuntimeError: provider is down")]


def test_unknown_job_type_is_retried_not_lost(settings_db, seeded, conn) -> None:
    conn.execute("delete from jobs")
    job_id = add_jobs(conn, 1, type_="nonsense")[0]
    assert worker.run_job(conn, worker.claim_next_job(conn)) == "PENDING"
    assert "no handler" in status_of(conn, job_id)["last_error"]


def test_successful_job_is_done_and_unlocked(settings_db, seeded, conn) -> None:
    conn.execute("delete from jobs")
    job_id = add_jobs(conn, 1)[0]
    assert (
        worker.run_job(conn, worker.claim_next_job(conn), handlers={"doc_nudge": lambda c, j: None}) == "DONE"
    )
    assert status_of(conn, job_id)["status"] == "DONE"


def test_a_failing_handler_leaves_no_half_written_rows(settings_db, seeded, conn) -> None:
    conn.execute("delete from jobs")
    job_id = add_jobs(conn, 1, case_id=seeded["meena"])[0]

    def half_then_boom(c, job):
        c.execute(
            "insert into messages (case_id, direction, channel, body) values (%s, 'out', 'web', 'partial')",
            (job["case_id"],),
        )
        raise RuntimeError("after the write")

    worker.run_job(conn, worker.claim_next_job(conn), handlers={"doc_nudge": half_then_boom})
    assert conn.execute("select count(*) as n from messages where body = 'partial'").fetchone()["n"] == 0
    assert status_of(conn, job_id)["status"] == "PENDING"


def test_sweeper_resets_only_stuck_running_jobs(settings_db, seeded, conn) -> None:
    conn.execute("delete from jobs")
    stuck = conn.execute(
        "insert into jobs (type, run_at, status, locked_at) values ('doc_nudge', now(), 'RUNNING', now() - interval '31 minutes') returning id"
    ).fetchone()["id"]
    busy = conn.execute(
        "insert into jobs (type, run_at, status, locked_at) values ('doc_nudge', now(), 'RUNNING', now() - interval '5 minutes') returning id"
    ).fetchone()["id"]
    conn.commit()
    assert worker.sweep_stuck(conn) == 1
    assert status_of(conn, stuck)["status"] == "PENDING" and status_of(conn, busy)["status"] == "RUNNING"


def test_tick_runs_one_due_job(settings_db, seeded, conn) -> None:
    conn.execute("delete from jobs")
    ids = add_jobs(conn, 2, type_="offer_domain")
    assert worker.tick(conn) and worker.tick(conn) and not worker.tick(conn)
    assert all(
        status_of(conn, i)["status"] == "DONE" for i in ids
    )  # offer_domain finishes quietly when flag is off
