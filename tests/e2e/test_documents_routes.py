"""/documents over HTTP: upload, the green/yellow/red list, decrypt-in-memory download, access control, budget."""

import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.access import SESSION_COOKIE, csrf_token, sign_session
from app.config import get_settings

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).parent.parent / "docs" / "fixtures"


@pytest.fixture
def web(seeded, settings_db, tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    get_settings.cache_clear()
    from app.main import app

    clients = []

    def login(persona: str) -> TestClient:
        c = TestClient(app)
        c.__enter__()
        clients.append(c)
        c.cookies.set(SESSION_COOKIE, sign_session(seeded[persona]))
        return c

    yield login
    for c in clients:
        c.__exit__(None, None, None)
    get_settings.cache_clear()


def post_file(client, case_id, name, doc_type, csrf=True, htmx=True):
    headers = {"X-CSRF-Token": csrf_token(case_id)} if csrf else {}
    if htmx:
        headers["HX-Request"] = "true"
    return client.post(
        "/documents",
        data={"doc_type": doc_type},
        files={"file": (name, (FIXTURES / name).read_bytes(), "image/jpeg")},
        headers=headers,
        follow_redirects=False,
    )


def vision(fake_claude, **over):
    out = {
        "doc_type_detected": "aadhaar",
        "readable": True,
        "quality_issues": [],
        "confidence": 0.9,
        "fields": {"name_latin": "Meena Kumari", "id_last4": "9012", "address_line": "ID 1234 5678 9012"},
    }
    fake_claude.add({"vision": [{"content": [{"tool": "document_check_result", "input": {**out, **over}}]}]})


def test_upload_shows_green_tick_and_masks(web, seeded, conn, fake_claude) -> None:
    meena = web("meena")
    vision(fake_claude)
    r = post_file(meena, seeded["meena"], "aadhaar_clear_1.jpg", "aadhaar")
    assert r.status_code == 200 and 'class="chip ok"' in r.text and "बिल्कुल ठीक है" in r.text
    assert "1234 5678 9012" not in r.text
    run = conn.execute(
        "select trigger, status, estimated_cost_usd from brain_runs where case_id = %s", (seeded["meena"],)
    ).fetchone()
    assert run["trigger"] == "upload" and run["status"] == "DONE" and float(run["estimated_cost_usd"]) > 0


def test_blurry_upload_asks_for_a_retake_with_a_reason(web, seeded, fake_claude) -> None:
    meena = web("meena")
    vision(fake_claude, quality_issues=["blur"])
    r = post_file(meena, seeded["meena"], "aadhaar_blurry.jpg", "aadhaar")
    assert 'class="chip retake"' in r.text and "धुंधली है" in r.text


def test_non_htmx_upload_redirects_back(web, seeded, fake_claude) -> None:
    meena = web("meena")
    vision(fake_claude)
    r = post_file(meena, seeded["meena"], "aadhaar_clear_2.jpg", "aadhaar", htmx=False)
    assert r.status_code == 303 and r.headers["location"] == "/documents"


def test_upload_needs_csrf_and_a_session(web, seeded, settings_db) -> None:
    meena = web("meena")
    assert post_file(meena, seeded["meena"], "photo_clear.jpg", "photo", csrf=False).status_code == 403
    from app.main import app

    with TestClient(app) as anon:
        r = anon.post("/documents", files={"file": ("a.jpg", b"x", "image/jpeg")})
        assert r.status_code == 401


def test_bad_upload_shows_a_friendly_error(web, seeded) -> None:
    meena = web("meena")
    r = meena.post(
        "/documents",
        data={"doc_type": "photo"},
        files={"file": ("a.gif", b"GIF89a", "image/gif")},
        headers={"X-CSRF-Token": csrf_token(seeded["meena"]), "HX-Request": "true"},
    )
    assert r.status_code == 200 and "JPEG, PNG या PDF" in r.text


def test_download_decrypts_in_memory_with_no_store(web, seeded, conn, fake_claude) -> None:
    meena = web("meena")
    post_file(meena, seeded["meena"], "photo_clear.jpg", "photo")
    doc_id = str(
        conn.execute("select id from documents where case_id = %s", (seeded["meena"],)).fetchone()["id"]
    )
    r = meena.get(f"/documents/{doc_id}/file")
    assert (
        r.status_code == 200
        and r.headers["cache-control"] == "no-store"
        and r.content.startswith(b"\xff\xd8")
    )
    assert r.headers["content-type"] == "image/jpeg" and r.headers["x-content-type-options"] == "nosniff"
    assert (
        r.content
        != Path(conn.execute("select file_path from documents").fetchone()["file_path"]).read_bytes()
    )


def test_other_cases_documents_are_404(web, seeded, conn, fake_claude) -> None:
    ravi, meena = web("ravi"), web("meena")
    post_file(ravi, seeded["ravi"], "photo_clear.jpg", "photo")
    doc_id = str(
        conn.execute("select id from documents where case_id = %s", (seeded["ravi"],)).fetchone()["id"]
    )
    assert meena.get(f"/documents/{doc_id}/file").status_code == 404
    assert meena.get("/documents/not-a-uuid/file").status_code == 404
    assert doc_id not in meena.get("/documents").text


def test_over_budget_stores_the_paper_but_defers_the_vision_check(
    web, seeded, conn, fake_claude, monkeypatch
) -> None:
    monkeypatch.setenv("DAILY_BUDGET_USD", "0")
    get_settings.cache_clear()
    meena = web("meena")
    r = post_file(meena, seeded["meena"], "aadhaar_clear_3.jpg", "aadhaar")
    assert r.status_code == 200 and 'class="chip pending"' in r.text
    assert fake_claude.calls_for("vision") == []
    assert conn.execute("select check_status from documents").fetchone()["check_status"] == "PENDING"


def test_documents_page_lists_needed_papers_for_active_applications(web, seeded, conn) -> None:
    ravi = web("ravi")
    page = ravi.get("/documents").text
    assert "PMEGP" in page and "आधार" in page and "चाहिए" in page
