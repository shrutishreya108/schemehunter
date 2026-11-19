"""run_brain(): one short run (LLD §7.1, §7.5, §7.6 + v0.3 budget rules).

Before the first Claude call:
  - DAILY_BUDGET_USD: if today's estimated spend is at/over budget, only deadline-related runs proceed;
    others are recorded as DEFERRED and re-queued for tomorrow.
  - MAX_RUNS_PER_CASE_PER_DAY: a case over its cap is DEFERRED the same way.
Then: ≤ MAX_STEPS_PER_RUN loop steps; every tool result goes back to Claude; finish_run is required.
No finish_run (cap hit, or the model just stopped) → RUN_STUCK event + curator alert.
Usage and estimated_cost_usd are written to brain_runs whatever happens.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg

from app.brain import claude
from app.brain.casefile import build_case_file
from app.brain.tools import TOOLS, dispatch
from app.brain.tools.base import Ctx
from app.config import get_settings
from app.costs import UsageMeter
from app.db import jsonb
from app.events import log_event
from app.i18n import bilingual
from app.rules.loader import load_schemes

log = logging.getLogger(__name__)

STUCK = {"en": "I got stuck; will try again later", "hi": "मैं अटक गई; थोड़ी देर बाद फिर कोशिश करूँगी"}
LATER = {"en": "I'll get back to you shortly.", "hi": "मैं थोड़ी देर में आपको जवाब दूँगी।"}
TOMORROW = {
    "en": "I've reached today's limit. I'll continue tomorrow.",
    "hi": "आज की सीमा पूरी हो गई। मैं कल आगे बढ़ूँगी।",
}


@dataclass
class RunResult:
    run_id: str | None
    status: str  # DONE | STUCK | ERROR | DEFERRED
    steps: int
    user_message: str
    cost_usd: float
    reason: str | None = None


def day_start(tz: str, now: datetime | None = None) -> datetime:
    local = (now or datetime.now(ZoneInfo(tz))).astimezone(ZoneInfo(tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def spent_today(conn: psycopg.Connection, tz: str) -> float:
    row = conn.execute(
        "select coalesce(sum(estimated_cost_usd), 0) as usd from brain_runs where started_at >= %s",
        (day_start(tz),),
    ).fetchone()
    return float(row["usd"])


def runs_today(conn: psycopg.Connection, case_id: UUID | str, tz: str) -> int:
    row = conn.execute(
        "select count(*) as n from brain_runs where case_id = %s and started_at >= %s "
        "and status <> 'DEFERRED'",
        (case_id, day_start(tz)),
    ).fetchone()
    return int(row["n"])


def deferral_reason(conn: psycopg.Connection, case_id: UUID | str, deadline_related: bool) -> str | None:
    s = get_settings()
    if deadline_related:
        return None
    if spent_today(conn, s.BUDGET_TZ) >= s.DAILY_BUDGET_USD:
        return "budget"
    if runs_today(conn, case_id, s.BUDGET_TZ) >= s.MAX_RUNS_PER_CASE_PER_DAY:
        return "case_cap"
    return None


def defer(
    conn: psycopg.Connection, case_id: UUID | str, task: str, trigger: str, reason: str, lang: str
) -> RunResult:
    tz = get_settings().BUDGET_TZ
    row = conn.execute(
        "insert into brain_runs (case_id, trigger, task, steps, status, error, estimated_cost_usd, started_at, "
        "ended_at) values (%s, %s, %s, 0, 'DEFERRED', %s, 0, now(), now()) returning id",
        (case_id, trigger, task, reason),
    ).fetchone()
    conn.execute(
        "insert into jobs (type, case_id, payload, run_at) values ('brain_run', %s, %s, %s)",
        (
            case_id,
            jsonb({"task": task, "trigger": trigger, "deferred": reason}),
            day_start(tz) + timedelta(days=1, minutes=5),
        ),
    )
    conn.commit()
    return RunResult(str(row["id"]), "DEFERRED", 0, TOMORROW.get(lang, TOMORROW["en"]), 0.0, reason)


def start_run(conn: psycopg.Connection, case_id: UUID | str, task: str, trigger: str) -> str:
    row = conn.execute(
        "insert into brain_runs (case_id, trigger, task, steps, status, started_at, model) "
        "values (%s, %s, %s, 0, 'RUNNING', now(), %s) returning id",
        (case_id, trigger, task, get_settings().MODEL_REASON),
    ).fetchone()
    conn.commit()
    return str(row["id"])


def end_run(
    conn: psycopg.Connection,
    run_id: str,
    steps: int,
    status: str,
    meter: UsageMeter,
    error: str | None = None,
) -> None:
    t = meter.tokens
    conn.execute(
        "update brain_runs set steps = %s, status = %s, error = %s, input_tokens = %s, output_tokens = %s, "
        "cache_read_tokens = %s, cache_write_tokens = %s, estimated_cost_usd = %s, ended_at = now() where id = %s",
        (
            steps,
            status,
            error,
            t["input"],
            t["output"],
            t["cache_read"],
            t["cache_write"],
            round(meter.cost, 6),
            run_id,
        ),
    )
    conn.commit()


def alert_curator(run_id: str, case_id: UUID | str, reason: str) -> None:
    """Email the curator. Never raises: an alert failure must not break the run."""
    s = get_settings()
    if not s.CURATOR_ALERT_EMAIL:
        log.warning("curator alert (no CURATOR_ALERT_EMAIL): run=%s reason=%s", run_id, reason)
        return
    try:
        from app.mouth.email import SmtpEmail

        SmtpEmail.from_settings().send(
            s.CURATOR_ALERT_EMAIL,
            f"Run {run_id} for case {case_id}: {reason}.\nSee {s.APP_BASE_URL}/admin/runs",
            subject=f"[Scheme Hunter] {reason}",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("curator alert failed: %s", type(exc).__name__)


def first_message(task: str, case_file: dict) -> dict:
    return {
        "role": "user",
        "content": f"TASK: {task}\n\nCASE FILE:\n{json.dumps(case_file, ensure_ascii=False)}",
    }


def run_loop(ctx: Ctx, task: str, system: list[dict], tools: list[dict]) -> int:
    messages = [first_message(task, build_case_file(ctx.case_id, ctx.conn))]
    steps = 0
    for _ in range(get_settings().MAX_STEPS_PER_RUN):
        steps += 1
        resp = claude.loop_call(system, tools, messages, ctx.meter, client=ctx.client)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            break
        results: list[dict[str, Any]] = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            out = dispatch(block.name, block.input, ctx)
            ok = "error" not in out
            log_event(
                ctx.conn,
                ctx.case_id,
                "agent",
                "TOOL_CALLED",
                bilingual(
                    f"Tool {block.name}: {'ok' if ok else 'error'}",
                    f"टूल {block.name}: {'ठीक' if ok else 'त्रुटि'}",
                ),
                payload={"tool": block.name, "ok": ok, "run_id": ctx.run_id},
            )
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(out, ensure_ascii=False, default=str),
                    "is_error": not ok,
                }
            )
        messages.append({"role": "user", "content": results})
        ctx.conn.commit()
        if ctx.finished:
            break
    return steps


def run_brain(
    case_id: UUID | str,
    task: str,
    trigger: str,
    *,
    conn: psycopg.Connection | None = None,
    deadline_related: bool = False,
    client: Any = None,
) -> RunResult:
    if conn is None:
        from app.db import connect

        with connect() as c:
            return run_brain(case_id, task, trigger, conn=c, deadline_related=deadline_related, client=client)

    case = conn.execute("select id, language, state_code from cases where id = %s", (case_id,)).fetchone()
    if not case:
        raise LookupError(f"case {case_id} not found")
    lang = case["language"] or "en"
    reason = deferral_reason(conn, case_id, deadline_related)
    if reason:
        return defer(conn, case_id, task, trigger, reason, lang)

    run_id = start_run(conn, case_id, task, trigger)
    schemes = load_schemes()
    ctx = Ctx(
        case_id=str(case_id),
        conn=conn,
        schemes=schemes,
        language=lang,
        state_code=case["state_code"],
        run_id=run_id,
        client=client,
    )
    system = claude.build_system_blocks(claude.prompt("system.md"), claude.rulebook_summary(schemes))
    tools = claude.cached_tools(TOOLS)
    steps = 0
    try:
        steps = run_loop(ctx, task, system, tools)
    except Exception as exc:  # noqa: BLE001 - API/network failure: record, tell the user politely
        conn.rollback()
        log.warning("run %s failed: %s", run_id, type(exc).__name__)
        end_run(conn, run_id, steps, "ERROR", ctx.meter, error=type(exc).__name__)
        return RunResult(
            run_id, "ERROR", steps, LATER.get(lang, LATER["en"]), ctx.meter.cost, type(exc).__name__
        )

    if not ctx.finished:
        log_event(
            conn,
            case_id,
            "system",
            "RUN_STUCK",
            bilingual(STUCK["en"], STUCK["hi"]),
            payload={"run_id": run_id, "steps": steps},
        )
        end_run(conn, run_id, steps, "STUCK", ctx.meter)
        alert_curator(run_id, case_id, "RUN_STUCK")
        return RunResult(run_id, "STUCK", steps, STUCK.get(lang, STUCK["en"]), ctx.meter.cost)

    end_run(conn, run_id, steps, "DONE", ctx.meter)
    return RunResult(run_id, "DONE", steps, ctx.user_message, ctx.meter.cost)
