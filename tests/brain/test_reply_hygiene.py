"""Replies never show internal source tags, and a bad cited reply still gives an explanation (no holding line)."""

import pytest

from app.brain import claude
from app.brain.tools import dispatch
from app.brain.tools.finish_run import strip_source_tags
from tests.brain.test_explain_match_tokens import _ctx, _fresh_pmegp_explanation

pytestmark = pytest.mark.db


def test_copied_source_tags_are_removed() -> None:
    assert strip_source_tags("The minimum age is 18. [verified, guideline:pmegp:p5:3] You are 30.") == (
        "The minimum age is 18. You are 30."
    )
    assert strip_source_tags("[estimate, rulefile:mudra] Fine.") == "Fine."


def test_a_cited_reply_without_the_layout_falls_back_to_a_plain_explanation(seeded, conn, fake_claude, monkeypatch) -> None:
    _fresh_pmegp_explanation(conn, seeded["meena"], fake_claude)

    def malformed(*args, **kwargs):
        raise claude.ClaudeError("cited answer missing EN:/HI: sections")

    monkeypatch.setattr(claude, "citations_call", malformed)
    out = dispatch("explain_match", {"scheme_id": "pmegp"}, _ctx(conn, seeded["meena"], fake_claude))
    assert out["source_status"] == "pending"  # explained without citations, not an error
    assert out["explanation"]
