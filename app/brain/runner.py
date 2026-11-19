"""run_brain(): one short run (LLD §7.1, §7.5, §7.6 + v0.3 budget rules + one-run-per-case).

Order inside a run:
  1. take the case's advisory lock on its own connection; it is held until the run ends (case_lock). Two runs for one
     case never overlap; runs for different cases do.
  2. a chat run whose message was already seen by a later chat run is SKIPPED: no model call, no reply.
  3. DAILY_BUDGET_USD and MAX_RUNS_PER_CASE_PER_DAY: a run over either is DEFERRED with a notice (SKIPPED runs and
     deferred runs do not count).
  4. up to MAX_STEPS_PER_RUN steps within the run's wall-clock deadline. finish_run ends the run. If the model stops
     with text and no finish_run, it is asked once more; then its text is used (DONE_FALLBACK) or the run is STUCK.
Every ERROR, STUCK and DEFERRED outcome also writes a short notice to the chat, so a failure is never silent.
Usage and estimated_cost_usd are written to brain_runs whatever happens.
"""

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row

from app.brain import claude
from app.brain.casefile import build_case_file
from app.brain.history import conversation
from app.brain.tools import TOOLS, dispatch
from app.brain.tools.base import Ctx, scan_text
from app.brain.tools.finish_run import FALLBACK as SAFE_REPLY
from app.config import get_settings
from app.costs import UsageMeter
from app.db import jsonb
from app.events import log_event
from app.i18n import bilingual
from app.mouth.outbox import post_notice
from app.rules.loader import load_schemes

log = logging.getLogger(__name__)

STUCK = {"en": "I got stuck; will try again later", "hi": "मैं अटक गई; थोड़ी देर बाद फिर कोशिश करूँगी"}
LATER = {"en": "I'll get back to you shortly.", "hi": "मैं थोड़ी देर में आपको जवाब दूँगी।"}
TOMORROW = {
    "en": "I've reached today's limit. I'll continue tomorrow.",
    "hi": "आज की सीमा पूरी हो गई। मैं कल आगे बढ़ूँगी।",
}
NUDGE = "Call finish_run now with your reply for the user."
COALESCED = "coalesced"
# statuses of runs that really read the conversation (a SKIPPED or DEFERRED run did not)
SEEN_STATUSES = ["RUNNING", "DONE", "DONE_FALLBACK", "STUCK", "ERROR"]
NOT_COUNTED = ("DEFERRED", "SKIPPED")


@dataclass
class RunResult:
    run_id: str | None
    status: str  # DONE | DONE_FALLBACK | STUCK | ERROR | DEFERRED | SKIPPED
    steps: int
    user_message: str
    cost_usd: float
    reason: str | None = None


@dataclass
class LoopOutcome:
    steps: int
    path: str | None  # None: finish_run; "nudged": finish_run after the nudge; "text_fallback"; "deadline"; "no_reply"
    fallback_text: str | None = None  # the model's last plain text, when finish_run never came
    timed_out: bool = False


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
        "and status not in ('DEFERRED', 'SKIPPED')",
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


def case_lock(case_id: UUID | str, url: str | None = None) -> psycopg.Connection:
    """Transaction-scoped advisory lock on one case, held by its own connection until the run ends.

    The run's own connection commits many times, which would release a transaction lock early, so the lock lives on
    a separate connection. Raises if the lock is not granted within LOCK_TIMEOUT_S.
    """
    s = get_settings()
    lock_conn = psycopg.connect(url or s.DATABASE_URL, row_factory=dict_row)
    try:
        lock_conn.execute(f"set local lock_timeout = '{int(s.LOCK_TIMEOUT_S)}s'")
        lock_conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"brain:{case_id}",))
    except Exception:
        lock_conn.rollback()
        lock_conn.close()
        raise
    return lock_conn


def _plain_text(content: list[Any]) -> str:
    return "".join(getattr(b, "text", "") or "" for b in content if getattr(b, "type", None) == "text").strip()


