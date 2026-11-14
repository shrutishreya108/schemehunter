"""Job handlers (LLD §10.3). Each is handler(conn, job); the worker commits on success and retries on any exception.

Messages to the user themself (nudges, reminders) are templated and sent directly; anything to a bank or officer is
drafted by the Brain and waits for approval. "Days" always mean demo-clock days: real elapsed time × DEMO_CLOCK_SCALE.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

from app.clock.schedule import NEXT_WINDOW_DAYS, schedule
from app.config import get_settings
from app.docs.requirements import GENERATED, document_statuses
from app.events import log_event
from app.i18n import bilingual, t
from app.mouth import templates
from app.mouth.outbox import notify_user
from app.rules.loader import load_schemes
from app.status.base import get_checker
from app.tracker.states import PRE_SUBMIT, IllegalTransition, transition
from app.tracker.store import DbTrackerStore, create_application, load_application

log = logging.getLogger(__name__)

WAITING = ("SUBMITTED", "UNDER_REVIEW", "RESUBMITTED")
CHASE_AFTER_DAYS = 10
CHASE_AGAIN_DAYS = 7
NOTHING_CHECKS_BEFORE_ASKING = 2
REMINDER_THRESHOLDS = (7, 2)


def scale() -> float:
    return get_settings().DEMO_CLOCK_SCALE


def elapsed_days(since: datetime, now: datetime | None = None) -> float:
    """Demo-clock days since `since` (1 real minute is a day at DEMO_CLOCK_SCALE=1440)."""
    return ((now or datetime.now(UTC)) - since).total_seconds() / 86400 * scale()


def scheme_of(app) -> dict:
    return next(s for s in load_schemes() if s["id"] == app.scheme_id)


def scheme_name(app, lang: str) -> str:
    return scheme_of(app)["name"].get(lang) or scheme_of(app)["name"]["en"]


def load_job_app(conn, job: dict):
    """The job's application, or None if it is gone. Jobs for vanished applications just finish."""
    if not job.get("application_id"):
        return None
    return load_application(conn, str(job["application_id"]))


def bilingual_templated(name: str, make_values) -> dict[str, str]:
    """Render a template in both languages. make_values(lang) returns the format values for that language."""
    return {lang: templates.render(name, lang, **make_values(lang)) for lang in ("en", "hi")}


# ---------------------------------------------------------------- status_check


def status_check(conn, job: dict) -> None:
    from app.brain.runner import deferral_reason, end_run, start_run
    from app.brain.tools import interpret_response as interpret
    from app.costs import UsageMeter

    app = load_job_app(conn, job)
    if not app or app.state not in WAITING:
        return  # not waiting on anyone any more: no reschedule
    case_id = app.case_id
    scheme = scheme_of(app)
    result = get_checker(scheme.get("status_mode", "user_forwards")).check(conn, app)
    prior = int((job.get("payload") or {}).get("nothing_checks", 0))
    moved = False
    nothing = prior

    if result.kind == "text":
        if deferral_reason(conn, case_id, False):
            nothing = prior  # over budget: leave the message unread and look again tomorrow
        else:
            run_id = start_run(conn, case_id, f"read forwarded message for {app.id}", "clock")
            meter = UsageMeter()
            outcome = "DONE"
            try:
                ctx = SimpleNamespace(meter=meter, client=None)
                parsed = interpret.classify(ctx, conn, case_id, result.text or "")
                applied = interpret.apply_result(conn, case_id, parsed, app.id, result.message_id)
                moved = bool(applied.get("applied"))
                nothing = 0
            except Exception:
                outcome = "ERROR"
                conn.rollback()
                raise
            finally:
                end_run(conn, run_id, 1, outcome, meter)
    else:
        nothing = prior + 1
        log_event(
            conn,
            case_id,
            "clock",
            "STATUS_CHECKED",
            bilingual("Status check: no new message from the portal", "स्थिति जाँच: पोर्टल से कोई नया संदेश नहीं"),
            payload={"nothing_checks": nothing},
            application_id=app.id,
        )
        if nothing == NOTHING_CHECKS_BEFORE_ASKING:
            ref = app.ref_number or "-"
            notify_user(
                conn,
                case_id,
                bilingual_templated(
                    "forward_sms", lambda lang: {"scheme": scheme_name(app, lang), "ref": ref}
                ),
                kind="forward_sms",
                application_id=app.id,
            )

    fresh = load_application(conn, app.id)
    if fresh and fresh.state in WAITING:
        schedule(
            conn,
            "status_check",
            case_id,
            app.id,
            days_from_now=1 if moved or result.kind == "text" else 3,
            payload={"nothing_checks": nothing},
        )


