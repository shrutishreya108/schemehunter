"""explain_match sends few guideline pages (FakeClaude; the real pmegp text is indexed by sync_sources)."""

import re

import pytest

from app.brain.tools import dispatch
from app.brain.tools import explain_match as em
from app.brain.tools.base import Ctx
from app.config import get_settings
from app.rules.loader import load_schemes
from app.rules.sources import SOURCES_DIR

pytestmark = pytest.mark.db
PMEGP_PAGES = SOURCES_DIR / "pmegp" / "pages"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _ctx(conn, case_id: str, fake) -> Ctx:
    return Ctx(case_id=case_id, conn=conn, schemes=load_schemes(), language="en", state_code="KA", client=fake)


def _fresh_pmegp_explanation(conn, case_id: str, fake, cost: int = 500000) -> dict:
    """Matches for this case, and a pmegp explanation that is not cached (so the model is called)."""
    ctx = _ctx(conn, case_id, fake)
    dispatch("update_profile", {"key": "project_cost", "value": cost}, ctx)
    dispatch("match_schemes", {}, ctx)
    conn.execute("update matches set explanation = null where case_id = %s and scheme_id = 'pmegp'", (case_id,))
    row = conn.execute(
        "select rule_results from matches where case_id = %s and scheme_id = 'pmegp'", (case_id,)
    ).fetchone()
    return row["rule_results"]


def _whole_document_chars() -> int:
    return sum(len(p.read_text(encoding="utf-8")) for p in PMEGP_PAGES.glob("[0-9][0-9].txt"))


def test_at_most_four_pages_and_under_a_quarter_of_the_text(seeded, conn, fake_claude) -> None:
    _fresh_pmegp_explanation(conn, seeded["meena"], fake_claude)
    out = dispatch("explain_match", {"scheme_id": "pmegp"}, _ctx(conn, seeded["meena"], fake_claude))
    assert out["source_status"] == "cited"
    docs = [
        d
        for c in fake_claude.calls_for("explain_cited")
        for d in c["messages"][0]["content"]
        if d["type"] == "document"
    ]
    assert 1 <= len(docs) <= em.MAX_PAGES
    sent_chars = sum(len(d["source"]["data"]) for d in docs)
    whole = _whole_document_chars()
    print(f"\nexplain_match sent {len(docs)} pages, {sent_chars} chars of {whole} ({sent_chars / whole:.1%})")
    assert sent_chars < 0.25 * _whole_document_chars(), (sent_chars, _whole_document_chars())
    assert out["pages"] == sorted(out["pages"]) and len(out["pages"]) == len(docs)


def test_the_deciding_rules_passage_is_among_the_pages_sent(seeded, conn, fake_claude) -> None:
    # a 60 lakh project fails the cost cap in both sectors, so there is a deciding rule to check
    _fresh_pmegp_explanation(conn, seeded["meena"], fake_claude, cost=6000000)
    decider = conn.execute(
        "select rule_results->>'deciding_rule' as d from matches where case_id = %s and scheme_id = 'pmegp'",
        (seeded["meena"],),
    ).fetchone()["d"]
    passage = conn.execute(
        "select passage from rule_evidence where scheme_id = 'pmegp' and rule_id = %s and passage is not null "
        "order by page limit 1",
        (decider,),
    ).fetchone()
    assert passage, f"no evidence passage for deciding rule {decider}"
    out = dispatch("explain_match", {"scheme_id": "pmegp"}, _ctx(conn, seeded["meena"], fake_claude))
    sent = {n: (PMEGP_PAGES / f"{n:02d}.txt").read_text(encoding="utf-8") for n in out["pages"]}
    assert any(_norm(passage["passage"]) in _norm(text) for text in sent.values()), (decider, out["pages"])


def test_with_no_search_hits_the_first_six_pages_are_sent(seeded, conn, fake_claude, monkeypatch) -> None:
    _fresh_pmegp_explanation(conn, seeded["meena"], fake_claude)
    monkeypatch.setattr(em, "pages_for", lambda *a, **k: [])
    monkeypatch.setattr(em, "verified_pages", lambda *a, **k: [])  # no evidence pages either
    out = dispatch("explain_match", {"scheme_id": "pmegp"}, _ctx(conn, seeded["meena"], fake_claude))
    assert out["page_selection"] == "fallback"
    assert out["pages"] == [1, 2, 3, 4, 5, 6]
    get_settings.cache_clear()


def test_tool_called_event_records_the_page_numbers_only(seeded, conn, fake_claude) -> None:
    import json

    from app.brain.runner import run_brain

    conn.execute("update matches set explanation = null where case_id = %s and scheme_id = 'pmegp'", (seeded["meena"],))
    finish = {"content": [{"tool": "finish_run", "input": {"user_message": "ठीक है।"}}]}
    fake_claude.add(
        {
            "loop": [
                {"content": [{"tool": "match_schemes", "input": {}}]},
                {"content": [{"tool": "explain_match", "input": {"scheme_id": "pmegp"}}]},
                finish,
            ]
        }
    )
    run_brain(seeded["meena"], "explain pmegp", "chat", conn=conn)
    events = conn.execute(
        "select payload from events where case_id = %s and type = 'TOOL_CALLED'", (seeded["meena"],)
    ).fetchall()
    payloads = [e["payload"] for e in events if (e["payload"] or {}).get("tool") == "explain_match"]
    assert payloads and payloads[0]["pages"] and all(isinstance(n, int) for n in payloads[0]["pages"])
    assert "EN:" not in json.dumps(payloads[0]) and "page" not in json.dumps(payloads[0]).lower().replace("pages", "")
