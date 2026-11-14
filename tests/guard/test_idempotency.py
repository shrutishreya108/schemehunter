from app.guard.actions import idempotency_key, new_short_code


def test_same_action_same_key() -> None:
    a = idempotency_key("send_email", "c1", "a1", "Dear Sir,\n\nPlease update  us.")
    b = idempotency_key("send_email", "c1", "a1", "  Dear Sir, Please update us. ")
    assert a == b and len(a) == 64


def test_any_difference_changes_key() -> None:
    base = idempotency_key("send_email", "c1", "a1", "hello")
    assert base != idempotency_key("send_chat", "c1", "a1", "hello")
    assert base != idempotency_key("send_email", "c2", "a1", "hello")
    assert base != idempotency_key("send_email", "c1", None, "hello")
    assert base != idempotency_key("send_email", "c1", "a1", "hello!")


def test_double_tap_dedupes() -> None:
    """Two approvals of the same draft produce one key, so the unique column drops the second insert."""
    keys = {idempotency_key("send_email", "c1", "a1", "Request for update") for _ in range(2)}
    assert len(keys) == 1


def test_short_code_unique_among_pending() -> None:
    taken = {f"{i:04d}" for i in range(9999)}  # only 9999 left
    assert new_short_code(taken) == "9999"
    code = new_short_code(set())
    assert len(code) == 4 and code.isdigit()
