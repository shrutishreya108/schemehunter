"""Curator pages behind basic auth. /admin/runs: per-run cost, running total, today vs DAILY_BUDGET_USD."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from app.access import rate_limit, require_admin
from app.brain.runner import day_start
from app.config import get_settings
from app.db import connect
from app.events import log_event
from app.i18n import bilingual
from app.routes.render import render
from app.signin import challenges as signin_challenges
from app.signin.emailnorm import mask_email

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def runs_with_running_total(conn, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "select id, case_id, trigger, status, steps, input_tokens, output_tokens, cache_read_tokens, "
        "cache_write_tokens, estimated_cost_usd, error, started_at, ended_at, "
        "sum(estimated_cost_usd) over (order by started_at, id) as running_total "
        "from brain_runs order by started_at desc, id desc limit %s",
        (limit,),
    ).fetchall()
    return [
        {
            **r,
            "estimated_cost_usd": float(r["estimated_cost_usd"] or 0),
            "running_total": float(r["running_total"] or 0),
            "duration_s": (r["ended_at"] - r["started_at"]).total_seconds() if r["ended_at"] and r["started_at"] else None,
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


@router.get("/web")
def web_pages_view(request: Request):
    """Each curated official web page: when it was read, its last status and error, and how old the copy is."""
    with connect() as conn:
        rows = conn.execute(
            "select url, scheme_id, title, fetched_at, last_status, error, "
            "case when text is null then 0 else length(text) end as chars from web_cache order by scheme_id, url"
        ).fetchall()
    now = datetime.now(UTC)
    pages = [
        {
            **dict(r),
            "age_days": (now - r["fetched_at"]).days if r["fetched_at"] else None,
            "old": bool(r["fetched_at"]) and (now - r["fetched_at"]).days > get_settings().WEB_SNAPSHOT_WARN_DAYS,
        }
        for r in rows
    ]
    return render(request, "admin/web.html", "en", pages=pages)


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
        sign_in_failures = conn.execute(
            "select created_at, purpose, email_norm, send_error from login_challenges "
            "where send_error is not null and created_at > now() - interval '7 days' "
            "order by created_at desc limit 100"
        ).fetchall()
    failures = [{**r, "masked": mask_email(r["email_norm"])} for r in sign_in_failures]
    return render(
        request,
        "admin/actions.html",
        "en",
        problems=problems,
        waiting=waiting,
        unsent=unsent,
        sign_in_failures=failures,
    )


@router.post("/cases/{case_id}/signin-link")
def issue_signin_link(request: Request, case_id: str, background: BackgroundTasks):
    """Break-glass for someone who lost email access: a one-time link, valid 60 minutes (LLD signin §6).

    The link is shown once on this page. If the case has an address, it is also emailed in the background.
    """
    from app.routes.signin import _send_later

    try:
        UUID(case_id)
    except ValueError:
        raise HTTPException(404, "case not found") from None
    with connect() as conn:
        case = conn.execute("select email from cases where id = %s", (case_id,)).fetchone()
        if not case:
            raise HTTPException(404, "case not found")
        if not rate_limit(conn, "admin:signin-link", 10, 3600):
            raise HTTPException(429, "too many links; wait an hour")
        email_norm = case["email"] or "no-email@invalid.example"
        row, _, token = signin_challenges.create_challenge(conn, email_norm, "admin", "en", case_id=case_id)
        log_event(
            conn,
            case_id,
            "system",
            "ADMIN_SIGNIN_LINK",
            bilingual(
                "A one-time sign-in link was issued by the curator", "क्यूरेटर ने एक बार का साइन-इन लिंक जारी किया"
            ),
            payload={"challenge_id": str(row["id"])},  # ids only: no token, no address
        )
    if case["email"]:
        background.add_task(_send_later, row["id"], case["email"], None, token, "en")
    link = f"{get_settings().APP_BASE_URL.rstrip('/')}/signin/link?t={token}"
    shown = mask_email(case["email"]) if case["email"] else "(no email on file)"
    resp = render(request, "admin/case_link.html", "en", link=link, shown=shown)
    resp.background = background
    return resp
