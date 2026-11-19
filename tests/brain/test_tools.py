"""Tools: dispatch validation, each tool's behaviour, explain_match's cited / pending / cached paths."""

import pytest

from app.brain.tools import REGISTRY, dispatch
from app.brain.tools.base import Ctx
from app.brain.tools.facts_vocab import VocabError, coerce
from app.config import get_settings
from app.rules.loader import load_schemes

SCHEMES = load_schemes()


def pure_ctx() -> Ctx:
    return Ctx(case_id="c", conn=None, schemes=SCHEMES)  # type: ignore[arg-type]


# ---------------------------------------------------------------- no DB


def test_every_tool_has_schema_and_handle() -> None:
    for name, module in REGISTRY.items():
        assert module.SCHEMA["name"] == name and module.SCHEMA["input_schema"]["type"] == "object"
        assert callable(module.handle) and module.Input


def test_unknown_tool_is_an_error_not_an_exception() -> None:
    out = dispatch("send_email", {"to": "bank@x"}, pure_ctx())
    assert "unknown tool 'send_email'" in out["error"] and "human approves" in out["error"]


def test_invalid_input_is_an_error() -> None:
    assert "invalid input" in dispatch("ask_user", {"question_hi": "?"}, pure_ctx())["error"]
    assert "invalid input" in dispatch("finish_run", {"next_check_in_days": 999}, pure_ctx())["error"]


@pytest.mark.parametrize(
    ("key", "raw", "clean"),
    [
        ("loan_amount", "3,00,000", 300000),
        ("loan_amount", 300000, 300000),
        ("in_default", "no", False),
        ("previous_tarun_repaid", "हाँ", True),
        ("gender", "Female", "female"),
        ("dob", "1991-04-12", "1991-04-12"),
        ("education_level", "10th", "10th"),
    ],
)
def test_coerce(key, raw, clean) -> None:
    assert coerce(key, raw) == clean


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("loan_amount", "teen lakh"),
        ("loan_amount", True),
        ("salary", 1),
        ("sector", "farming"),
        ("dob", "12/04/1991"),
    ],
)
def test_coerce_rejects(key, raw) -> None:
    with pytest.raises(VocabError):
        coerce(key, raw)


# ---------------------------------------------------------------- DB


def db_ctx(conn, case_id: str, lang: str = "hi") -> Ctx:
    return Ctx(case_id=case_id, conn=conn, schemes=SCHEMES, language=lang, state_code="KA")


@pytest.mark.db
def test_update_profile_and_match(seeded, conn) -> None:
    ctx = db_ctx(conn, seeded["meena"])
    assert dispatch("update_profile", {"key": "loan_amount", "value": "300000"}, ctx) == {
        "ok": True,
        "key": "loan_amount",
        "value": 300000,
    }
    out = dispatch("match_schemes", {}, ctx)
    results = {r["scheme_id"]: r["result"] for r in out["results"]}
    assert results["pmegp"] == "FIT" and results["mudra"] == "FIT" and results["stand_up_india"] == "NO_FIT"
    assert out["next_fact_to_ask"] == "enterprise_size"  # cgtmse is first in file order
    stored = conn.execute(
        "select count(*) as n from matches where case_id = %s", (seeded["meena"],)
    ).fetchone()
    assert stored["n"] == 5


@pytest.mark.db
def test_match_clears_stale_explanation_when_result_changes(seeded, conn) -> None:
    ctx = db_ctx(conn, seeded["ravi"])
    dispatch("update_profile", {"key": "business_stage", "value": "existing"}, ctx)
    dispatch("match_schemes", {}, ctx)
    row = conn.execute(
        "select result, explanation from matches where case_id = %s and scheme_id = 'pmegp'",
        (seeded["ravi"],),
    ).fetchone()
    assert row["result"] == "NO_FIT" and row["explanation"] is None


@pytest.mark.db
def test_explain_cached_seed_makes_no_claude_call(seeded, conn, fake_claude) -> None:
    out = dispatch("explain_match", {"scheme_id": "pmegp"}, db_ctx(conn, seeded["ravi"]))
    assert out["cached"] is True and out["explanation"].startswith("आप PMEGP")
    assert fake_claude.calls == []


