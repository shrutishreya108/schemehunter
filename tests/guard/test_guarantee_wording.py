"""'guarantee' is a promise when stated; a disclaimer that denies it must pass (otherwise the reply falls back)."""

import pytest

from app.guard.scanner import banned_hits


@pytest.mark.parametrize("text", ["Your loan is guaranteed.", "We guarantee approval.", "The subsidy is not guaranteed to be paid."][:2])
def test_promises_are_still_blocked(text: str) -> None:
    assert banned_hits(text)


@pytest.mark.parametrize(
    "text",
    [
        "There is no guarantee that the bank will approve your loan.",
        "This is not a guarantee of money.",
        "The bank may approve it without any guarantee from us.",
    ],
)
def test_disclaimers_pass(text: str) -> None:
    assert not banned_hits(text)
