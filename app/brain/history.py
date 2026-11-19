"""Conversation turns for the Messages API (fix 1): recent chat as real user/assistant turns.

The window is short (HISTORY_MESSAGES) and each turn is cut to TURN_CHARS, so cost stays bounded.
Turns are merged so roles alternate, and the list starts with a user turn, as the API requires.
"""

import psycopg

HISTORY_MESSAGES = 8
TURN_CHARS = 1000


def merge_alternating(turns: list[dict]) -> list[dict]:
    out: list[dict] = []
    for t in turns:
        if not t["content"]:
            continue
        if out and out[-1]["role"] == t["role"]:
            out[-1]["content"] += "\n" + t["content"]
        else:
            out.append(dict(t))
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def chat_turns(conn: psycopg.Connection, case_id: str) -> list[dict]:
    rows = conn.execute(
        "select direction, body from messages where case_id = %s and channel = 'web' "
        "order by created_at desc, id desc limit %s",
        (case_id, HISTORY_MESSAGES),
    ).fetchall()
    turns = [
        {"role": "user" if r["direction"] == "in" else "assistant", "content": (r["body"] or "")[:TURN_CHARS]}
        for r in reversed(rows)
    ]
    return merge_alternating(turns)


def conversation(conn: psycopg.Connection, case_id: str, instruction: str) -> list[dict]:
    """Earlier turns, then one final user turn holding the instruction. The latest user message is in it."""
    turns = chat_turns(conn, case_id)
    if turns and turns[-1]["role"] == "user":
        turns = turns[:-1]  # the latest user message is repeated inside the instruction, not twice
    return [*turns, {"role": "user", "content": instruction}]
