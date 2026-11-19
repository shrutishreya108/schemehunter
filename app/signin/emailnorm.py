"""Email normalisation and the privacy helpers around it (LLD signin §4). Pure code."""

import hashlib
import re
import unicodedata

EMAIL_RE = re.compile(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[a-z0-9-]+(\.[a-z0-9-]+)+")
MAX_LEN = 254


class InvalidEmail(ValueError):
    """Shown to the user as-is. Carries no part of the address."""


def normalize_email(raw: str) -> str:
    """NFKC, strip, lower. Raises InvalidEmail. Keeps +tags and dots: they are part of the address."""
    s = unicodedata.normalize("NFKC", raw or "").strip().lower()
    if not s or len(s) > MAX_LEN:
        raise InvalidEmail("Please enter a valid email address.")
    if not s.isascii():
        raise InvalidEmail("Please use an email with English letters.")
    if not EMAIL_RE.fullmatch(s):
        raise InvalidEmail("Please enter a valid email address.")
    return s


def email_key(email_norm: str) -> str:
    """Rate-limit key material: a hash, so no address text reaches the rate_limits table."""
    return hashlib.sha256(email_norm.encode("utf-8")).hexdigest()[:32]


def mask_email(email_norm: str) -> str:
    """meena@gmail.com -> m**@gmail.com"""
    local, _, domain = email_norm.partition("@")
    return f"{local[:1]}**@{domain}"
