"""claude.py: caching layout, routing, max_tokens, citation parsing, cost metering. No DB, no network."""

from types import SimpleNamespace

import pytest

from app.brain import claude
from app.brain.tools import TOOLS
from app.costs import UsageMeter, cost_usd, price_for, usage_tokens
from app.rules.loader import load_schemes


def test_system_blocks_cache_rulebook() -> None:
    blocks = claude.build_system_blocks("SYSTEM", claude.rulebook_summary(load_schemes()))
    assert "cache_control" not in blocks[0]
    assert blocks[1]["cache_control"] == {"type": "ephemeral"} and "pmegp" in blocks[1]["text"]


def test_rulebook_summary_is_stable() -> None:
    schemes = load_schemes()
    assert claude.rulebook_summary(schemes) == claude.rulebook_summary(list(reversed(schemes)))


def test_cached_tools_marks_only_last_and_copies() -> None:
    cached = claude.cached_tools(TOOLS)
    assert [("cache_control" in t) for t in cached] == [False] * (len(TOOLS) - 1) + [True]
    assert all("cache_control" not in t for t in TOOLS)
    assert [t["name"] for t in TOOLS] == [
        "update_profile",
        "match_schemes",
        "explain_match",
        "ask_user",
        "create_application",
        "transition_application",
        "finish_run",
    ]


def test_loop_call_routing_and_max_tokens(fake_claude) -> None:
    fake_claude.add({"loop": [{"content": [{"text": "hi"}]}]})
    claude.loop_call([], [], [{"role": "user", "content": "x"}], UsageMeter())
    call = fake_claude.calls[-1]
    assert (call["model"], call["max_tokens"]) == ("claude-sonnet-4-5", 1200)


def test_forced_tool_call_long_budget(fake_claude) -> None:
    fake_claude.add({"explain": [{"content": [{"tool": "explanation", "input": {"en": "a", "hi": "b"}}]}]})
    out = claude.forced_tool_call(
        {"name": "explanation", "input_schema": {}}, [], [], UsageMeter(), purpose="explain"
    )
    call = fake_claude.calls[-1]
    assert out == {"en": "a", "hi": "b"}
    assert call["max_tokens"] == 2500 and call["tool_choice"] == {"type": "tool", "name": "explanation"}


def test_scanner_uses_cheap_model(fake_claude) -> None:
    fake_claude.add({"scan": [{"content": [{"text": '["Super Yojana"]'}]}]})
    assert claude.cheap_scheme_extractor(UsageMeter())("text") == ["Super Yojana"]
    assert fake_claude.calls[-1]["model"] == "claude-haiku-4-5"


def test_citations_call_documents_cached_and_parsed(fake_claude) -> None:
    pages = [(3, "The applicant must be above 18 years of age."), (4, "Cost limits apply.")]
    out = claude.citations_call(pages, "PMEGP", "Explain.", UsageMeter())
    docs = [p for p in fake_claude.calls[-1]["messages"][0]["content"] if p["type"] == "document"]
    assert [d["title"] for d in docs] == ["PMEGP guidelines page 3", "PMEGP guidelines page 4"]
    assert all(d["citations"] == {"enabled": True} for d in docs)
    assert "cache_control" in docs[-1] and "cache_control" not in docs[0]
    assert out["citations"] == [{"page": 3, "cited_text": "above 18 years of age"}]
    assert out["en"].startswith("You qualify") and out["hi"].startswith("आप")


def test_parse_cited_answer_requires_both_languages() -> None:
    resp = SimpleNamespace(content=[SimpleNamespace(type="text", text="no markers", citations=None)])
    with pytest.raises(claude.ClaudeError):
        claude.parse_cited_answer(resp, [])


def test_no_real_client_in_tests() -> None:
    claude.set_client(None)
    with pytest.raises(Exception):  # noqa: B017 - either missing key or the conftest network block
        claude.get_client()


# ---------------------------------------------------------------- costs


def test_price_lookup_by_prefix() -> None:
    assert price_for("claude-sonnet-4-5-20250929")["output"] == 15.00
    assert price_for("claude-haiku-4-5")["cache_read"] == 0.10
    assert price_for("some-future-model")["output"] == 15.00  # unknown → most expensive


def test_cost_hand_calculated() -> None:
    # Sonnet: 1,200 in × $3 + 150 out × $15 + 4,000 cache-read × $0.30 + 2,000 cache-write × $3.75, per million
    # = 0.0036 + 0.00225 + 0.0012 + 0.0075 = $0.01455
    tokens = usage_tokens(
        {
            "input_tokens": 1200,
            "output_tokens": 150,
            "cache_read_input_tokens": 4000,
            "cache_creation_input_tokens": 2000,
        }
    )
    assert cost_usd("claude-sonnet-4-5", tokens) == pytest.approx(0.01455)


def test_meter_accumulates_across_models() -> None:
    meter = UsageMeter()
    meter.add("claude-sonnet-4-5", {"input_tokens": 1_000_000})
    meter.add("claude-haiku-4-5", {"output_tokens": 1_000_000})
    assert meter.calls == 2 and meter.cost == pytest.approx(3.00 + 5.00)
    assert meter.by_model == {"claude-sonnet-4-5": 3.0, "claude-haiku-4-5": 5.0}