# ---------------------------------------------------------------- chase


def chase(conn, job: dict) -> None:
    from app.brain.runner import run_brain

    app = load_job_app(conn, job)
    if not app or app.state not in (*WAITING, "QUERY_RAISED"):
        return
    row = conn.execute("select last_movement_at from applications where id = %s", (app.id,)).fetchone()
    idle = elapsed_days(row["last_movement_at"])
    if idle < CHASE_AFTER_DAYS:
        schedule(conn, "chase", app.case_id, app.id, days_from_now=CHASE_AFTER_DAYS - idle)
        return
    task = (
        f"Chase: application {app.id} ({app.scheme_id}) has had no movement for {int(idle)} days. "
        "Draft a polite follow-up to the bank with draft_message (audience bank, application_id given) asking "
        "for the status, then finish_run with one short, calm sentence for the user saying a draft is waiting "
        "for their approval."
    )
    result = run_brain(app.case_id, task, "clock", conn=conn)
    if result.status == "ERROR":
        raise RuntimeError(f"chase run failed: {result.reason}")  # API trouble: retry with backoff
    schedule(conn, "chase", app.case_id, app.id, days_from_now=CHASE_AGAIN_DAYS)


# ---------------------------------------------------------------- doc_nudge


def missing_docs(conn, case_id: str, app) -> list[str]:
    scheme = scheme_of(app)
    return [
        d["doc_type"]
        for d in document_statuses(conn, case_id, scheme)
        if d["doc_type"] not in GENERATED and d["status"] != "OK"
    ]


def doc_nudge(conn, job: dict) -> None:
    app = load_job_app(conn, job)
    if not app or app.state not in ("DOCS_PENDING", "QUERY_RAISED", "ELIGIBLE"):
        return
    missing = missing_docs(conn, app.case_id, app)
    if not missing:
        return  # everything is OK: stop nudging
    recent = conn.execute(
        "select created_at from events where application_id = %s and type = 'REMINDER_SENT' "
        "and payload->>'kind' = 'doc_nudge' order by created_at desc limit 1",
        (app.id,),
    ).fetchone()
    if not recent or elapsed_days(recent["created_at"]) >= 1:  # at most one per day per person
        notify_user(
            conn,
            app.case_id,
            bilingual_templated(
                "doc_nudge",
                lambda lang: {
                    "scheme": scheme_name(app, lang),
                    "docs": ", ".join(t(f"doc_{d}", lang) for d in missing),
                },
            ),
            kind="doc_nudge",
            application_id=app.id,
            payload={"missing": missing},
        )
    schedule(conn, "doc_nudge", app.case_id, app.id, days_from_now=1)


# ---------------------------------------------------------------- deadline_reminders


def deadline_reminders(conn, job: dict, today: date | None = None) -> None:
    app = load_job_app(conn, job)
    if not app or app.state not in PRE_SUBMIT:
        return
    row = conn.execute("select deadline from applications where id = %s", (app.id,)).fetchone()
    deadline = row["deadline"]
    if not deadline:
        return  # rolling window: nothing to remind about
    days_left = (deadline - (today or date.today())).days
    if days_left < 0:
        try:
            transition(app, "MISSED", "clock", "the deadline passed", DbTrackerStore(conn))
        except IllegalTransition:
            pass
        return
    sent = {
        int(r["th"])
        for r in conn.execute(
            "select payload->>'threshold' as th from events where application_id = %s and type = 'REMINDER_SENT' "
            "and payload->>'kind' = 'deadline_reminder'",
            (app.id,),
        )
        if r["th"]
    }
    for threshold in REMINDER_THRESHOLDS:
        if days_left <= threshold and threshold not in sent:
            notify_user(
                conn,
                app.case_id,
                bilingual_templated(
                    "deadline_reminder",
                    lambda lang: {"scheme": scheme_name(app, lang), "days": str(max(days_left, 0))},
                ),
                kind="deadline_reminder",
                application_id=app.id,
                payload={"threshold": threshold},
            )
            sent.add(threshold)
            break  # one reminder per run, even if the window is already inside both thresholds
    pending = [th for th in REMINDER_THRESHOLDS if th not in sent]
    if pending:
        at = datetime.combine(deadline - timedelta(days=max(pending)), time(3, 0), tzinfo=UTC)
        at = max(at, datetime.now(UTC) + timedelta(hours=1))
    else:
        at = datetime.combine(deadline + timedelta(days=1), time(3, 0), tzinfo=UTC)
    schedule(conn, "deadline_reminders", app.case_id, app.id, at=at)


