"""Personal data and the AI (reliability milestone 1, section 4): what reaches the model, and when a photo is sent."""

import base64
import json
from pathlib import Path

import pytest

from app.brain.casefile import build_case_file, profile_for_model
from app.brain.tools import answer_question as qa
from app.brain.tools.base import Ctx
from app.docs.pipeline import process_upload
from app.rules.loader import load_schemes

pytestmark = pytest.mark.db
FIXTURES = Path(__file__).resolve().parents[1] / "docs" / "fixtures"


@pytest.fixture
def vault(settings_db, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("VAULT_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_the_case_file_has_no_name_address_or_birth_date(seeded, conn) -> None:
    text = json.dumps(build_case_file(seeded["meena"], conn), ensure_ascii=False, default=str)
    for secret in ("Meena Kumari", "1991-04-12", "dob", "address_line", "name_latin"):
        assert secret not in text, secret


def test_the_model_still_gets_what_the_rules_need(seeded, conn) -> None:
    profile = build_case_file(seeded["meena"], conn)["profile"]
    assert profile["category"] == "OBC" and profile["sector"] == "service"
    assert isinstance(profile["age_years"], int) and profile["age_years"] >= 18


def test_profile_for_model_drops_identifying_keys_even_if_stored() -> None:
    answers = [
        {"key": "name_latin", "value": "A B"},
        {"key": "dob", "value": "2000-01-01"},
        {"key": "address_line", "value": "1 Road"},
        {"key": "loan_amount", "value": 300000},
    ]
    out = profile_for_model(answers)
    assert out == {"loan_amount": 300000, "age_years": out["age_years"]}


def test_paper_sources_for_answers_carry_no_extracted_fields(seeded, conn, vault, monkeypatch) -> None:

    conn.execute(
        "insert into documents (case_id, doc_type, file_path, sha256, mime, check_status, extracted) "
        "values (%s, 'passbook', '/x', 'h1', 'image/jpeg', 'OK', %s)",
        (
            seeded["meena"],
            json.dumps({"name_latin": "Meena Kumari", "ifsc": "SBIN0001234", "bank_name": "SBI"}),
        ),
    )
    conn.commit()
    ctx = Ctx(case_id=seeded["meena"], conn=conn, schemes=load_schemes(), language="en")
    texts = " ".join(s["text"] for s in qa.paper_sources(ctx))
    assert "passbook" in texts and "OK" in texts
    for secret in ("Meena Kumari", "SBIN0001234", "SBI"):
        assert secret not in texts


def test_aadhaar_photo_is_not_sent_to_the_ai_without_permission(seeded, conn, vault, fake_claude) -> None:
    data = (FIXTURES / "aadhaar_clear_1.jpg").read_bytes()
    out = process_upload(conn, seeded["meena"], data, "image/jpeg", "aadhaar", vision_consent=False)
    assert fake_claude.calls_for("vision") == []  # nothing left the server
    assert out["status"] == "PENDING" and out.get("consent_needed") is True
    assert (
        conn.execute("select check_status from documents where id = %s", (out["document_id"],)).fetchone()[
            "check_status"
        ]
        == "PENDING"
    )


def test_aadhaar_photo_is_checked_only_with_permission_for_that_upload(
    seeded, conn, vault, fake_claude
) -> None:
    fake_claude.add(
        {
            "vision": [
                {
                    "content": [
                        {
                            "tool": "document_check_result",
                            "input": {
                                "doc_type_detected": "aadhaar",
                                "readable": True,
                                "quality_issues": [],
                                "confidence": 0.9,
                                "fields": {"name_latin": "Meena Kumari", "id_last4": "9012"},
                            },
                        }
                    ]
                }
            ]
        }
    )
    data = (FIXTURES / "aadhaar_clear_1.jpg").read_bytes()
    out = process_upload(conn, seeded["meena"], data, "image/jpeg", "aadhaar", vision_consent=True)
    assert len(fake_claude.calls_for("vision")) == 1 and out["status"] == "OK"


def test_the_check_tool_respects_a_missing_permission(seeded, conn, vault, fake_claude) -> None:
    from app.brain.tools import dispatch

    data = (FIXTURES / "aadhaar_clear_2.jpg").read_bytes()
    out = process_upload(conn, seeded["meena"], data, "image/jpeg", "aadhaar", vision_consent=False)
    ctx = Ctx(
        case_id=seeded["meena"],
        conn=conn,
        schemes=load_schemes(),
        state_code="KA",
        language="en",
        client=fake_claude,
    )
    res = dispatch("check_document", {"document_id": out["document_id"]}, ctx)
    assert res["status"] == "PENDING" and fake_claude.calls_for("vision") == []


def test_no_vision_call_for_other_papers(seeded, conn, vault, fake_claude) -> None:
    data = (FIXTURES / "photo_clear.jpg").read_bytes()
    out = process_upload(conn, seeded["meena"], data, "image/jpeg", "photo")
    assert out["status"] == "OK" and fake_claude.calls_for("vision") == []
