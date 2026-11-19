"""Access control (LLD §16.1) and rate limits.

- Email sign-in (app/signin, app/routes/signin.py) → signed session cookie bound to case_id (Secure, HttpOnly,
  SameSite=Lax, 30 days). There is no personal link and no PIN.
- CSRF: per-case token (HMAC of SESSION_SECRET) in a <meta> tag; HTMX sends it as X-CSRF-Token, plain forms
  as a hidden `csrf` field.
- Curator: /admin/* behind HTTP basic auth with ADMIN_PASSWORD.
- Rate limits: Postgres fixed-window counters (rate_limits table).
"""

import base64
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime
from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import get_settings

SESSION_COOKIE = "sh_session"
LANG_COOKIE = "sh_lang"
SESSION_DAYS = 30

_basic = HTTPBasic()


# ---------------------------------------------------------------- admin


def require_admin(credentials: Annotated[HTTPBasicCredentials, Depends(_basic)]) -> str:
    """HTTP basic auth against ADMIN_PASSWORD. Any username; refuses if no password is configured."""
    expected = get_settings().ADMIN_PASSWORD
    if not expected or not secrets.compare_digest(credentials.password.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"WWW-Authenticate": "Basic"})
    return credentials.username


# ---------------------------------------------------------------- tokens


def hash_token(token: str) -> str:
    """Only sha256(token) is stored in cases.access_token_hash."""
    return hashlib.sha256(token.encode()).hexdigest()


def _hmac(message: str) -> str:
    return hmac.new(get_settings().SESSION_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


def sign_session(case_id: str, now: float | None = None) -> str:
    expires = int((now or time.time()) + SESSION_DAYS * 86400)
    payload = base64.urlsafe_b64encode(f"{case_id}|{expires}".encode()).decode().rstrip("=")
    return f"{payload}.{_hmac('session:' + payload)}"


def read_session(cookie: str | None, now: float | None = None) -> str | None:
    """case_id from a valid, unexpired cookie; else None."""
    if not cookie or "." not in cookie:
        return None
    payload, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _hmac("session:" + payload)):
        return None
    try:
        case_id, expires = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode().split("|")
    except (ValueError, UnicodeDecodeError):
        return None
    return case_id if int(expires) > (now or time.time()) else None


def case_exists(case_id: str) -> bool:
    from app.db import connect

    with connect() as conn:
        return conn.execute("select 1 from cases where id = %s", (case_id,)).fetchone() is not None


def current_case_id(request: Request) -> str:
    case_id = read_session(request.cookies.get(SESSION_COOKIE))
    if not case_id or not case_exists(case_id):  # a cookie for a deleted case is just signed out
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in.")
    return case_id


def optional_case_id(request: Request) -> str | None:
    case_id = read_session(request.cookies.get(SESSION_COOKIE))
    return case_id if case_id and case_exists(case_id) else None


# ---------------------------------------------------------------- CSRF


def csrf_token(case_id: str) -> str:
    return _hmac(f"csrf:{case_id}")[:32]


def check_csrf(case_id: str, supplied: str | None) -> None:
    if not supplied or not hmac.compare_digest(supplied, csrf_token(case_id)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "bad CSRF token")


# ---------------------------------------------------------------- rate limits


def client_ip(request: Request) -> str:
    """Behind Caddy (TRUST_PROXY): first X-Forwarded-For hop. Otherwise the socket peer."""
    if get_settings().TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(
    conn: psycopg.Connection, key: str, limit: int, window_seconds: int, now: datetime | None = None
) -> bool:
    """Count one hit in the current fixed window; True if still within `limit`."""
    now = now or datetime.now(UTC)
    window = datetime.fromtimestamp(int(now.timestamp()) // window_seconds * window_seconds, UTC)
    row = conn.execute(
        "insert into rate_limits (key, window_start, count) values (%s, %s, 1) "
        "on conflict (key, window_start) do update set count = rate_limits.count + 1 returning count",
        (key, window),
    ).fetchone()
    return row["count"] <= limit
