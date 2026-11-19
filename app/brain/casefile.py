"""build_case_file(case_id): the compact JSON the Brain sees on waking (LLD §7.2).

Never images, never full ID numbers, never the full history: profile, missing facts, documents,
matches, applications, pending actions, the last 10 events, and (v0.3) the last 6 chat messages plus
the open question, so the Brain knows what the user's latest answer refers to.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import psycopg

from app.docs.masking import mask_fields

RECENT_EVENTS = 10
RECENT_MESSAGES = 6
MESSAGE_CHARS = 300


class CaseNotFound(LookupError):
    pass


def benefit_summary(benefit: dict | None) -> str:
    """One line from the computed benefit, e.g. "subsidy 25%, own 5%"; empty if nothing computed."""
    if not benefit:
        return ""
    labels = {"subsidy_percent": "subsidy {}%", "own_contribution_percent": "own {}%", "mudra_category": "{}"}
    parts = [labels.get(k, k + " {}").format(v) for k, v in benefit.items() if v is not None]
    return ", ".join(parts)


def days_since(ts: datetime | None, now: datetime) -> int | None:
    return (now - ts).days if ts else None


def iso(d: date | datetime | None) -> str | None:
    return d.isoformat() if d else None


def assemble_case_file(
    case: dict,
    answers: list[dict],
    documents: list[dict],
    matches: list[dict],
    applications: list[dict],
    pending: list[dict],
    events: list[dict],
    now: datetime | None = None,
    messages: list[dict] | None = None,
) -> dict[str, Any]:
    """Pure assembly from rows (unit-testable without Postgres)."""
    now = now or datetime.now(UTC)
    lang = case.get("language") or "en"
    missing = sorted({f for m in matches for f in (m.get("missing_facts") or [])})
    return {
        "today": now.date().isoformat(),
        "language": lang,
        "state_code": case.get("state_code"),
        "profile": mask_fields({a["key"]: a["value"] for a in answers}),
        "missing_facts": missing,
        "documents": [
            {
                "id": str(d["id"]),
                "doc_type": d["doc_type"],
                "check_status": d["check_status"],
                "issue": ((d.get("check_result") or {}).get("issue")),
            }
            for d in documents
        ],
        "matches": [
            {
                "scheme_id": m["scheme_id"],
                "result": m["result"],
                "benefit_summary": benefit_summary(m.get("benefit")),
            }
            for m in matches
        ],
        "applications": [
            {
                "id": str(a["id"]),
                "scheme_id": a["scheme_id"],
                "state": a["state"],
                "ref_number": a["ref_number"],
                "deadline": iso(a["deadline"]),
                "days_since_movement": days_since(a["last_movement_at"], now),
            }
            for a in applications
        ],
        "pending_actions": [
            {
                "id": str(p["id"]),
                "kind": p["kind"],
                "one_line": (p["payload"] or {}).get("one_line", p["kind"]),
            }
            for p in pending
        ],
        "recent_events": [
            f"{iso(e['created_at'])[:16]} {e['actor']}: {(e['summary'] or {}).get(lang) or (e['summary'] or {}).get('en', '')}"
            for e in reversed(events)  # events arrive newest-first; show oldest-first
        ],
        "recent_messages": [
            {
                "from": "user" if m["direction"] == "in" else "assistant",
                "text": (m["body"] or "")[:MESSAGE_CHARS],
            }
            for m in reversed(messages or [])
        ],
        "open_question": open_question(messages or []),
    }


def open_question(messages: list[dict]) -> dict | None:
    """The last ask_user question, until the assistant replies again (messages newest-first).

    User messages after the question don't close it: they are the answer the Brain needs to match to it.
    """
    for m in messages:
        if m["direction"] == "in":
            continue
        meta = m.get("attachments") or {}
        if meta.get("kind") == "question":
            return {"expected_fact": meta.get("expected_fact"), "question": m["body"]}
        return None
    return None


def build_case_file(
    case_id: UUID | str, conn: psycopg.Connection | None = None, now: datetime | None = None
) -> dict[str, Any]:
    if conn is None:
        from app.db import connect

        with connect() as c:
            return build_case_file(case_id, c, now)

    case = conn.execute("select * from cases where id = %s", (case_id,)).fetchone()
    if not case:
        raise CaseNotFound(str(case_id))
    q = lambda sql: conn.execute(sql, (case_id,)).fetchall()
    return assemble_case_file(
        case,
        answers=q("select key, value from profile_answers where case_id = %s order by key"),
        documents=q(
            "select id, doc_type, check_status, check_result from documents where case_id = %s order by created_at"
        ),
        matches=q(
            "select scheme_id, result, missing_facts, benefit from matches where case_id = %s order by scheme_id"
        ),
        applications=q(
            "select id, scheme_id, state, ref_number, deadline, last_movement_at "
            "from applications where case_id = %s order by created_at"
        ),
        pending=q(
            "select id, kind, payload from pending_actions where case_id = %s and status = 'PENDING' "
            "order by created_at"
        ),
        events=conn.execute(
            "select actor, summary, created_at from events where case_id = %s "
            "order by created_at desc, id desc limit %s",
            (case_id, RECENT_EVENTS),
        ).fetchall(),
        now=now,
        messages=conn.execute(
            "select direction, body, attachments from messages where case_id = %s and channel = 'web' "
            "order by created_at desc, id desc limit %s",
            (case_id, RECENT_MESSAGES),
        ).fetchall(),
    )
