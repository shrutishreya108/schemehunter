"""/approvals and the user's own buttons over HTTP: approve once → send once, decline, ownership, curator-only rows."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.access import csrf_token, demo_link_token
from app.config import get_settings
from app.guard import actions

pytestmark = pytest.mark.db
BODY = "I am writing on behalf of Ravi Kumar about application PMEGP/KA/2026/04471. Could you please share the current status?"


@pytest.fixture
def web(seeded, settings_db):
    get_settings.cache_clear()
    from app.main import app

    clients = []

    def login(persona: str) -> TestClient:
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        assert c.get(f"/c/{demo_link_token(persona)}", follow_redirects=False).status_code == 303
        return c

    yield login
    for c in clients:
        c.__exit__(None, None, None)
    get_settings.cache_clear()


def make_action(conn, seeded) -> str:
    app_id = str(
        conn.execute("select id from applications where case_id = %s", (seeded["ravi"],)).fetchone()["id"]
    )
    conn.execute("update cases set email = 'ravi@example.test' where id = %s", (seeded["ravi"],))
    payload = {"to": "bank@example.test", "subject": "PMEGP/KA/2026/04471", "body": BODY, "audience": "bank"}
    row = actions.create_pending(conn, seeded["ravi"], "send_email", payload, app_id)
    conn.commit()
    return str(row["id"])


def test_approvals_page_lists_the_draft_with_buttons(web, seeded, conn) -> None:
    action_id = make_action(conn, seeded)
    page = web("ravi").get("/approvals").text
    assert (
        BODY in page
        and f"/approvals/{action_id}/approve" in page
        and f"/approvals/{action_id}/reject" in page
    )
    assert "telegram" not in page.lower()


def test_approve_sends_once_and_a_second_click_changes_nothing(web, seeded, conn, smtp_settings) -> None:
    action_id = make_action(conn, seeded)
    ravi = web("ravi")
    r = ravi.post(
        f"/approvals/{action_id}/approve", data={"csrf": csrf_token(seeded["ravi"])}, follow_redirects=False
    )
    assert r.status_code == 303 and r.headers["location"] == "/approvals"
    ravi.post(
        f"/approvals/{action_id}/approve", data={"csrf": csrf_token(seeded["ravi"])}, follow_redirects=False
    )
    assert len(smtp_settings.handler.messages) == 1
    sent = smtp_settings.handler.messages[0]
    assert "ravi@example.test" in sent.rcpt_tos and "bank@example.test" in sent.rcpt_tos  # user copied
    assert (
        conn.execute("select status from pending_actions where id = %s", (action_id,)).fetchone()["status"]
        == "EXECUTED"
    )


def test_decline_sends_nothing(web, seeded, conn, smtp_settings) -> None:
    action_id = make_action(conn, seeded)
    web("ravi").post(f"/approvals/{action_id}/reject", data={"csrf": csrf_token(seeded["ravi"])})
    actions.approve(
        conn, action_id, "web", case_id=seeded["ravi"]
    )  # approving after declining changes nothing
    assert (
        conn.execute("select status from pending_actions where id = %s", (action_id,)).fetchone()["status"]
        == "REJECTED"
    )
    assert smtp_settings.handler.messages == []


def test_approve_needs_csrf_a_session_and_ownership(web, seeded, conn, smtp_settings) -> None:
    from app.main import app

    action_id = make_action(conn, seeded)
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
    with TestClient(app) as anon:
        assert anon.post(f"/approvals/{action_id}/approve").status_code == 401
    assert smtp_settings.handler.messages == []
    assert BODY not in meena.get("/approvals").text  # and she can't read his draft either


def test_curator_only_failures_are_hidden_from_the_user_page(web, seeded, conn) -> None:
    action_id = make_action(conn, seeded)
    conn.execute(
        "update pending_actions set status = 'FAILED', error = 'guard: hits=[...]' where id = %s",
        (action_id,),
    )
    conn.commit()
    assert BODY not in web("ravi").get("/approvals").text


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


def test_application_ids_are_uuids(seeded) -> None:
    assert UUID(seeded["meena"])
