"""Alarm Clock worker: claim loop, backoff, sweeper (LLD §10.2). `python -m app.clock.worker`.

No Redis, no queue system: Postgres `FOR UPDATE SKIP LOCKED` makes two workers safe. A job that fails is retried
after 1 min, 10 min, 1 h, 6 h, 24 h; the 5th failure marks it DEAD and emails the curator.
"""

import logging
import time
from datetime import datetime, timedelta

import psycopg

from app.config import get_settings

log = logging.getLogger(__name__)

BACKOFF = [60, 600, 3600, 6 * 3600, 24 * 3600]
MAX_ATTEMPTS = 5
POLL_SECONDS = 5
SWEEP_EVERY_SECONDS = 600
STUCK_AFTER_MINUTES = 30

CLAIM_SQL = """
update jobs set status='RUNNING', locked_at=now(), attempts=attempts+1
where id = (select id from jobs where status='PENDING' and run_at <= now()
            order by run_at for update skip locked limit 1)
returning *;
"""


def claim_next_job(conn: psycopg.Connection) -> dict | None:
    """Atomically take the oldest due job. The claim is committed at once, so other workers skip it."""
    job = conn.execute(CLAIM_SQL).fetchone()
    conn.commit()
    return job


def mark(conn: psycopg.Connection, job_id: int, status: str, error: str | None = None) -> None:
    conn.execute(
        "update jobs set status = %s, last_error = %s, locked_at = null where id = %s",
        (status, error, job_id),
    )
    conn.commit()


def reschedule(conn: psycopg.Connection, job: dict, delay_seconds: int, error: str) -> None:
    conn.execute(
        "update jobs set status = 'PENDING', run_at = now() + make_interval(secs => %s), last_error = %s, "
        "locked_at = null where id = %s",
        (delay_seconds, error[:300], job["id"]),
    )
    conn.commit()


def alert_dead_job(job: dict, error: str) -> None:
    """Email the curator. Never raises: an alert failure must not stop the worker."""
    s = get_settings()
    if not s.CURATOR_ALERT_EMAIL:
        log.warning("dead job %s %s (no CURATOR_ALERT_EMAIL): %s", job["id"], job["type"], error)
        return
    try:
        from app.mouth.email import SmtpEmail

        SmtpEmail.from_settings().send(
            s.CURATOR_ALERT_EMAIL,
            f"Job {job['id']} ({job['type']}) failed {job['attempts']} times and is DEAD.\n"
            f"Last error: {error}\nSee {s.APP_BASE_URL}/admin/jobs",
            subject="[Scheme Hunter] job is DEAD",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("dead-job alert failed: %s", type(exc).__name__)


def run_job(conn: psycopg.Connection, job: dict, handlers: dict | None = None) -> str:
    """Run one claimed job. Returns the job's new status: DONE, PENDING (retry) or DEAD."""
    from app.clock.handlers import HANDLERS

    handler = (handlers or HANDLERS).get(job["type"])
    try:
        if handler is None:
            raise LookupError(f"no handler for job type {job['type']!r}")
        handler(conn, job)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - every handler failure is retried with backoff
        conn.rollback()
        error = f"{type(exc).__name__}: {exc}"[:300]
        attempts = job["attempts"]
        if attempts >= MAX_ATTEMPTS:
            mark(conn, job["id"], "DEAD", error)
            alert_dead_job(job, error)
            return "DEAD"
        reschedule(conn, job, BACKOFF[min(attempts, len(BACKOFF)) - 1], error)
        return "PENDING"
    mark(conn, job["id"], "DONE")
    return "DONE"


def sweep_stuck(conn: psycopg.Connection) -> int:
    """RUNNING for more than 30 minutes (the worker died mid-job) → PENDING again."""
    rows = conn.execute(
        "update jobs set status = 'PENDING', locked_at = null where status = 'RUNNING' "
        "and locked_at < now() - make_interval(mins => %s) returning id",
        (STUCK_AFTER_MINUTES,),
    ).fetchall()
    conn.commit()
    return len(rows)


def housekeeping(conn: psycopg.Connection) -> None:
    """Things that aren't jobs: expire old approvals, retry failed sends, tidy the rate-limit table."""
    from app.guard import actions

    try:
        actions.expire(conn)
        actions.retry_failed_sends(conn)
        conn.execute("delete from rate_limits where window_start < now() - interval '1 day'")
        conn.execute("delete from login_challenges where created_at < now() - interval '24 hours'")
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        log.warning("housekeeping failed: %s", type(exc).__name__)


def tick(conn: psycopg.Connection) -> bool:
    """Run at most one due job. True if one ran."""
    job = claim_next_job(conn)
    if not job:
        return False
    run_job(conn, job)
    return True


def run_forever() -> None:
    from app.db import connect

    last_sweep = datetime.min
    while True:
        try:
            with connect() as conn:
                while True:
                    if tick(conn):
                        continue
                    if datetime.now() - last_sweep > timedelta(seconds=SWEEP_EVERY_SECONDS):
                        sweep_stuck(conn)
                        housekeeping(conn)
                        last_sweep = datetime.now()
                    time.sleep(POLL_SECONDS)
        except psycopg.OperationalError as exc:  # database restarting: wait and reconnect
            log.warning("worker lost the database (%s); retrying", type(exc).__name__)
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
