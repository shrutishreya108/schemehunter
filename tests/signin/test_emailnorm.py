"""Email normalisation and masking (pure, no database)."""

import pytest

from app.signin.emailnorm import InvalidEmail, email_key, mask_email, normalize_email


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("  Meena@Gmail.COM ", "meena@gmail.com"),
        ("first.last+tag@sub.example.in", "first.last+tag@sub.example.in"),  # dots and +tags are kept
        ("ＭＥＥＮＡ@gmail.com", "meena@gmail.com"),  # full-width letters fold to ASCII via NFKC
    ],
)
def test_normalises(raw, clean) -> None:
    assert normalize_email(raw) == clean


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "no-at-sign",
        "a b@x.com",
        "a,b@x.com",
        "a@x.com\r\nBcc: x@y.com",
        "<a@x.com>",
        "मीना@example.com",
        "a@" + "x" * 250 + ".com",
        "a@localhost",
    ],
)
def test_rejects_unsafe_or_malformed(raw) -> None:
    with pytest.raises(InvalidEmail):
        normalize_email(raw)


def test_non_english_gets_its_own_message() -> None:
    with pytest.raises(InvalidEmail, match="English letters"):
        normalize_email("मीना@example.com")


def test_masking_keeps_domain_and_first_letter_only() -> None:
    assert mask_email("meena@gmail.com") == "m**@gmail.com"
    assert mask_email("a@x.in") == "a**@x.in"


def test_rate_limit_key_hides_the_address() -> None:
    key = email_key("meena@gmail.com")
    assert len(key) == 32 and "meena" not in key and "@" not in key and key == email_key("meena@gmail.com")
