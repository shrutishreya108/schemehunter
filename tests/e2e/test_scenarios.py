"""Scenarios A-G from the conversation brief. Each one checks the reply and that the application state survives.

The model is FakeClaude. These tests check what our code does with each reply and what it sends to the model.
"""

import itertools

import pytest

from app.brain.runner import run_brain
from app.brain.tools import dispatch
from app.brain.tools.base import Ctx
from app.clock import handlers
from app.routes.chat import chat_task
from app.rules.loader import load_schemes

pytestmark = pytest.mark.db


def ctx_for(conn, case_id: str, fake) -> Ctx:
    return Ctx(
        case_id=case_id, conn=conn, schemes=load_schemes(), state_code="KA", language="en", client=fake
    )


def qa_reply(*answers: dict) -> dict:
    return {"content": [{"tool": "answers", "input": {"answers": list(answers)}}]}


def fact(q: int, answer: str, label: str, sources: list[str]) -> dict:
    return {"q": q, "answer": answer, "label": label, "sources": sources}


def first_app(conn, case_id: str) -> dict:
    """The person's first application; starts one (DISCOVERED -> ELIGIBLE) when the seed has none."""
    from app.tracker.store import create_application

    row = conn.execute(
        "select * from applications where case_id = %s order by created_at limit 1", (case_id,)
    ).fetchone()
    if row is None:
        create_application(conn, case_id, "pmegp")
        conn.commit()
        row = conn.execute(
            "select * from applications where case_id = %s order by created_at limit 1", (case_id,)
        ).fetchone()
    return row


def add_message(conn, case_id: str, direction: str, body: str, minutes_ago: float = 0) -> None:
    conn.execute(
        "insert into messages (case_id, direction, channel, body, processed, created_at) "
        "values (%s, %s, 'web', %s, true, now() - make_interval(mins => %s))",
        (case_id, direction, body, minutes_ago),
    )
    conn.commit()


# ---------------------------------------------------------------- A: six questions about PMEGP documents and privacy


def test_a_six_questions_each_get_a_labelled_answer(seeded, conn, fake_claude) -> None:
    ctx = ctx_for(conn, seeded["meena"], fake_claude)
    fake_claude.add(
        {
            "qa": [
                qa_reply(
                    fact(
                        1,
                        "Papers are encrypted on the server before they are saved.",
                        "verified",
                        ["code:encryption"],
                    ),
                    fact(
                        2,
                        "PMEGP needs an Aadhaar, a photo, a passbook and a project report, per the rule file.",
                        "estimate",
                        ["rulefile:pmegp"],
                    ),
                    fact(
                        3, "Aadhaar masking is accepted by the bank.", "verified", ["guideline:pmegp:p3"]
                    ),  # no such source
                    fact(
                        4,
                        "There is no delete button yet. Papers are deleted 90 days after a case closes.",
                        "verified",
                        ["code:no-delete"],
                    ),
                    fact(5, "Your papers are safe.", "verified", []),  # no source: must not stay verified
                    fact(
                        6, "We guarantee approval.", "verified", ["code:masking"]
                    ),  # a promise: must be replaced
                )
            ]
        }
    )
    out = dispatch("answer_question", {"questions": [f"Question {i}" for i in range(1, 7)]}, ctx)
    answers = out["answers"]
    assert [a["q"] for a in answers] == [1, 2, 3, 4, 5, 6]
    assert [a["label"] for a in answers] == [
        "verified",
        "estimate",
        "unknown",
        "verified",
        "unknown",
        "unknown",
    ]
    assert answers[2]["sources"] == [] and answers[1]["sources"] == ["rulefile:pmegp"]
    assert "guarantee" not in str(answers).lower()
    assert all(a["answer"] for a in answers)


def test_a_sources_sent_to_the_model_are_grounded_and_labelled(seeded, conn, fake_claude) -> None:
    ctx = ctx_for(conn, seeded["meena"], fake_claude)
    fake_claude.add({"qa": [qa_reply(fact(1, "x", "unknown", []))]})
    dispatch("answer_question", {"questions": ["What is encrypted?"]}, ctx)
    prompt = fake_claude.calls_for("qa")[-1]["messages"][0]["content"]
    assert "[code:encryption] (verified)" in prompt
    # retrieval finds no guideline text for this question, and the gap is stated, not hidden
    assert "[guideline:pmegp:none] (unknown) No guideline text for pmegp matched these questions." in prompt
    assert "[rulefile:pmegp] (estimate)" in prompt


