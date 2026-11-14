"""Curator pages behind basic auth. /admin/runs: per-run cost, running total, today vs DAILY_BUDGET_USD."""

from fastapi import APIRouter, Depends, Request

from app.access import require_admin
from app.brain.runner import day_start
from app.config import get_settings
from app.db import connect
from app.routes.render import render

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def runs_with_running_total(conn, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "select id, case_id, trigger, status, steps, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, estimated_cost_usd, error, started_at, "
        "sum(estimated_cost_usd) over (order by started_at, id) as running_total "
        "from brain_runs order by started_at desc, id desc limit %s",
        (limit,),
    ).fetchall()
    return [
        {
            **r,
            "estimated_cost_usd": float(r["estimated_cost_usd"] or 0),
            "running_total": float(r["running_total"] or 0),
        }
        for r in rows
    ]


def spend_summary(conn) -> dict:
    s = get_settings()
    row = conn.execute(
        "select coalesce(sum(estimated_cost_usd), 0) as total, "
        "coalesce(sum(estimated_cost_usd) filter (where started_at >= %s), 0) as today from brain_runs",
        (day_start(s.BUDGET_TZ),),
    ).fetchone()
    return {"total": float(row["total"]), "today": float(row["today"]), "budget": s.DAILY_BUDGET_USD}


@router.get("/runs")
def runs(request: Request):
    with connect() as conn:
        rows = runs_with_running_total(conn)
        summary = spend_summary(conn)
    return render(request, "admin/runs.html", "en", runs=rows, summary=summary)


@router.get("/jobs")
def jobs(request: Request):
    with connect() as conn:
        counts = {
            r["status"]: r["n"]
            for r in conn.execute("select status, count(*) as n from jobs group by status order by status")
        }
        dead = conn.execute(
            "select id, type, case_id, attempts, last_error, run_at from jobs where status = 'DEAD' order by run_at desc limit 100"
        ).fetchall()
        upcoming = conn.execute(
            "select id, type, case_id, status, run_at, attempts from jobs where status in ('PENDING', 'RUNNING') "
            "order by run_at limit 100"
        ).fetchall()
    return render(request, "admin/jobs.html", "en", counts=counts, dead=dead, upcoming=upcoming)


@router.get("/actions")
def actions(request: Request):
    with connect() as conn:
        problems = conn.execute(
            "select created_at, case_id, kind, status, attempts, error from pending_actions "
            "where status = 'FAILED' order by created_at desc limit 100"
        ).fetchall()
        waiting = conn.execute(
            "select created_at, case_id, kind, short_code from pending_actions where status = 'PENDING' "
            "order by created_at limit 100"
        ).fetchall()
        # Contact-form rows are stored before sending (T10): an unprocessed one never reached the mailbox.
        unsent = conn.execute(
            "select created_at, from_addr, body from messages where channel = 'web' and direction = 'in' "
            "and case_id is null and processed = false order by created_at desc limit 100"
        ).fetchall()
    return render(request, "admin/actions.html", "en", problems=problems, waiting=waiting, unsent=unsent)