def run_loop(
    ctx: Ctx,
    task: str,
    system: list[dict],
    tools: list[dict],
    use_history: bool = False,
    deadline: float | None = None,
) -> LoopOutcome:
    case_file = build_case_file(ctx.case_id, ctx.conn)
    if use_history:  # chat: the real turns go in as messages, so the summary of them is dropped
        case_file.pop("recent_messages", None)
        instruction = f"TASK: {task}\n\nCASE FILE:\n{json.dumps(case_file, ensure_ascii=False)}"
        messages = conversation(ctx.conn, ctx.case_id, instruction)
    else:
        messages = [first_message(task, case_file)]
    steps = 0
    nudged = False
    last_text = ""
    timed_out = False
    for _ in range(get_settings().MAX_STEPS_PER_RUN):
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            break
        steps += 1
        resp = claude.loop_call(system, tools, messages, ctx.meter, client=ctx.client)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            last_text = _plain_text(resp.content)
            if not nudged:  # the model stopped with text: ask once for finish_run
                nudged = True
                messages.append({"role": "user", "content": NUDGE})
                continue
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
                payload={
                    "tool": block.name,
                    "ok": ok,
                    "run_id": ctx.run_id,
                    **({"pages": out["pages"]} if block.name == "explain_match" and out.get("pages") else {}),
                },
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
    if ctx.finished:
        return LoopOutcome(steps, "nudged" if nudged else None, None, False)
    if last_text and not timed_out:
        return LoopOutcome(steps, "text_fallback", last_text, False)
    return LoopOutcome(steps, "deadline" if timed_out else "no_reply", None, timed_out)


def _superseded(conn: psycopg.Connection, case_id: UUID | str, message_id: Any) -> bool:
    """True if a later chat run has already started after this message was saved (it saw the message)."""
    msg = conn.execute("select created_at from messages where id = %s", (message_id,)).fetchone()
    if not msg:
        return False
    row = conn.execute(
        "select count(*) as n from brain_runs where case_id = %s and trigger = 'chat' and started_at > %s "
        "and status = any(%s)",
        (case_id, msg["created_at"], SEEN_STATUSES),
    ).fetchone()
    return int(row["n"]) > 0


def _skip(conn: psycopg.Connection, case_id: UUID | str, task: str, trigger: str) -> RunResult:
    row = conn.execute(
        "insert into brain_runs (case_id, trigger, task, steps, status, error, estimated_cost_usd, started_at, "
        "ended_at) values (%s, %s, %s, 0, 'SKIPPED', %s, 0, now(), now()) returning id",
        (case_id, trigger, task, COALESCED),
    ).fetchone()
    conn.commit()
    return RunResult(str(row["id"]), "SKIPPED", 0, "", 0.0, COALESCED)


def _safe_reply(ctx: Ctx, text: str) -> tuple[str, str]:
    """The model's own text, if the promise and scheme-name scanner passes it; otherwise the fixed safe sentence."""
    if scan_text(ctx, text)["ok"]:
        return text, "text_fallback"
    return SAFE_REPLY.get(ctx.language, SAFE_REPLY["en"]), "text_blocked"


def _failed_before_run(conn: psycopg.Connection, case_id: UUID | str, task: str, trigger: str, error: str) -> RunResult:
    """The lock was not granted (or the database refused): the same ERROR path, with a notice."""
    row = conn.execute("select language from cases where id = %s", (case_id,)).fetchone()
    lang = (row or {}).get("language") or "en"
    run_id = start_run(conn, case_id, task, trigger)
    end_run(conn, run_id, 0, "ERROR", UsageMeter(), error=error)
    post_notice(case_id, LATER)
    return RunResult(run_id, "ERROR", 0, LATER.get(lang, LATER["en"]), 0.0, error)


def _asked_since(conn: psycopg.Connection, case_id: UUID | str, since: Any) -> bool:
    """True if the Brain posted anything to the user since the run started (a question counts as an answer)."""
    row = conn.execute(
        "select 1 from messages where case_id = %s and direction = 'out' and created_at >= %s limit 1",
        (case_id, since),
    ).fetchone()
    return row is not None


def run_brain(
    case_id: UUID | str,
    task: str,
    trigger: str,
    *,
    conn: psycopg.Connection | None = None,
    deadline_related: bool = False,
    client: Any = None,
    message_id: Any = None,
) -> RunResult:
    if conn is None:
        from app.db import connect

        with connect() as c:
            return run_brain(
                case_id, task, trigger, conn=c, deadline_related=deadline_related, client=client, message_id=message_id
            )
    try:
        lock = case_lock(case_id)
    except Exception as exc:  # noqa: BLE001 - lock wait timed out, or the database refused
        conn.rollback()
        log.warning("case lock not granted for %s: %s", case_id, type(exc).__name__)
        return _failed_before_run(conn, case_id, task, trigger, type(exc).__name__)
    try:
        return _locked(conn, case_id, task, trigger, deadline_related, client, message_id)
    finally:
        try:
            lock.commit()
        finally:
            lock.close()


