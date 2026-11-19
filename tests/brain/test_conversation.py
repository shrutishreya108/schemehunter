"""Conversation handling (fixes 1, 2, 5): real turns, alternation, the instruction, and the chat task text."""

import pytest

from app.brain.history import HISTORY_MESSAGES, TURN_CHARS, conversation, merge_alternating
from app.routes.chat import CHAT_TASK, chat_task

pytestmark = pytest.mark.db


def turn(role: str, text: str) -> dict:
    return {"role": role, "content": text}


def test_merge_keeps_alternation_and_starts_with_a_user_turn() -> None:
    merged = merge_alternating(
        [turn("assistant", "hi"), turn("user", "a"), turn("user", "b"), turn("assistant", "c")]
    )
    assert [t["role"] for t in merged] == ["user", "assistant"]
    assert merged[0]["content"] == "a\nb"


def test_empty_turns_are_dropped() -> None:
    assert merge_alternating([turn("user", ""), turn("user", "x")]) == [turn("user", "x")]


def add(conn, case_id, direction, body, minutes_ago=0):
    conn.execute(
        "insert into messages (case_id, direction, channel, body, processed, created_at) "
        "values (%s, %s, 'web', %s, true, now() - make_interval(mins => %s))",
        (case_id, direction, body, minutes_ago),
    )
    conn.commit()


def test_conversation_holds_earlier_turns_then_one_instruction(seeded, conn) -> None:
    cid = seeded["meena"]
    conn.execute("delete from messages where case_id = %s", (cid,))
    add(conn, cid, "in", "Hello", 5)
    add(conn, cid, "out", "Hi, tell me about your business.", 4)
    add(conn, cid, "in", "I make garments", 3)
    add(conn, cid, "out", "How much loan do you need?", 2)
    add(conn, cid, "in", "What is Aadhaar masking?", 1)  # the latest user message
    msgs = conversation(conn, cid, "INSTRUCTION")
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant", "user"]
    assert msgs[0]["content"] == "Hello" and msgs[-1] == {"role": "user", "content": "INSTRUCTION"}
    assert "What is Aadhaar masking?" not in str(msgs[:-1])  # the latest message is not sent twice


def test_history_window_and_turn_length_are_capped(seeded, conn) -> None:
    cid = seeded["meena"]
    conn.execute("delete from messages where case_id = %s", (cid,))
    for i in range(20):
        add(conn, cid, "in" if i % 2 == 0 else "out", f"m{i} " + "x" * 3000, 30 - i)
    msgs = conversation(conn, cid, "INSTRUCTION")
    assert len(msgs) <= HISTORY_MESSAGES + 1
    assert all(len(m["content"]) <= TURN_CHARS + 2 or m["content"] == "INSTRUCTION" for m in msgs)


def test_chat_task_has_no_forced_onboarding() -> None:
    task = chat_task("What is PMEGP?")
    assert "Continue onboarding" not in task and "onboarding" not in task.lower()
    assert "Answer every question" in CHAT_TASK and "What is PMEGP?" in task
