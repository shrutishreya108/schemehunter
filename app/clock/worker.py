"""Alarm Clock worker: claim loop, backoff, sweeper (LLD §10.2). `python -m app.clock.worker`."""

BACKOFF = [60, 600, 3600, 6 * 3600, 24 * 3600]
MAX_ATTEMPTS = 5

CLAIM_SQL = """
update jobs set status='RUNNING', locked_at=now(), attempts=attempts+1
where id = (select id from jobs where status='PENDING' and run_at <= now()
            order by run_at for update skip locked limit 1)
returning *;
"""


def claim_next_job() -> dict | None:
    raise NotImplementedError("slice 6")


def run_forever() -> None:
    raise NotImplementedError("slice 6")


def sweep_stuck() -> int:
    """RUNNING older than 30 min → PENDING (every 10 min)."""
    raise NotImplementedError("slice 6")


if __name__ == "__main__":
    run_forever()
