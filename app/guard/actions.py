"""Pending actions: the approval gate (LLD §11).

Slice 2 ships the pure helpers (idempotency key, short code). DB lifecycle lands in slice 7.
Lifecycle: PENDING → APPROVED → EXECUTED; or REJECTED; EXPIRED after 7 days; FAILED after 3 tries.
"""

import hashlib
import re
import secrets
from collections.abc import Iterable

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


def create_pending(case_id: str, kind: str, payload: dict, application_id: str | None = None) -> dict:
    raise NotImplementedError("slice 7")


def approve(action_id: str, approved_by: str) -> None:
    raise NotImplementedError("slice 7")


def reject(action_id: str, rejected_by: str) -> None:
    raise NotImplementedError("slice 7")


def execute(action_id: str) -> None:
    raise NotImplementedError("slice 7")


def expire() -> int:
    raise NotImplementedError("slice 7")