@pytest.mark.db
def test_explain_pending_when_no_pages(seeded, conn, fake_claude, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RULEBOOK_SOURCES_DIR", str(tmp_path))
    get_settings.cache_clear()
    ctx = db_ctx(conn, seeded["meena"])
    dispatch("match_schemes", {}, ctx)
    out = dispatch("explain_match", {"scheme_id": "pmegp"}, ctx)
    exp = conn.execute(
        "select explanation from matches where case_id = %s and scheme_id = 'pmegp'", (seeded["meena"],)
    ).fetchone()["explanation"]
    assert out["source_status"] == "pending" and exp["citations"] == [] and exp["source_status"] == "pending"
    assert fake_claude.calls_for("explain")[0]["model"] == "claude-sonnet-4-5"
    assert fake_claude.calls_for("explain")[0]["max_tokens"] == 2500
    assert fake_claude.calls_for("scan")  # explanation scanned by the cheap model
    get_settings.cache_clear()


@pytest.mark.db
def test_every_fit_with_pages_has_a_citation(seeded, conn, fake_claude, tmp_path, monkeypatch) -> None:
    """Applies only to schemes whose guideline pages exist: here pmegp and mudra get pages, others don't."""
    for scheme_id in ("pmegp", "mudra"):
        pages = tmp_path / scheme_id / "pages"
        pages.mkdir(parents=True)
        (pages / "03.txt").write_text("The applicant must be above 18 years of age.", encoding="utf-8")
        (pages / "04.txt").write_text("Project cost limits apply.", encoding="utf-8")
    monkeypatch.setenv("RULEBOOK_SOURCES_DIR", str(tmp_path))
    get_settings.cache_clear()
    ctx = db_ctx(conn, seeded["meena"])
    dispatch("update_profile", {"key": "loan_amount", "value": 300000}, ctx)
    fits = [r["scheme_id"] for r in dispatch("match_schemes", {}, ctx)["results"] if r["result"] == "FIT"]
    with_pages = [s for s in fits if (tmp_path / s / "pages").is_dir()]
    assert set(with_pages) == {"pmegp", "mudra"}
    for scheme_id in with_pages:
        out = dispatch("explain_match", {"scheme_id": scheme_id}, ctx)
        assert out["source_status"] == "cited" and out["citations"] >= 1, scheme_id
    docs_per_call = [
        [p for p in c["messages"][0]["content"] if p["type"] == "document"]
        for c in fake_claude.calls_for("explain_cited")
    ]
    pmegp_docs = next(d for d in docs_per_call if d[0]["title"].startswith("PM Employment"))
    assert [d["title"] for d in pmegp_docs] == [
        "PM Employment Generation Programme guidelines page 3",
        "PM Employment Generation Programme guidelines page 4",
    ]
    get_settings.cache_clear()


@pytest.mark.db
def test_explanation_failing_guard_falls_back(seeded, conn, fake_claude, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RULEBOOK_SOURCES_DIR", str(tmp_path))
    get_settings.cache_clear()
    fake_claude.add(
        {
            "explain": [
                {
                    "content": [
                        {
                            "tool": "explanation",
                            "input": {"en": "Your loan is guaranteed.", "hi": "लोन पक्का है।"},
                        }
                    ]
                }
            ]
        }
    )
    ctx = db_ctx(conn, seeded["meena"], lang="en")
    dispatch("match_schemes", {}, ctx)
    out = dispatch("explain_match", {"scheme_id": "pmegp"}, ctx)
    assert (
        out["explanation"]
        == "PM Employment Generation Programme: Preliminary: you may qualify to apply. The rules are not yet checked against official guidelines."
    )
    get_settings.cache_clear()


@pytest.mark.db
def test_explain_requires_match_first(seeded, conn) -> None:
    conn.execute("delete from matches where case_id = %s", (seeded["meena"],))
    assert (
        "match_schemes first"
        in dispatch("explain_match", {"scheme_id": "pmegp"}, db_ctx(conn, seeded["meena"]))["error"]
    )
    assert (
        "unknown scheme_id"
        in dispatch("explain_match", {"scheme_id": "nope"}, db_ctx(conn, seeded["meena"]))["error"]
    )


@pytest.mark.db
def test_ask_user_one_question_and_guard(seeded, conn) -> None:
    ctx = db_ctx(conn, seeded["meena"])
    q = {
        "question_hi": "आपकी सालाना पारिवारिक आय कितनी है?",
        "question_en": "What is your yearly family income?",
        "expected_fact": "family_income",
    }
    assert dispatch("ask_user", q, ctx) == {"ok": True, "shown_to_user": True}
    assert "one question per run" in dispatch("ask_user", q, ctx)["error"]
    bad = dispatch(
        "ask_user", {**q, "question_en": "You will get the loan, ok?"}, db_ctx(conn, seeded["meena"])
    )
    assert "blocked by guard" in bad["error"]
    row = conn.execute(
        "select body, attachments from messages where case_id = %s and direction = 'out'", (seeded["meena"],)
    ).fetchone()
    assert row["attachments"]["expected_fact"] == "family_income" and row["body"].startswith("आपकी")


@pytest.mark.db
def test_finish_run_rewrites_then_safe_fallback(seeded, conn) -> None:
    ctx = db_ctx(conn, seeded["meena"])
    bad = {"user_message": "पैसा पक्का मिलेगा।"}
    assert "blocked by guard" in dispatch("finish_run", bad, ctx)["error"]
    assert "blocked by guard" in dispatch("finish_run", bad, ctx)["error"]
    assert dispatch("finish_run", bad, ctx)["ok"] is True  # third time: fallback, run ends
    assert ctx.finished and ctx.user_message == "मैं जाँच करके आपको जल्दी बताऊँगी।"
    sent = [
        r["body"]
        for r in conn.execute(
            "select body from messages where case_id = %s and direction = 'out'", (seeded["meena"],)
        )
    ]
    assert sent == ["मैं जाँच करके आपको जल्दी बताऊँगी।"]
