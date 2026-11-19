"""Sign-in challenges: 6-digit code and one-tap link, each consumed exactly once (LLD signin §4). No model here.

Codes and tokens exist in memory only, for the email body. The database holds keyed hashes.
Every failure returns None, so callers cannot tell "unknown email", "wrong code" and "expired" apart.
"""

import hashlib
import hmac
import secrets
import unicodedata
from typing import Any
from uuid import UUID, uuid4

import psycopg

from app.access import hash_token
from app.config import get_settings

CONSENT_VERSION = "signin-v1"
SIGNIN_PURPOSES = ("signin", "signup")


def _mac(message: str) -> str:
    return hmac.new(get_settings().SESSION_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


def code_digits(raw: str | None) -> str | None:
    """Exactly six digits. Spaces and dashes are ignored; Devanagari digits are accepted."""
    s = "".join(ch for ch in (raw or "") if ch not in " - ")
    if len(s) != 6 or not all(ch.isdecimal() for ch in s):
        return None
    return "".join(str(unicodedata.decimal(ch)) for ch in s)


def supersede_open(
    conn: psycopg.Connection,
    *,
    purposes: tuple[str, ...],
    email_norm: str | None = None,
    case_id: UUID | str | None = None,
) -> None:
    """Earlier unused challenges stop working once a new one is issued."""
    if case_id is not None:
        conn.execute(
            "update login_challenges set superseded_at = now() where case_id = %s and purpose = any(%s) "
            "and consumed_at is null and superseded_at is null",
            (str(case_id), list(purposes)),
        )
    else:
        conn.execute(
            "update login_challenges set superseded_at = now() where email_norm = %s and purpose = any(%s) "
            "and consumed_at is null and superseded_at is null",
            (email_norm, list(purposes)),
        )


def create_challenge(
    conn: psycopg.Connection,
    email_norm: str,
    purpose: str,
    lang: str,
    case_id: UUID | str | None = None,
) -> tuple[dict[str, Any], str | None, str]:
    """Returns (row, code, token). code is None for admin links; code and token must only go into the email."""
    s = get_settings()
    cid = uuid4()
    token = secrets.token_urlsafe(32)
    code = None if purpose == "admin" else f"{secrets.randbelow(10**6):06d}"
    if purpose == "admin":
        supersede_open(conn, purposes=("admin",), case_id=case_id)
        ttl = s.SIGNIN_ADMIN_LINK_TTL_MIN
    else:
        supersede_open(conn, purposes=SIGNIN_PURPOSES, email_norm=email_norm)
        ttl = s.SIGNIN_CODE_TTL_MIN
    row = conn.execute(
        "insert into login_challenges (id, purpose, email_norm, case_id, code_hash, link_hash, lang, expires_at) "
        "values (%s, %s, %s, %s, %s, %s, %s, now() + make_interval(mins => %s)) returning *",
        (
            cid,
            purpose,
            email_norm,
            str(case_id) if case_id is not None else None,
            _mac(f"otp:{cid}:{code}") if code else None,
            hash_token(token),
            lang,
            ttl,
        ),
    ).fetchone()
    return row, code, token


def _consume(conn: psycopg.Connection, challenge_id: Any) -> dict | None:
    """Exactly one winner: the UPDATE only matches while consumed_at is still null."""
    return conn.execute(
        "update login_challenges set consumed_at = now() where id = %s and consumed_at is null returning *",
        (challenge_id,),
    ).fetchone()


def verify_code(conn: psycopg.Connection, email_norm: str, code: str | None) -> dict | None:
    """Check a sign-in or sign-up code. Every wrong try is counted and kept, even when this returns None."""
    digits = code_digits(code)
    if digits is None:
        return None
    row = conn.execute(
        "select * from login_challenges where email_norm = %s and purpose = any(%s) and consumed_at is null "
        "and superseded_at is null and expires_at > now() order by created_at desc limit 1 for update",
        (email_norm, list(SIGNIN_PURPOSES)),
    ).fetchone()
    if not row or row["code_hash"] is None or row["attempts"] >= get_settings().SIGNIN_MAX_ATTEMPTS:
        return None
    conn.execute("update login_challenges set attempts = attempts + 1 where id = %s", (row["id"],))
    if not hmac.compare_digest(row["code_hash"], _mac(f"otp:{row['id']}:{digits}")):
        return None
    return _consume(conn, row["id"])


def peek_link(conn: psycopg.Connection, token: str) -> dict | None:
    """Is this one-tap link still usable? Consumes nothing, so an email scanner can't spend it."""
    return conn.execute(
        "select * from login_challenges where link_hash = %s and consumed_at is null "
        "and superseded_at is null and expires_at > now()",
        (hash_token(token),),
    ).fetchone()


def verify_link(conn: psycopg.Connection, token: str) -> dict | None:
    """Consume a one-tap link. Only a POST from the user's own click gets here."""
    row = conn.execute(
        "select * from login_challenges where link_hash = %s and consumed_at is null "
        "and superseded_at is null and expires_at > now() for update",
        (hash_token(token),),
    ).fetchone()
    return _consume(conn, row["id"]) if row else None


def resolve_case(conn: psycopg.Connection, challenge: dict) -> str | None:
    """The case this proof opens. Sign-up creates a case only if no one has verified this email yet."""
    if challenge["purpose"] in ("signin", "admin"):
        row = conn.execute("select id from cases where id = %s", (challenge["case_id"],)).fetchone()
        return str(row["id"]) if row else None
    email = challenge["email_norm"]
    existing = conn.execute(
        "select case_id from identities where kind = 'email' and value_norm = %s", (email,)
    ).fetchone()
    if existing:
        return str(existing["case_id"])
    case = conn.execute(
        "insert into cases (language, email, state, consent_version, consent_at) "
        "values (%s, %s, 'ONBOARDING', %s, now()) returning id",
        (challenge["lang"] or "hi", email, CONSENT_VERSION),
    ).fetchone()
    won = conn.execute(
        "insert into identities (case_id, kind, value_norm, verified_at) values (%s, 'email', %s, now()) "
        "on conflict (kind, value_norm) do nothing returning case_id",
        (case["id"], email),
    ).fetchone()
    if won:
        return str(case["id"])
    conn.execute(
        "delete from cases where id = %s", (case["id"],)
    )  # a parallel sign-up won: keep only its case
    winner = conn.execute(
        "select case_id from identities where kind = 'email' and value_norm = %s", (email,)
    ).fetchone()
    return str(winner["case_id"])


def identity_case(conn: psycopg.Connection, email_norm: str) -> str | None:
    row = conn.execute(
        "select case_id from identities where kind = 'email' and value_norm = %s", (email_norm,)
    ).fetchone()
    return str(row["case_id"]) if row else None