def _locked(
    conn: psycopg.Connection,
    case_id: UUID | str,
    task: str,
    trigger: str,
    deadline_related: bool,
    client: Any,
    message_id: Any,
) -> RunResult:
    s = get_settings()
    case = conn.execute("select id, language, state_code from cases where id = %s", (case_id,)).fetchone()
    if not case:
        raise LookupError(f"case {case_id} not found")
    lang = case["language"] or "en"
    if trigger == "chat" and message_id is not None and _superseded(conn, case_id, message_id):
        return _skip(conn, case_id, task, trigger)
    reason = deferral_reason(conn, case_id, deadline_related)
    if reason:
        result = defer(conn, case_id, task, trigger, reason, lang)
        post_notice(case_id, TOMORROW)
        return result

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
    deadline_s = s.CHAT_RUN_DEADLINE_S if trigger == "chat" else s.CLAUDE_JOB_DEADLINE_S
    run_started = conn.execute("select now() as t").fetchone()["t"]
    try:
        out = run_loop(
            ctx, task, system, tools, use_history=trigger == "chat", deadline=time.monotonic() + deadline_s
        )
    except Exception as exc:  # noqa: BLE001 - API/network/database failure: record, tell the user politely
        conn.rollback()
        log.warning("run %s failed: %s", run_id, type(exc).__name__)
        end_run(conn, run_id, 0, "ERROR", ctx.meter, error=type(exc).__name__)
        post_notice(case_id, LATER)
        return RunResult(run_id, "ERROR", 0, LATER.get(lang, LATER["en"]), ctx.meter.cost, type(exc).__name__)

    if ctx.finished:
        if ctx.user_message.strip() or _asked_since(conn, case_id, run_started):
            end_run(conn, run_id, out.steps, "DONE", ctx.meter, error=out.path)
            return RunResult(run_id, "DONE", out.steps, ctx.user_message, ctx.meter.cost)
        # finish_run came back with no text and no question was asked: never leave the user in silence
        end_run(conn, run_id, out.steps, "DONE", ctx.meter, error="empty_finish")
        post_notice(case_id, LATER)
        return RunResult(run_id, "DONE", out.steps, LATER.get(lang, LATER["en"]), ctx.meter.cost, "empty_finish")

    if out.path == "text_fallback" and out.fallback_text:
        reply, path = _safe_reply(ctx, out.fallback_text)
        conn.execute(
            "insert into messages (case_id, direction, channel, body, attachments, processed) "
            "values (%s, 'out', 'web', %s, %s, true)",
            (case_id, reply, jsonb({"kind": "reply"})),
        )
        conn.commit()
        end_run(conn, run_id, out.steps, "DONE_FALLBACK", ctx.meter, error=path)
        return RunResult(run_id, "DONE_FALLBACK", out.steps, reply, ctx.meter.cost, path)

    error = out.path or "no_reply"
    log_event(
        conn,
        case_id,
        "system",
        "RUN_STUCK",
        bilingual(STUCK["en"], STUCK["hi"]),
        payload={"run_id": run_id, "steps": out.steps, "path": error},
    )
    end_run(conn, run_id, out.steps, "STUCK", ctx.meter, error=error)
    alert_curator(run_id, case_id, "RUN_STUCK")
    post_notice(case_id, STUCK)
    return RunResult(run_id, "STUCK", out.steps, STUCK.get(lang, STUCK["en"]), ctx.meter.cost, error)


def close_stale_runs(conn: psycopg.Connection) -> int:
    """Housekeeping: a run row with no end time after STALE_RUN_MIN minutes is closed as ERROR 'stale', once."""
    rows = conn.execute(
        "update brain_runs set status = 'ERROR', error = 'stale', ended_at = now() "
        "where ended_at is null and started_at < now() - make_interval(mins => %s) returning id, case_id",
        (get_settings().STALE_RUN_MIN,),
    ).fetchall()
    conn.commit()  # the rows are closed before any notice is written, so a second pass cannot post again
    for row in rows:
        if row["case_id"]:
            post_notice(row["case_id"], LATER)
    return len(rows)
