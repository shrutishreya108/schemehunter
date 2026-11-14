"""Pending actions: the approval gate (LLD §11).

Lifecycle: PENDING → APPROVED → EXECUTED; or REJECTED; EXPIRED after 7 days; FAILED after 3 tries.

Nothing leaves the system except through execute(), and execute() only runs on an APPROVED row.
The executor claims the row (status EXECUTED, executed_at set) *before* calling the provider, so a crash
mid-send can never send twice; a failed send puts it back to APPROVED until the 3rd try, then FAILED.
"""

import hashlib
import json
import logging
import re
import secrets
from collections.abc import Iterable
from typing import Any

from app.events import log_event
from app.i18n import bilingual

log = logging.getLogger(__name__)

KINDS = {"send_email", "send_chat", "instruct_submit", "register_domain"}
STATUSES = {"PENDING", "APPROVED", "EXECUTED", "REJECTED", "FAILED", "EXPIRED"}
EXPIRY_DAYS = 7
MAX_EXECUTOR_TRIES = 3


def normalize_body(body: str) -> str:
    """Collapse whitespace and trim, so re-drafts that differ only in spacing dedupe."""
    return re.sub(r"\s+", " ", body).strip()


def idempotency_key(kind: str, case_id: str, application_id: str | None, body: str) -> str:
    """sha256(kind | case_id | application_id | normalized_body)."""
    raw = "|".join([kind, str(case_id), str(application_id or ""), normalize_body(body)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_short_code(taken: Iterable[str]) -> str:
    """4 digits, unique among this case's PENDING actions ("YES 4471")."""
    taken = set(taken)
    if len(taken) >= 10_000:
        raise RuntimeError("no short codes left for this case")
    while True:
        code = f"{secrets.randbelow(10_000):04d}"
        if code not in taken:
            return code


def create_pending(
    conn, case_id: str, kind: str, payload: dict, application_id: str | None = None
) -> dict | None:
    """Insert a PENDING action. A duplicate (same idempotency key) is dropped and returns None."""
    from psycopg.types.json import Jsonb

    if kind not in KINDS:
        raise ValueError(f"unknown pending-action kind {kind!r}")
    key = idempotency_key(kind, case_id, application_id, json.dumps(payload, sort_keys=True, default=str))
    taken = [
        r["short_code"]
        for r in conn.execute(
            "select short_code from pending_actions where case_id = %s and status = 'PENDING'", (case_id,)
        ).fetchall()
    ]
    return conn.execute(
        "insert into pending_actions (short_code, case_id, application_id, kind, payload, idempotency_key) "
        "values (%s, %s, %s, %s, %s, %s) on conflict (idempotency_key) do nothing "
        "returning id, short_code",
        (new_short_code(taken), case_id, application_id, kind, Jsonb(payload), key),
    ).fetchone()


class GuardError(Exception):
    """An approval request that can't be honoured (wrong case, wrong state). Message is user-safe."""


def one_line(kind: str, payload: dict, lang: str = "en") -> str:
    """A short description for the Needs-you inbox. Never includes the full message body."""
    if kind == "send_email":
        who = "the bank" if payload.get("audience") == "bank" else "the officer"
        hi_who = "बैंक" if payload.get("audience") == "bank" else "अधिकारी"
        return (
            f"Send an email to {who}: {payload.get('subject', '')}"
            if lang == "en"
            else (f"{hi_who} को ईमेल भेजें: {payload.get('subject', '')}")
        )
    if kind == "send_chat":
        return "Send you this message" if lang == "en" else "आपको यह संदेश भेजें"
    if kind == "instruct_submit":
        name = str(payload.get("scheme_id", "")).upper()
        return (
            f"Press submit on the {name} portal. I'll send the steps and your pre-filled pack."
            if lang == "en"
            else f"{name} पोर्टल पर सबमिट दबाएँ। मैं स्टेप और भरा हुआ पैक भेजूँगी।"
        )
    return kind


def _load(conn, action_id: str, case_id: str | None) -> dict:
    sql = "select * from pending_actions where id = %s"
    params: tuple = (action_id,)
    if case_id:
        sql += " and case_id = %s"
        params = (action_id, case_id)
    row = conn.execute(sql, params).fetchone()
    if not row:
        raise GuardError("no such action")  # another case's id looks the same as a missing one
    return row


def find_by_code(conn, case_id: str, code: str) -> dict | None:
    return conn.execute(
        "select * from pending_actions where case_id = %s and short_code = %s and status = 'PENDING'",
        (case_id, code),
    ).fetchone()


def approve(conn, action_id: str, approved_by: str, case_id: str | None = None, run: bool = True) -> dict:
    """PENDING → APPROVED (then executed unless run=False). Idempotent: a double tap changes nothing."""
    row = _load(conn, action_id, case_id)
    if row["status"] != "PENDING":
        return row  # already decided: second tap, or a late YES. Never re-send.
    conn.execute(
        "update pending_actions set status = 'APPROVED', decided_at = now() where id = %s and status = 'PENDING'",
        (action_id,),
    )
    log_event(
        conn,
        row["case_id"],
        "user",
        "ACTION_APPROVED",
        bilingual(
            f"Approved: {one_line(row['kind'], row['payload'])}",
            f"मंज़ूर किया: {one_line(row['kind'], row['payload'], 'hi')}",
        ),
        payload={"action_id": str(row["id"]), "kind": row["kind"], "by": approved_by},
        application_id=row["application_id"],
    )
    return execute(conn, action_id) if run else _load(conn, action_id, case_id)


def reject(conn, action_id: str, rejected_by: str, case_id: str | None = None) -> dict:
    row = _load(conn, action_id, case_id)
    if row["status"] != "PENDING":
        return row
    conn.execute(
        "update pending_actions set status = 'REJECTED', decided_at = now() where id = %s and status = 'PENDING'",
        (action_id,),
    )
    log_event(
        conn,
        row["case_id"],
        "user",
        "ACTION_REJECTED",
        bilingual(
            f"Declined: {one_line(row['kind'], row['payload'])}",
            f"मना किया: {one_line(row['kind'], row['payload'], 'hi')}",
        ),
        payload={"action_id": str(row["id"]), "kind": row["kind"], "by": rejected_by},
        application_id=row["application_id"],
    )
    return _load(conn, action_id, case_id)


def execute(conn, action_id: str) -> dict:
    """Send an APPROVED action. Claims the row first (status EXECUTED, executed_at) so a crash can't resend."""
    claimed = conn.execute(
        "update pending_actions set status = 'EXECUTED', executed_at = now(), attempts = attempts + 1 "
        "where id = %s and status = 'APPROVED' returning *",
        (action_id,),
    ).fetchone()
    if not claimed:
        return _load(conn, action_id, None)  # not approved, or already claimed by someone else
    conn.commit()  # the claim is durable before any provider call
    try:
        from app.guard.executors import run_action

        run_action(conn, claimed)
    except Exception as exc:  # noqa: BLE001 - any provider failure is recorded, never raised into a request
        conn.rollback()
        tries = claimed["attempts"]
        final = tries >= MAX_EXECUTOR_TRIES
        log.warning("action %s failed (try %d): %s", action_id, tries, type(exc).__name__)
        conn.execute(
            "update pending_actions set status = %s, error = %s, executed_at = case when %s then executed_at else null end "
            "where id = %s",
            ("FAILED" if final else "APPROVED", f"{type(exc).__name__}: {exc}"[:300], final, action_id),
        )
        conn.commit()
    return _load(conn, action_id, None)


def retry_failed_sends(conn) -> int:
    """APPROVED actions whose last try failed (not FAILED yet) get another go. Called by the worker."""
    ids = [
        r["id"]
        for r in conn.execute(
            "select id from pending_actions where status = 'APPROVED' and attempts > 0 and attempts < %s",
            (MAX_EXECUTOR_TRIES,),
        )
    ]
    for action_id in ids:
        execute(conn, str(action_id))
    return len(ids)


def expire(conn) -> int:
    """PENDING for more than 7 days → EXPIRED, with a timeline entry so the agent (and user) can see it."""
    rows = conn.execute(
        "update pending_actions set status = 'EXPIRED', decided_at = now() "
        "where status = 'PENDING' and created_at < now() - make_interval(days => %s) "
        "returning id, case_id, application_id, kind, payload",
        (EXPIRY_DAYS,),
    ).fetchall()
    for r in rows:
        log_event(
            conn,
            r["case_id"],
            "system",
            "ACTION_REJECTED",
            bilingual(
                f"Expired after {EXPIRY_DAYS} days with no answer: {one_line(r['kind'], r['payload'])}",
                f"{EXPIRY_DAYS} दिन तक जवाब नहीं आया, बंद किया: {one_line(r['kind'], r['payload'], 'hi')}",
            ),
            payload={"action_id": str(r["id"]), "expired": True},
            application_id=r["application_id"],
        )
    return len(rows)


def pending_for_case(conn, case_id: str) -> list[dict[str, Any]]:
    return conn.execute(
        "select id, short_code, kind, payload, status, application_id, created_at, error from pending_actions "
        "where case_id = %s and status in ('PENDING', 'APPROVED', 'FAILED') order by created_at",
        (case_id,),
    ).fetchall()
