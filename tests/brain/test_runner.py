"""run_brain(): step cap, budget/per-case guards, error-return path, finish_run required, cost accounting."""

import json

import pytest

from app.brain import runner
from app.brain.runner import run_brain
from app.config import get_settings

pytestmark = pytest.mark.db

FINISH = {"content": [{"tool": "finish_run", "input": {"user_message": "ठीक है।"}}]}


def tool(name: str, **inp) -> dict:
    return {"content": [{"tool": name, "input": inp}]}


def run_row(conn, run_id: str) -> dict:
    return conn.execute("select * from brain_runs where id = %s", (run_id,)).fetchone()


def events_of(conn, case_id: str, type_: str) -> list[dict]:
    return conn.execute("select * from events where case_id = %s and type = %s", (case_id, type_)).fetchall()


def test_happy_run_records_usage_and_cost(seeded, conn, fake_claude) -> None:
    fake_claude.add({"loop": [tool("match_schemes"), FINISH]})
    result = run_brain(seeded["meena"], "hello", "chat", conn=conn)
    row = run_row(conn, result.run_id)
    assert (result.status, result.steps, row["status"], row["steps"]) == ("DONE", 2, "DONE", 2)
    assert row["input_tokens"] > 0 and row["cache_read_tokens"] > 0
    assert (
        float(row["estimated_cost_usd"]) == pytest.approx(result.cost_usd, abs=1e-6) and result.cost_usd > 0
    )
    first = fake_claude.calls_for("loop")[0]
    assert first["system"][-1]["cache_control"] == {"type": "ephemeral"}  # Rule Book block cached
    assert first["tools"][-1]["cache_control"] == {"type": "ephemeral"}  # tool definitions cached
    assert "CASE FILE" in first["messages"][0]["content"]  # case file is the per-run part


def test_step_cap_writes_run_stuck_and_alerts(seeded, conn, fake_claude, monkeypatch) -> None:
    alerts = []
    monkeypatch.setattr(runner, "alert_curator", lambda run_id, case_id, reason: alerts.append(reason))
    fake_claude.add({"loop": [tool("match_schemes") for _ in range(20)]})
    result = run_brain(seeded["meena"], "loop forever", "chat", conn=conn)
    assert (result.status, result.steps) == ("STUCK", 12)
    assert len(fake_claude.calls_for("loop")) == 12  # never a 13th call
    assert events_of(conn, seeded["meena"], "RUN_STUCK") and alerts == ["RUN_STUCK"]
    assert run_row(conn, result.run_id)["status"] == "STUCK"


def test_finish_run_required(seeded, conn, fake_claude, monkeypatch) -> None:
    monkeypatch.setattr(runner, "alert_curator", lambda *a: None)
    fake_claude.add({"loop": [{"content": [{"text": "All done, bye!"}]}]})
    result = run_brain(seeded["meena"], "hello", "chat", conn=conn)
    assert result.status == "STUCK" and result.steps == 1
    assert events_of(conn, seeded["meena"], "RUN_STUCK")


def test_tool_error_returns_to_model_not_raised(seeded, conn, fake_claude) -> None:
    fake_claude.add(
        {
            "loop": [
                tool("update_profile", key="loan_amount", value="lots"),
                tool("update_profile", key="loan_amount", value=300000),
                FINISH,
            ]
        }
    )
    result = run_brain(seeded["meena"], "answer", "chat", conn=conn)
    assert result.status == "DONE"
    second = fake_claude.calls_for("loop")[1]["messages"][-1]["content"][0]
    assert second["type"] == "tool_result" and second["is_error"] is True
    assert "whole number of rupees" in json.loads(second["content"])["error"]
    value = conn.execute(
        "select value from profile_answers where case_id = %s and key = 'loan_amount'", (seeded["meena"],)
    ).fetchone()["value"]
    assert value == 300000


def test_tool_crash_is_contained(seeded, conn, fake_claude, monkeypatch) -> None:
    from app.brain.tools import match_schemes

    def boom(data, ctx):
        ctx.conn.execute(
            "insert into profile_answers (case_id, key, value, source) values (%s, 'x', '1', 'typed')",
            (ctx.case_id,),
        )
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(match_schemes, "handle", boom)
    fake_claude.add({"loop": [tool("match_schemes"), FINISH]})
    assert run_brain(seeded["meena"], "x", "chat", conn=conn).status == "DONE"
    out = json.loads(fake_claude.calls_for("loop")[1]["messages"][-1]["content"][0]["content"])
    assert "failed (RuntimeError)" in out["error"]
    assert not conn.execute(
        "select 1 from profile_answers where key = 'x'"
    ).fetchone()  # savepoint rolled back


def test_budget_guard_defers_without_calling_claude(seeded, conn, fake_claude) -> None:
    conn.execute(
        "insert into brain_runs (case_id, status, estimated_cost_usd, started_at) values (%s, 'DONE', %s, now())",
        (seeded["ravi"], get_settings().DAILY_BUDGET_USD),
    )
    conn.commit()
    result = run_brain(seeded["meena"], "hello", "chat", conn=conn)
    assert (result.status, result.reason) == ("DEFERRED", "budget") and fake_claude.calls == []
    job = conn.execute(
        "select * from jobs where case_id = %s and type = 'brain_run'", (seeded["meena"],)
    ).fetchone()
    assert job["payload"]["deferred"] == "budget"  # re-queued for tomorrow
    fake_claude.add({"loop": [FINISH]})
    assert run_brain(seeded["meena"], "deadline!", "job", conn=conn, deadline_related=True).status == "DONE"


def test_per_case_daily_cap(seeded, conn, fake_claude, monkeypatch) -> None:
    monkeypatch.setenv("MAX_RUNS_PER_CASE_PER_DAY", "2")
    get_settings.cache_clear()
    fake_claude.add({"loop": [FINISH, FINISH, FINISH]})
    assert [run_brain(seeded["meena"], "x", "chat", conn=conn).status for _ in range(3)] == [
        "DONE",
        "DONE",
        "DEFERRED",
    ]
    assert run_brain(seeded["ravi"], "x", "chat", conn=conn).status == "DONE"  # other cases unaffected
    get_settings.cache_clear()


def test_api_failure_is_recorded_politely(seeded, conn, fake_claude) -> None:
    def down(**kwargs):
        raise ConnectionError("api down")

    fake_claude.create = down
    result = run_brain(seeded["meena"], "x", "chat", conn=conn)
    assert result.status == "ERROR" and result.user_message.startswith("I'll get back")
    assert run_row(conn, result.run_id)["error"] == "ConnectionError"


def test_admin_runs_page_shows_cost_and_running_total(seeded, conn, fake_claude, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    fake_claude.add({"loop": [FINISH, FINISH]})
    costs = [run_brain(seeded["meena"], "x", "chat", conn=conn).cost_usd for _ in range(2)]
    with TestClient(app) as client:
        assert client.get("/admin/runs").status_code == 401
        page = client.get("/admin/runs", auth=("curator", "test-admin")).text
    assert f"{costs[0]:.6f}" in page and f"{sum(costs):.4f}" in page  # per-run cost + running total
    assert "of $10.00 daily budget" in page
