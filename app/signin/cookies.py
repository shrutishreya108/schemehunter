"""Signed, expiring values for the sign-in flow: the pending-email cookie and the form token (LLD signin §5)."""

import base64
import hashlib
import hmac
import time

from app.config import get_settings


def _mac(raw: str) -> str:
    return hmac.new(
        get_settings().SESSION_SECRET.encode(), f"signin:{raw}".encode(), hashlib.sha256
    ).hexdigest()


def sign(payload: str) -> str:
    raw = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{raw}.{_mac(raw)}"


def unsign(value: str | None) -> str | None:
    """The payload if the signature is valid, else None. Expiry is the caller's business."""
    if not value or "." not in value:
        return None
    raw, _, sig = value.rpartition(".")
    if not hmac.compare_digest(sig, _mac(raw)):
        return None
    try:
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None


def sign_pending(email_norm: str, purpose: str, ttl_seconds: int = 15 * 60, now: float | None = None) -> str:
    """Cookie for the code page: which email we sent to, and whether it is sign-in or sign-up."""
    expires = int((now or time.time()) + ttl_seconds)
    return sign(f"{email_norm}|{purpose}|{expires}")


def read_pending(value: str | None, now: float | None = None) -> tuple[str, str] | None:
    payload = unsign(value)
    if not payload:
        return None
    try:
        email_norm, purpose, expires = payload.split("|")
    except ValueError:
        return None
    if int(expires) <= (now or time.time()) or purpose not in ("signin", "signup"):
        return None
    return email_norm, purpose


def form_token(now: float | None = None) -> str:
    return sign(f"form|{int(now or time.time())}")


def form_token_ok(
    value: str | None, min_age_s: float = 2, max_age_s: float = 3600, now: float | None = None
) -> bool:
    """Refuse forms sent too fast (bots) or from a stale page."""
    payload = unsign(value)
    if not payload or not payload.startswith("form|"):
        return False
    age = (now or time.time()) - int(payload.split("|")[1])
    return min_age_s <= age <= max_age_s