# ---------------------------------------------------------------- retry_cycle


def retry_cycle(conn, job: dict) -> None:
    """Start the next round: a fresh application in ELIGIBLE, reusing the papers already in the vault."""
    app = load_job_app(conn, job)
    if not app or app.state not in ("RETRY_NEXT_CYCLE", "MISSED"):
        return
    clone = create_application(conn, app.case_id, app.scheme_id, actor="agent")
    conn.execute(
        "update applications set cycle = %s where id = %s",
        (f"retry of {str(app.id)[:8]}", clone.id),
    )
    log_event(
        conn,
        app.case_id,
        "clock",
        "STATE_CHANGED",
        bilingual(
            f"Started the next round for {app.scheme_id.upper()}; your papers are reused",
            f"{app.scheme_id.upper()} का अगला दौर शुरू किया; आपके कागज़ दोबारा काम आएँगे",
        ),
        payload={"previous": app.id, "new": clone.id, "window_days": NEXT_WINDOW_DAYS},
        application_id=clone.id,
    )


# ---------------------------------------------------------------- brain_run, offer_domain, retention


def brain_run(conn, job: dict) -> None:
    """Run the Brain for a queued task (this is where budget-deferred runs finally execute)."""
    from app.brain.runner import run_brain

    payload = job.get("payload") or {}
    task = payload.get("task")
    if not task or not job.get("case_id"):
        raise ValueError("brain_run job needs a task and a case")
    result = run_brain(
        job["case_id"],
        task,
        payload.get("trigger") or "clock",
        conn=conn,
        deadline_related=bool(payload.get("deadline_related")),
    )
    if result.status == "ERROR":
        raise RuntimeError(f"brain run failed: {result.reason}")


def offer_domain(conn, job: dict) -> None:
    """Stretch feature, behind FEATURE_DOMAIN_OFFER. Not built: finishing quietly keeps the queue clean."""
    if get_settings().FEATURE_DOMAIN_OFFER:
        log.warning("FEATURE_DOMAIN_OFFER is on but the domain offer is not built yet")


def retention_cleanup(conn, job: dict | None = None) -> int:
    """Delete documents (files and rows) of CLOSED cases that have been quiet for RETENTION_DAYS."""
    days = get_settings().RETENTION_DAYS
    cases = conn.execute(
        "select c.id from cases c where c.state = 'CLOSED' and coalesce("
        "(select max(created_at) from events e where e.case_id = c.id), c.created_at) < now() - make_interval(days => %s)",
        (days,),
    ).fetchall()
    removed = 0
    for c in cases:
        for d in conn.execute(
            "select id, file_path from documents where case_id = %s", (c["id"],)
        ).fetchall():
            Path(d["file_path"]).unlink(missing_ok=True)
            removed += 1
        conn.execute("delete from documents where case_id = %s", (c["id"],))
        log_event(
            conn,
            c["id"],
            "system",
            "DOC_CHECKED",
            bilingual("Your papers were deleted as promised", "वादे के मुताबिक आपके कागज़ मिटा दिए गए"),
            payload={"retention": True},
        )
    if job is not None:  # as a queued job it repeats daily; from cron it just runs once
        schedule(conn, "retention_cleanup", None, at=datetime.now(UTC) + timedelta(days=1))
    return removed


def run_retention() -> int:
    """Entry point for the host cron line (scripts/retention_cleanup.sh)."""
    from app.db import connect

    with connect() as conn:
        return retention_cleanup(conn)


HANDLERS = {
    "status_check": status_check,
    "chase": chase,
    "doc_nudge": doc_nudge,
    "deadline_reminders": deadline_reminders,
    "retry_cycle": retry_cycle,
    "brain_run": brain_run,
    "offer_domain": offer_domain,
    "retention_cleanup": retention_cleanup,
}
