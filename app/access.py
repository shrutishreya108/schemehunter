"""Access control (LLD §16.1) and rate limits.

- Secret case link /c/<token> → signed session cookie bound to case_id (Secure, HttpOnly, SameSite=Lax,
  30 days). Only sha256(token) is stored. POST /c/rotate issues a new link and kills the old one.
- CSRF: per-case token (HMAC of SESSION_SECRET) in a <meta> tag; HTMX sends it as X-CSRF-Token, plain forms
  as a hidden `csrf` field.
- Curator: /admin/* behind HTTP basic auth with ADMIN_PASSWORD.
- Rate limits: Postgres fixed-window counters (rate_limits table).
- Optional 4-digit PIN: asked once per device after the link is opened; 5 wrong tries per 10 minutes.
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
PIN_COOKIE = "sh_pin_pending"
PIN_PENDING_MINUTES = 10
PIN_TRIES = 5
PIN_WINDOW_SECONDS = 600

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


def new_link_token() -> str:
    """32 random bytes, base64url."""
    return secrets.token_urlsafe(32)


def _hmac(message: str) -> str:
    return hmac.new(get_settings().SESSION_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()


def demo_link_token(persona_key: str) -> str:
    """Deterministic token for seeded demo personas, so demo-reset keeps the same links. Never for real cases."""
    return _hmac(f"demo:{persona_key}")


def bind_token(case_id: str) -> str:
    """Signed token for the Telegram deep link t.me/<bot>?start=<token>. Binds one chat to this case."""
    return f"{case_id.replace('-', '')}{_hmac(f'bind:{case_id}')[:24]}"


def case_for_bind_token(token: str) -> str | None:
    """Case id for a valid bind token, else None. Constant-time compare."""
    raw, sig = token[:32], token[32:]
    if len(raw) != 32 or not raw.isalnum():
        return None
    case_id = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"
    return case_id if hmac.compare_digest(sig, _hmac(f"bind:{case_id}")[:24]) else None


# ---------------------------------------------------------------- sessions


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


def case_for_token(conn: psycopg.Connection, token: str) -> dict | None:
    return conn.execute(
        "select id, language, pin_hash from cases where access_token_hash = %s", (hash_token(token),)
    ).fetchone()


def rotate_link(conn: psycopg.Connection, case_id: str) -> str:
    """New secret link; the old token stops working immediately."""
    token = new_link_token()
    conn.execute("update cases set access_token_hash = %s where id = %s", (hash_token(token), case_id))
    return token


def current_case_id(request: Request) -> str:
    case_id = read_session(request.cookies.get(SESSION_COOKIE))
    if not case_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please open your personal Scheme Hunter link.")
    return case_id


def optional_case_id(request: Request) -> str | None:
    return read_session(request.cookies.get(SESSION_COOKIE))


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


def hash_pin(case_id: str, pin: str) -> str:
    return _hmac(f"pin:{case_id}:{pin}")


def valid_pin_format(pin: str) -> bool:
    return len(pin) == 4 and pin.isascii() and pin.isdigit()


def set_pin(conn: psycopg.Connection, case_id: str, pin: str | None) -> None:
    """Set a 4-digit PIN, or clear it with None."""
    if pin is not None and not valid_pin_format(pin):
        raise ValueError("PIN must be 4 digits")
    conn.execute(
        "update cases set pin_hash = %s where id = %s", (hash_pin(case_id, pin) if pin else None, case_id)
    )


def check_pin(conn: psycopg.Connection, case_id: str, pin: str) -> bool:
    """True if the PIN matches. Wrong tries are counted: 5 per 10 minutes per case, then locked out."""
    if not rate_limit(conn, f"pin:{case_id}", PIN_TRIES, PIN_WINDOW_SECONDS):
        return False  # locked out: even the right PIN waits
    row = conn.execute("select pin_hash from cases where id = %s", (case_id,)).fetchone()
    return bool(
        row
        and row["pin_hash"]
        and valid_pin_format(pin)
        and hmac.compare_digest(row["pin_hash"], hash_pin(case_id, pin))
    )


def sign_pin_pending(case_id: str, now: float | None = None) -> str:
    """Short-lived cookie: "the link was opened, the PIN is still to come". Not a session."""
    expires = int((now or time.time()) + PIN_PENDING_MINUTES * 60)
    payload = base64.urlsafe_b64encode(f"{case_id}|{expires}".encode()).decode().rstrip("=")
    return f"{payload}.{_hmac('pin-pending:' + payload)}"


def read_pin_pending(cookie: str | None, now: float | None = None) -> str | None:
    if not cookie or "." not in cookie:
        return None
    payload, sig = cookie.rsplit(".", 1)
    if not hmac.compare_digest(sig, _hmac("pin-pending:" + payload)):
        return None
    try:
        case_id, expires = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode().split("|")
    except (ValueError, UnicodeDecodeError):
        return None
    return case_id if int(expires) > (now or time.time()) else None
