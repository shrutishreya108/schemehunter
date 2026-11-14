"""15 portal messages → expected intents (seed/portal_messages.yaml). Built in slice 8.

The fixture file itself is checked now so it can't rot before slice 8.
"""

from pathlib import Path

import yaml

MESSAGES = Path(__file__).resolve().parents[2] / "seed" / "portal_messages.yaml"
INTENTS = {"status_update", "query_raised", "rejected", "sanctioned", "question", "approval_reply", "other"}


def test_fixture_has_15_messages_with_known_intents() -> None:
    msgs = yaml.safe_load(MESSAGES.read_text(encoding="utf-8"))
    assert len(msgs) == 15
    assert {m["expect"]["intent"] for m in msgs} == INTENTS
