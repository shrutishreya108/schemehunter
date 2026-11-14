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
def jobs() -> None:
    raise NotImplementedError("slice 10")


@router.get("/actions")
def actions() -> None:
    raise NotImplementedError("slice 10")
