"""/approvals and /webhooks/telegram over HTTP: "YES twice → one send", binding, unknown chats, duplicates."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.access import bind_token, csrf_token, demo_link_token
from app.config import get_settings
from app.guard import actions
from app.mouth.telegram import TelegramChannel

pytestmark = pytest.mark.db
SECRET = "hook-secret"
BODY = "I am writing on behalf of Ravi Kumar about application PMEGP/KA/2026/04471. Could you please share the current status?"


@pytest.fixture
def web(seeded, settings_db, monkeypatch):
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:tok")
    monkeypatch.setenv("TELEGRAM_BOT_USERNAME", "SchemeHunterBot")
    get_settings.cache_clear()
    sent: list[tuple[str, str]] = []

    def fake_send(self, to, text, attachments=()):
        sent.append((to, text))
        return str(len(sent))

    monkeypatch.setattr(TelegramChannel, "send", fake_send)
    from app.main import app

    clients = []

    def login(persona: str) -> TestClient:
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        assert c.get(f"/c/{demo_link_token(persona)}", follow_redirects=False).status_code == 303
        return c

    login.sent = sent
    login.hook = TestClient(app)
    yield login
    for c in clients:
        c.__exit__(None, None, None)
    get_settings.cache_clear()


def update(n: int, chat: int, text: str) -> dict:
    return {"update_id": n, "message": {"text": text, "chat": {"id": chat, "type": "private"}}}


def post_hook(web, payload: dict, secret: str | None = SECRET):
    headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}
    return web.hook.post("/webhooks/telegram", json=payload, headers=headers)


def link_chat(conn, case_id: str, chat: int) -> None:
    conn.execute("update cases set telegram_chat_id = %s where id = %s", (str(chat), case_id))
    conn.commit()


def make_action(conn, seeded) -> tuple[str, str]:
    app_id = str(
        conn.execute("select id from applications where case_id = %s", (seeded["ravi"],)).fetchone()["id"]
    )
    conn.execute("update cases set email = 'ravi@example.test' where id = %s", (seeded["ravi"],))
    payload = {"to": "bank@example.test", "subject": "PMEGP/KA/2026/04471", "body": BODY, "audience": "bank"}
    row = actions.create_pending(conn, seeded["ravi"], "send_email", payload, app_id)
    conn.commit()
    return str(row["id"]), row["short_code"]


# ---------------------------------------------------------------- the webhook


def test_webhook_rejects_a_bad_or_missing_secret(web) -> None:
    assert post_hook(web, update(1, 5, "hi"), secret="nope").status_code == 403
    assert post_hook(web, update(1, 5, "hi"), secret=None).status_code == 403


def test_yes_with_code_sends_the_email_once_even_if_repeated(web, seeded, conn, smtp_settings) -> None:
    link_chat(conn, seeded["ravi"], 900)
    action_id, code = make_action(conn, seeded)
    assert post_hook(web, update(1, 900, f"YES {code}")).status_code == 200
    assert (
        post_hook(web, update(2, 900, f"YES {code}")).json().get("stale")
    )  # a second YES: nothing left to approve
    assert (
        post_hook(web, update(1, 900, f"YES {code}")).json().get("duplicate")
    )  # Telegram redelivering update 1
    assert len(smtp_settings.handler.messages) == 1
    sent = smtp_settings.handler.messages[0]
    assert "ravi@example.test" in sent.rcpt_tos  # the user is copied
    assert (
        conn.execute("select status from pending_actions where id = %s", (action_id,)).fetchone()["status"]
        == "EXECUTED"
    )


def test_no_with_code_declines_and_sends_nothing(web, seeded, conn, smtp_settings) -> None:
    link_chat(conn, seeded["ravi"], 900)
    action_id, code = make_action(conn, seeded)
    post_hook(web, update(1, 900, f"NO {code}"))
    assert (
        conn.execute("select status from pending_actions where id = %s", (action_id,)).fetchone()["status"]
        == "REJECTED"
    )
    assert smtp_settings.handler.messages == []


def test_a_code_from_another_persons_chat_does_nothing(web, seeded, conn, smtp_settings) -> None:
    link_chat(conn, seeded["ravi"], 900)
    link_chat(conn, seeded["meena"], 901)
    action_id, code = make_action(conn, seeded)
    assert (
        post_hook(web, update(1, 901, f"YES {code}")).json().get("stale")
    )  # Meena can't approve Ravi's draft
    assert (
        conn.execute("select status from pending_actions where id = %s", (action_id,)).fetchone()["status"]
        == "PENDING"
    )
    assert smtp_settings.handler.messages == []


def test_a_wrong_code_does_nothing(web, seeded, conn, smtp_settings) -> None:
    link_chat(conn, seeded["ravi"], 900)
    action_id, code = make_action(conn, seeded)
    wrong = f"{(int(code) + 1) % 10000:04d}"
    assert post_hook(web, update(1, 900, f"YES {wrong}")).json().get("stale")
    assert (
        conn.execute("select status from pending_actions where id = %s", (action_id,)).fetchone()["status"]
        == "PENDING"
    )


def test_unknown_chat_is_told_to_open_the_link_first(web, seeded, conn) -> None:
    post_hook(web, update(1, 777, "hello"))
    assert web.sent and web.sent[0][0] == "777" and "personal Scheme Hunter link" in web.sent[0][1]
    assert (
        conn.execute(
            "select count(*) as n from jobs where type = 'brain_run' and payload->>'trigger' = 'telegram'"
        ).fetchone()["n"]
        == 0
    )


def test_start_binds_the_chat_to_the_case(web, seeded, conn) -> None:
    post_hook(web, update(1, 321, f"/start {bind_token(seeded['meena'])}"))
    assert (
        conn.execute("select telegram_chat_id from cases where id = %s", (seeded["meena"],)).fetchone()[
            "telegram_chat_id"
        ]
        == "321"
    )
    assert any("Connected" in t or "जुड़" in t for _, t in web.sent)


def test_start_with_a_forged_token_binds_nothing(web, seeded, conn) -> None:
    post_hook(web, update(1, 321, f"/start {bind_token(seeded['meena'])[:-2]}zz"))
    assert (
        conn.execute("select count(*) as n from cases where telegram_chat_id is not null").fetchone()["n"]
        == 0
    )
    assert web.sent and "personal Scheme Hunter link" in web.sent[0][1]


def test_rebinding_a_chat_moves_it_to_the_new_case(web, seeded, conn) -> None:
    post_hook(web, update(1, 321, f"/start {bind_token(seeded['meena'])}"))
    post_hook(web, update(2, 321, f"/start {bind_token(seeded['ravi'])}"))
    rows = {r["id"]: r["telegram_chat_id"] for r in conn.execute("select id, telegram_chat_id from cases")}
    assert rows[UUID(seeded["ravi"])] == "321" and rows[UUID(seeded["meena"])] is None


def test_forwarded_sms_is_stored_and_queues_a_brain_run(web, seeded, conn) -> None:
    link_chat(conn, seeded["ravi"], 900)
    post_hook(web, update(1, 900, "Your application 04471 has been forwarded to Bank for appraisal."))
    msg = conn.execute(
        "select id, case_id, processed, direction, channel from messages where external_id = 'tg:1'"
    ).fetchone()
    assert str(msg["case_id"]) == seeded["ravi"] and msg["direction"] == "in" and not msg["processed"]
    job = conn.execute(
        "select payload from jobs where type = 'brain_run' and payload->>'trigger' = 'telegram'"
    ).fetchone()
    assert str(msg["id"]) in job["payload"]["task"]
    assert "MESSAGE_RECEIVED" in [
        r["type"] for r in conn.execute("select type from events where case_id = %s", (seeded["ravi"],))
    ]


def test_non_text_updates_are_ignored(web, seeded) -> None:
    assert post_hook(
        web, {"update_id": 9, "message": {"chat": {"id": 1, "type": "private"}, "photo": []}}
    ).json() == {"ok": True}


# ---------------------------------------------------------------- /approvals page


def test_approvals_page_lists_drafts_with_code_and_buttons(web, seeded, conn) -> None:
    action_id, code = make_action(conn, seeded)
    ravi = web("ravi")
    page = ravi.get("/approvals").text
    assert BODY in page and f"YES {code}" in page and f"/approvals/{action_id}/approve" in page
    assert "t.me/SchemeHunterBot?start=" in page  # not linked to Telegram yet: the link is offered


def test_approve_button_sends_and_redirects(web, seeded, conn, smtp_settings) -> None:
    action_id, _ = make_action(conn, seeded)
    ravi = web("ravi")
    r = ravi.post(
        f"/approvals/{action_id}/approve", data={"csrf": csrf_token(seeded["ravi"])}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"] == "/approvals"
    assert len(smtp_settings.handler.messages) == 1
    again = ravi.post(
        f"/approvals/{action_id}/approve", data={"csrf": csrf_token(seeded["ravi"])}, follow_redirects=False
    )
    assert again.status_code == 303 and len(smtp_settings.handler.messages) == 1  # double tap: still one


def test_decline_button(web, seeded, conn, smtp_settings) -> None:
    action_id, _ = make_action(conn, seeded)
    web("ravi").post(f"/approvals/{action_id}/reject", data={"csrf": csrf_token(seeded["ravi"])})
    assert (
        conn.execute("select status from pending_actions where id = %s", (action_id,)).fetchone()["status"]
        == "REJECTED"
    )
    assert smtp_settings.handler.messages == []


def test_approve_needs_csrf_a_session_and_ownership(web, seeded, conn, smtp_settings) -> None:
    action_id, _ = make_action(conn, seeded)
    ravi, meena = web("ravi"), web("meena")
    assert ravi.post(f"/approvals/{action_id}/approve").status_code == 403
    assert ravi.post(f"/approvals/{action_id}/approve", data={"csrf": "wrong"}).status_code == 403
    assert (
        meena.post(f"/approvals/{action_id}/approve", data={"csrf": csrf_token(seeded["meena"])}).status_code
        == 404
    )
    assert (
        meena.post("/approvals/not-a-uuid/approve", data={"csrf": csrf_token(seeded["meena"])}).status_code
        == 404
    )
    from app.main import app as fastapi_app

    with TestClient(fastapi_app) as anon:
        assert anon.post(f"/approvals/{action_id}/approve").status_code == 401
    assert smtp_settings.handler.messages == []
    assert BODY not in meena.get("/approvals").text  # and she can't read his draft either


def test_curator_only_failures_are_hidden_from_the_user_page(web, seeded, conn) -> None:
    action_id, _ = make_action(conn, seeded)
    conn.execute(
        "update pending_actions set status = 'FAILED', error = 'guard: hits=[...]' where id = %s",
        (action_id,),
    )
    conn.commit()
    assert BODY not in web("ravi").get("/approvals").text


# ---------------------------------------------------------------- user buttons on an application


def test_user_presses_submit_with_reference_number(web, seeded, conn) -> None:
    from app.tracker.store import create_application

    app = create_application(conn, seeded["meena"], "pmegp")
    conn.execute("update applications set state = 'AWAITING_HUMAN_SUBMIT' where id = %s", (app.id,))
    conn.commit()
    meena = web("meena")
    assert 'name="ref_number"' in meena.get(f"/applications/{app.id}").text
    bad = meena.post(
        f"/applications/{app.id}/submitted", data={"csrf": csrf_token(seeded["meena"]), "ref_number": ""}
    )
    assert bad.status_code in (409, 422)  # no reference number: the tracker's guard says no
    ok = meena.post(
        f"/applications/{app.id}/submitted",
        data={"csrf": csrf_token(seeded["meena"]), "ref_number": "PMEGP/KA/2026/09999"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    row = conn.execute("select state, ref_number from applications where id = %s", (app.id,)).fetchone()
    assert (row["state"], row["ref_number"]) == ("SUBMITTED", "PMEGP/KA/2026/09999")
    assert "status_check" in [
        r["type"] for r in conn.execute("select type from jobs where application_id = %s", (app.id,))
    ]


def test_user_cannot_press_a_button_out_of_order(web, seeded, conn) -> None:
    from app.tracker.store import create_application

    app = create_application(conn, seeded["meena"], "pmegp")  # ELIGIBLE
    conn.commit()
    r = web("meena").post(
        f"/applications/{app.id}/submitted", data={"csrf": csrf_token(seeded["meena"]), "ref_number": "X1"}
    )
    assert r.status_code == 409
    assert (
        conn.execute("select state from applications where id = %s", (app.id,)).fetchone()["state"]
        == "ELIGIBLE"
    )
