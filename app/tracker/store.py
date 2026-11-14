"""DB-backed TrackerStore (LLD §9): state in `applications`, diary in `events`, effects as jobs/actions."""

from datetime import datetime

import psycopg

from app.clock.schedule import NEXT_WINDOW_DAYS, run_at_for, schedule
from app.config import get_settings
from app.events import log_event
from app.guard.actions import create_pending
from app.i18n import bilingual
from app.tracker.states import Application, Effect, transition

STATE_WORDS = {
    "DISCOVERED": ("Found", "मिली"),
    "ELIGIBLE": ("You qualify to apply", "आवेदन के योग्य"),
    "DOCS_PENDING": ("Collecting papers", "कागज़ जुटा रहे हैं"),
    "DOCS_READY": ("Papers ready", "कागज़ तैयार"),
    "FORM_READY": ("Form ready", "फ़ॉर्म तैयार"),
    "AWAITING_HUMAN_SUBMIT": ("Waiting for you to submit", "आपके सबमिट करने का इंतज़ार"),
    "SUBMITTED": ("Submitted", "जमा हो गया"),
    "UNDER_REVIEW": ("Under review", "जाँच में"),
    "QUERY_RAISED": ("They asked for something", "उन्होंने कुछ माँगा है"),
    "RESUBMITTED": ("Sent again", "दोबारा भेजा"),
    "SANCTIONED": ("Sanctioned", "मंज़ूर"),
    "REJECTED": ("Rejected", "अस्वीकार"),
    "RETRY_NEXT_CYCLE": ("Will try next round", "अगली बार फिर कोशिश"),
    "CLOSED": ("Closed", "बंद"),
    "MISSED": ("Deadline missed", "समय निकल गया"),
}


def state_label(state: str, lang: str) -> str:
    en, hi = STATE_WORDS.get(state, (state, state))
    return hi if lang == "hi" else en


class DbTrackerStore:
    """One instance per connection; every write joins the caller's transaction."""

    def __init__(self, conn: psycopg.Connection, scale: float | None = None):
        self.conn = conn
        self.scale = scale or get_settings().DEMO_CLOCK_SCALE

    def required_docs_ok(self, app: Application) -> bool:
        from app.docs.requirements import required_docs_ok

        return required_docs_ok(self.conn, app.case_id, app.scheme_id)

    def save_state(self, app: Application, to_state: str) -> None:
        self.conn.execute(
            "update applications set state = %s, last_movement_at = now(), updated_at = now() where id = %s",
            (to_state, app.id),
        )

    def log_state_change(
        self, app: Application, from_state: str, to_state: str, actor: str, reason: str, payload: dict | None
    ) -> None:
        en_f, hi_f = STATE_WORDS.get(from_state, (from_state, from_state))
        en_t, hi_t = STATE_WORDS.get(to_state, (to_state, to_state))
        tail = f" ({reason})" if reason else ""
        name = app.scheme_id.upper()
        log_event(
            self.conn,
            app.case_id,
            actor,
            "STATE_CHANGED",
            bilingual(f"{name}: {en_f} → {en_t}{tail}", f"{name}: {hi_f} → {hi_t}{tail}"),
            payload={"from": from_state, "to": to_state, **(payload or {})},
            application_id=app.id,
        )

    def run_effect(self, app: Application, effect: Effect) -> None:
        if effect.kind == "pending":
            create_pending(
                self.conn,
                app.case_id,
                effect.name,
                {"application_id": app.id, "scheme_id": app.scheme_id},
                application_id=app.id,
            )
            return
        args = dict(effect.args)
        task = args.pop("task", None)
        at = args.pop("at", None)
        run_at = None
        if at == "next_window":
            run_at = run_at_for(NEXT_WINDOW_DAYS, self.scale)
        elif isinstance(at, datetime):
            run_at = at
        schedule(
            self.conn,
            effect.name,
            app.case_id,
            app.id,
            days_from_now=args.get("days", 0),
            at=run_at,
            payload={"task": task} if task else None,
            scale=self.scale,
        )


def load_application(
    conn: psycopg.Connection, application_id: str, case_id: str | None = None
) -> Application | None:
    """Fetch an application; with case_id, only if it belongs to that case."""
    sql = "select id, case_id, scheme_id, state, ref_number from applications where id = %s"
    params: tuple = (application_id,)
    if case_id:
        sql += " and case_id = %s"
        params = (application_id, case_id)
    row = conn.execute(sql, params).fetchone()
    if not row:
        return None
    return Application(
        id=str(row["id"]),
        case_id=str(row["case_id"]),
        scheme_id=row["scheme_id"],
        state=row["state"],
        ref_number=row["ref_number"],
    )


def create_application(
    conn: psycopg.Connection, case_id: str, scheme_id: str, actor: str = "agent"
) -> Application:
    """New row in DISCOVERED, then → ELIGIBLE. The caller has already checked the match is FIT."""
    row = conn.execute(
        "insert into applications (case_id, scheme_id, state) values (%s, %s, 'DISCOVERED') returning id",
        (case_id, scheme_id),
    ).fetchone()
    app = Application(id=str(row["id"]), case_id=case_id, scheme_id=scheme_id, state="DISCOVERED")
    name = scheme_id.upper()
    log_event(
        conn,
        case_id,
        actor,
        "STATE_CHANGED",
        bilingual(f"{name} application created", f"{name} आवेदन बना"),
        {"to": "DISCOVERED"},
        app.id,
    )
    return transition(app, "ELIGIBLE", actor, "", DbTrackerStore(conn))