# ---------------------------------------------------------------- B: the next pending profile answer


def test_b_answering_the_pending_profile_question_keeps_the_application(seeded, conn, fake_claude) -> None:
    ctx = ctx_for(conn, seeded["meena"], fake_claude)
    app = first_app(conn, seeded["meena"])
    assert dispatch("update_profile", {"key": "loan_amount", "value": 300000}, ctx) == {
        "ok": True,
        "key": "loan_amount",
        "value": 300000,
    }
    assert dispatch("match_schemes", {}, ctx)["results"]
    assert first_app(conn, seeded["meena"])["state"] == app["state"]


# ---------------------------------------------------------------- C: an unrelated question during onboarding


def test_c_unrelated_question_is_answered_not_replaced_by_a_profile_question(
    seeded, conn, fake_claude
) -> None:
    case_id = seeded["meena"]
    conn.execute("delete from messages where case_id = %s", (case_id,))
    add_message(conn, case_id, "in", "Hello, what is PMEGP?", 2)
    add_message(conn, case_id, "out", "PMEGP is a scheme. How much loan do you need?", 1)
    add_message(conn, case_id, "in", "Does PMEGP cover a shop rent?", 0)
    fake_claude.add(
        {
            "qa": [qa_reply(fact(1, "I don't have verified information on rent.", "unknown", []))],
            "loop": [
                {
                    "content": [
                        {"tool": "answer_question", "input": {"questions": ["Does PMEGP cover a shop rent?"]}}
                    ]
                },
                {
                    "content": [
                        {
                            "tool": "finish_run",
                            "input": {
                                "user_message": "1. I don't have verified information on shop rent yet."
                            },
                        }
                    ]
                },
            ],
        }
    )
    result = run_brain(case_id, chat_task("Does PMEGP cover a shop rent?"), "chat", conn=conn)
    assert result.status == "DONE" and "shop rent" in result.user_message
    first = fake_claude.calls_for("loop")[0]
    assert "Continue onboarding" not in str(first["messages"])
    roles = [m["role"] for m in first["messages"]]
    assert roles[0] == "user" and all(a != b for a, b in itertools.pairwise(roles))  # strict alternation
    assert (
        first["messages"][-1]["role"] == "user"
        and "Does PMEGP cover a shop rent?" in first["messages"][-1]["content"]
    )
    assert not any(
        e["type"] == "TOOL_CALLED" and "ask_user" in str(e)
        for e in conn.execute(
            "select type, payload::text as p from events where case_id = %s", (case_id,)
        ).fetchall()
    )


# ---------------------------------------------------------------- D: only two of four papers uploaded


def test_d_partial_papers_keep_the_application_pending_and_one_reminder_a_day(
    seeded, conn, fake_claude
) -> None:
    case_id = seeded["meena"]
    app = first_app(conn, case_id)
    conn.execute("update applications set state = 'DOCS_PENDING' where id = %s", (app["id"],))
    for doc in ("aadhaar", "photo"):
        conn.execute(
            "insert into documents (case_id, doc_type, file_path, sha256, mime, check_status) "
            "values (%s, %s, '/x', %s, 'image/jpeg', 'OK')",
            (case_id, doc, doc),
        )
    conn.commit()
    job = {"application_id": app["id"], "case_id": case_id, "payload": {}}
    handlers.doc_nudge(conn, job)
    handlers.doc_nudge(conn, job)  # the same day again
    nudges = conn.execute(
        "select body from messages where case_id = %s and direction = 'out' and body ilike '%%still needed%%'",
        (case_id,),
    ).fetchall()
    assert len(nudges) == 1 and "passbook" in nudges[0]["body"].lower()
    assert first_app(conn, case_id)["state"] == "DOCS_PENDING"


