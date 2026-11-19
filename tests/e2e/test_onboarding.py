"""Meena's live onboarding through the real HTTP routes, with FakeClaude scripts. Plus the adversarial run."""

import pytest
from fastapi.testclient import TestClient

from app.access import SESSION_COOKIE, csrf_token, sign_session

pytestmark = pytest.mark.db


@pytest.fixture
def meena_client(seeded, settings_db, tmp_path, monkeypatch):
    from app.config import get_settings
    from app.main import app

    monkeypatch.setenv("RULEBOOK_SOURCES_DIR", str(tmp_path))  # no guideline pages → "source pending"
    get_settings.cache_clear()
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sign_session(seeded["meena"]))
        yield client, seeded["meena"]
    get_settings.cache_clear()


def say(client, case_id: str, text: str):
    """Send, then poll like the page does until the reply has arrived (the send itself returns at once)."""
    r = client.post(
        "/chat", data={"text": text}, headers={"HX-Request": "true", "X-CSRF-Token": csrf_token(case_id)}
    )
    for _ in range(50):
        if "load delay:2s" not in r.text:
            break
        r = client.get("/chat/messages", headers={"HX-Request": "true"})
    return r


def test_meena_three_turns(meena_client, conn, fake_claude) -> None:
    client, case_id = meena_client
    fake_claude.load("meena_onboarding")

    r1 = say(client, case_id, "नमस्ते, मुझे सिलाई का काम शुरू करने के लिए पैसे चाहिए")
    assert r1.status_code == 200 and "How much loan do you want?" in r1.text
    page = client.get("/chat").text
    assert 'name="key" value="loan_amount"' in page  # /answers fallback offers the open question

    r2 = say(client, case_id, "तीन लाख")
    assert "PMEGP" in r2.text and "प्रधानमंत्री मुद्रा योजना" in r2.text
    matches = {
        r["scheme_id"]: r["result"]
        for r in conn.execute("select scheme_id, result from matches where case_id = %s", (case_id,))
    }
    assert matches["pmegp"] == "FIT" and matches["mudra"] == "FIT"

    r3 = say(client, case_id, "क्यों? नियम दिखाइए")
    assert "किशोर" in r3.text
    schemes = client.get("/schemes").text
    assert "source pending" in schemes and "नियम देखें" not in schemes  # grey "source pending" badge, no link
    assert "Preliminary: you may qualify to apply" in schemes

    runs = conn.execute(
        "select status, steps, estimated_cost_usd from brain_runs where case_id = %s order by started_at",
        (case_id,),
    ).fetchall()
    assert [r["status"] for r in runs] == ["DONE", "DONE", "DONE"] and [r["steps"] for r in runs] == [3, 3, 3]
    assert all(float(r["estimated_cost_usd"]) > 0 for r in runs)
    assert not conn.execute(
        "select 1 from events where case_id = %s and type = 'RUN_STUCK'", (case_id,)
    ).fetchone()
    assert not fake_claude.queues["loop"] and not fake_claude.queues["explain"]  # script fully consumed
    second_turn = fake_claude.calls_for("loop")[3]["messages"][-1][
        "content"
    ]  # the instruction, with the case file
    assert '"open_question": {"expected_fact": "loan_amount"' in second_turn  # Brain sees what it asked


def test_adversarial_send_and_invented_scheme_are_rejected(meena_client, conn, fake_claude) -> None:
    client, case_id = meena_client
    fake_claude.load("adversarial")
    r = say(client, case_id, "बैंक को अभी ईमेल कर दो")
    assert r.status_code == 200
    out = [
        m["body"]
        for m in conn.execute(
            "select body from messages where case_id = %s and direction = 'out'", (case_id,)
        )
    ]
    assert out == ["आप PMEGP के लिए आवेदन करने के योग्य हैं।"]  # only the compliant reply reached the user
    assert "महिला उद्यम शक्ति" not in r.text and "पक्का" not in r.text
    assert not conn.execute("select 1 from pending_actions").fetchone()  # nothing queued to send
    assert not conn.execute("select 1 from messages where channel = 'email'").fetchone()
    blocked = conn.execute(
        "select payload from events where case_id = %s and type = 'TOOL_CALLED' "
        "and payload->>'tool' = 'send_email'",
        (case_id,),
    ).fetchone()
    assert blocked["payload"]["ok"] is False
    results = [m["content"] for m in fake_claude.calls_for("loop")[1]["messages"][-1]["content"]]
    assert "unknown tool 'send_email'" in results[0]
    guard = fake_claude.calls_for("loop")[2]["messages"][-1]["content"][0]["content"]
    assert "blocked by guard" in guard and "महिला उद्यम शक्ति योजना" in guard


def test_chat_requires_session_and_csrf(seeded, settings_db) -> None:
    from app.main import app

    with TestClient(app) as client:
        assert client.post("/chat", data={"text": "hi"}).status_code == 401
        client.cookies.set(SESSION_COOKIE, sign_session(seeded["meena"]))
        assert client.post("/chat", data={"text": "hi"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_answers_fallback_costs_nothing(meena_client, conn, fake_claude) -> None:
    client, case_id = meena_client
    r = client.post(
        "/answers",
        data={"key": "loan_amount", "value": "300000", "csrf": csrf_token(case_id)},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == "/schemes"
    assert fake_claude.calls == []
    result = conn.execute(
        "select result from matches where case_id = %s and scheme_id = 'mudra'", (case_id,)
    ).fetchone()["result"]
    assert result == "FIT"


def test_language_toggle(meena_client, conn) -> None:
    client, case_id = meena_client
    client.get("/lang/en", headers={"referer": "/schemes"}, follow_redirects=False)
    assert "My schemes" in client.get("/schemes").text
    assert conn.execute("select language from cases where id = %s", (case_id,)).fetchone()["language"] == "en"


@pytest.mark.parametrize(
    ("referer", "target"),
    [
        ("https://schemehunter.example.in/schemes", "/schemes"),
        ("//evil.example/x", "/x"),  # host dropped: stays on our site
        ("https://schemehunter.example.in.evil.example/phish", "/phish"),
        (None, "/"),
    ],
)
def test_language_toggle_never_redirects_offsite(referer, target) -> None:
    from app.routes.pages import safe_back

    assert safe_back(referer) == target