def test_d_reminders_stop_when_everything_is_in(seeded, conn, monkeypatch) -> None:
    case_id = seeded["meena"]
    app = first_app(conn, case_id)
    conn.execute("update applications set state = 'DOCS_PENDING' where id = %s", (app["id"],))
    conn.commit()
    monkeypatch.setattr(handlers, "missing_docs", lambda *a: [])  # every paper is OK
    handlers.doc_nudge(conn, {"application_id": app["id"], "case_id": case_id, "payload": {}})
    assert not conn.execute(
        "select 1 from messages where case_id = %s and body ilike '%%still needed%%'", (case_id,)
    ).fetchone()


# ---------------------------------------------------------------- E: correcting an earlier application detail


def test_e_correcting_an_answer_replaces_it_and_recomputes(seeded, conn, fake_claude) -> None:
    ctx = ctx_for(conn, seeded["meena"], fake_claude)
    dispatch("update_profile", {"key": "loan_amount", "value": 300000}, ctx)
    out = dispatch("update_profile", {"key": "loan_amount", "value": 250000}, ctx)
    assert out["ok"] and out["value"] == 250000
    rows = conn.execute(
        "select value from profile_answers where case_id = %s and key = 'loan_amount'", (seeded["meena"],)
    ).fetchall()
    assert len(rows) == 1 and rows[0]["value"] == 250000
    assert dispatch("match_schemes", {}, ctx)["results"]


# ---------------------------------------------------------------- F: the subsidy calculation, asked again


def test_f_subsidy_is_computed_in_code_and_quoted_to_the_model(seeded, conn, fake_claude) -> None:
    ctx = ctx_for(conn, seeded["meena"], fake_claude)
    fake_claude.add(
        {"qa": [qa_reply(fact(1, "The subsidy is ₹1,25,000.", "estimate", ["calc:subsidy:pmegp"]))]}
    )
    out = dispatch("answer_question", {"questions": ["What is my subsidy?"]}, ctx)
    prompt = fake_claude.calls_for("qa")[-1]["messages"][0]["content"]
    assert "₹1,25,000" in prompt and "total project cost ₹5,00,000" in prompt  # the number comes from code
    assert out["answers"][0]["label"] == "estimate" and out["answers"][0]["sources"] == ["calc:subsidy:pmegp"]


def test_f_no_cost_sheet_means_no_number_is_invented(seeded, conn, fake_claude) -> None:
    ctx = ctx_for(conn, seeded["ravi"], fake_claude)
    conn.execute("delete from artifacts where case_id = %s and kind = 'cost_inputs'", (seeded["ravi"],))
    conn.commit()
    fake_claude.add({"qa": [qa_reply(fact(1, "I can't calculate it yet.", "unknown", []))]})
    dispatch("answer_question", {"questions": ["What is my subsidy?"]}, ctx)
    assert "cannot be calculated" in fake_claude.calls_for("qa")[-1]["messages"][0]["content"]


# ---------------------------------------------------------------- G: asking to submit


def test_g_submission_needs_the_pack_and_the_person_presses_submit(
    seeded, conn, fake_claude, vault_env, fake_pdf
) -> None:
    from app.docs.generated import store_generated

    case_id = seeded["meena"]
    ctx = ctx_for(conn, case_id, fake_claude)
    app = first_app(conn, case_id)
    early = dispatch(
        "request_submit",
        {"application_id": str(app["id"]), "pack_document_id": "00000000-0000-0000-0000-000000000000"},
        ctx,
    )
    assert "error" in early
    pack = store_generated(conn, case_id, b"%PDF-1.4 pack", "prefill_pack", {"en": "Pack", "hi": "पैक"})
    conn.execute("update applications set state = 'FORM_READY' where id = %s", (app["id"],))
    conn.commit()
    out = dispatch("request_submit", {"application_id": str(app["id"]), "pack_document_id": pack}, ctx)
    assert out["state"] == "AWAITING_HUMAN_SUBMIT"
    status = conn.execute(
        "select status from pending_actions where application_id = %s and kind = 'instruct_submit'",
        (app["id"],),
    ).fetchone()["status"]
    assert status == "PENDING"  # waiting for the person's own approval
    assert first_app(conn, case_id)["state"] == "AWAITING_HUMAN_SUBMIT"  # never SUBMITTED by us
