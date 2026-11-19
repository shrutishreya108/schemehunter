"""Slice 5: 20 specimen fixtures scored through the real pipeline with FakeClaude vision (no API, no real IDs)."""

import base64
import io
import json
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml
from PIL import Image

from app.brain.tools import dispatch
from app.brain.tools.base import Ctx
from app.config import get_settings
from app.costs import UsageMeter
from app.docs import vault
from app.docs.pipeline import MAX_EDGE_PX, UploadError, process_upload, strip_exif
from app.docs.requirements import document_statuses, required_docs_ok, resolve_documents
from app.rules.loader import load_schemes

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).parent / "fixtures"
LABELS = yaml.safe_load((FIXTURES / "labels.yaml").read_text(encoding="utf-8"))
TWELVE = re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b")
UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)  # ids can look like 4-4-4 digits


def has_id_number(text: str) -> bool:
    """A 12-digit ID-like number, ignoring UUIDs (random ids can look like one by chance)."""
    return bool(TWELVE.search(UUID.sub("", text)))


@pytest.fixture
def vault_env(settings_db, tmp_path, monkeypatch):
    monkeypatch.setenv("VAULT_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("VAULT_DIR", str(tmp_path / "vault"))
    get_settings.cache_clear()
    yield tmp_path / "vault"
    get_settings.cache_clear()


def queue_vision(fake_claude, vision: dict) -> None:
    fake_claude.add({"vision": [{"content": [{"tool": "document_check_result", "input": vision}]}]})


def upload(conn, case_id, label, fake_claude, meter=None):
    data = (FIXTURES / label["file"]).read_bytes()
    if label["claimed"] in ("aadhaar", "passbook"):
        queue_vision(fake_claude, label["vision"])
    return process_upload(
        conn, case_id, data, "image/jpeg", label["claimed"], meter=meter or UsageMeter(), vision_consent=True
    )


def test_there_are_20_labelled_fixtures() -> None:
    assert len(LABELS) == 20 and all((FIXTURES / lab["file"]).exists() for lab in LABELS)


def test_fixtures_are_scored_and_rates_reported(seeded, conn, vault_env, fake_claude, capsys) -> None:
    case_id = seeded["meena"]
    got = Counter()
    wrong = []
    for label in LABELS:
        result = upload(conn, case_id, label, fake_claude)
        got[(label["expect"], result["status"])] += 1
        if result["status"] != label["expect"]:
            wrong.append((label["file"], label["expect"], result["status"]))
    total = len(LABELS)
    correct = sum(n for (exp, st), n in got.items() if exp == st)
    retake = sum(n for (exp, st), n in got.items() if st == "RETAKE")
    problem = sum(n for (exp, st), n in got.items() if st == "PROBLEM")
    print(
        f"documents: {correct}/{total} correct; RETAKE {retake}, PROBLEM {problem}, OK {total - retake - problem}"
    )
    assert not wrong, wrong
    assert (retake, problem) == (6, 4)
    # vision ran only for aadhaar/passbook (17 of 20); photo, quotation, caste cert did not
    assert len(fake_claude.calls_for("vision")) == sum(
        lab["claimed"] in ("aadhaar", "passbook") for lab in LABELS
    )


def test_no_12_digit_number_reaches_the_db_or_the_case_file(seeded, conn, vault_env, fake_claude) -> None:
    from app.brain.casefile import build_case_file

    case_id = seeded["meena"]
    for label in LABELS:
        upload(conn, case_id, label, fake_claude)
    leaked = []
    for table, col in [
        ("documents", "check_result"),
        ("documents", "extracted"),
        ("events", "summary"),
        ("events", "payload"),
        ("messages", "body"),
        ("profile_answers", "value"),
        ("jobs", "payload"),
        ("brain_runs", "error"),
    ]:
        for row in conn.execute(f"select {col}::text as v from {table}"):
            if row["v"] and has_id_number(row["v"]):
                leaked.append((table, col, row["v"][:80]))
    assert not leaked, leaked
    assert not has_id_number(json.dumps(build_case_file(case_id, conn), default=str))
    masked = conn.execute(
        "select extracted from documents where case_id = %s and extracted::text like '%%XXXX XXXX 9012%%'",
        (case_id,),
    ).fetchall()
    assert len(masked) >= 3  # the three phrasings of the raw 12-digit id were all masked, not dropped


def test_duplicate_upload_returns_existing_row_and_calls_nothing(
    seeded, conn, vault_env, fake_claude
) -> None:
    label = LABELS[0]
    first = upload(conn, seeded["meena"], label, fake_claude)
    calls = len(fake_claude.calls)
    again = process_upload(
        conn, seeded["meena"], (FIXTURES / label["file"]).read_bytes(), "image/jpeg", "aadhaar"
    )
    assert (
        again["duplicate"]
        and again["document_id"] == first["document_id"]
        and len(fake_claude.calls) == calls
    )
    assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 1


def test_passbook_name_mismatch_is_a_problem_with_a_reason(seeded, conn, vault_env, fake_claude) -> None:
    label = next(lab for lab in LABELS if lab["file"] == "passbook_name_mismatch.jpg")
    out = upload(conn, seeded["meena"], label, fake_claude)
    assert out["status"] == "PROBLEM" and "name" in out["reason"]["en"] and out["reason"]["hi"]


def test_vision_call_is_downscaled_forced_schema_and_metered(seeded, conn, vault_env, fake_claude) -> None:
    big = io.BytesIO()
    Image.new("RGB", (3200, 2000), (240, 240, 230)).save(big, format="JPEG")
    queue_vision(fake_claude, LABELS[3]["vision"])
    meter = UsageMeter()
    process_upload(
        conn, seeded["meena"], big.getvalue(), "image/jpeg", "aadhaar", meter=meter, vision_consent=True
    )
    call = fake_claude.calls_for("vision")[0]
    assert call["max_tokens"] == 1200 and call["model"] == get_settings().MODEL_REASON
    assert call["tool_choice"] == {"type": "tool", "name": "document_check_result"}
    image = next(p for p in call["messages"][0]["content"] if p["type"] == "image")
    sent = Image.open(io.BytesIO(base64.b64decode(image["source"]["data"])))
    assert max(sent.size) <= MAX_EDGE_PX and max(sent.size) > 1000
    assert meter.calls == 1 and meter.cost > 0  # metered like every other call
    assert "last four digits" in call["system"][0]["text"] or "last 4" in call["system"][0]["text"].lower()


def test_photo_quotation_and_pdf_aadhaar_never_call_the_model(seeded, conn, vault_env, fake_claude) -> None:
    for name in ("photo_clear.jpg", "quotation_clear.jpg"):
        label = next(lab for lab in LABELS if lab["file"] == name)
        assert upload(conn, seeded["meena"], label, fake_claude)["status"] == "OK"
    pdf = b"%PDF-1.4\n" + b"x" * 200
    out = process_upload(conn, seeded["meena"], pdf, "application/pdf", "aadhaar")
    assert out["status"] == "RETAKE"  # an unchecked PDF is never OK for aadhaar/passbook
    assert fake_claude.calls_for("vision") == []


@pytest.mark.parametrize(
    ("data", "mime", "fragment"),
    [
        (b"x" * (10 * 1024 * 1024 + 1), "image/jpeg", "10 MB"),
        (b"GIF89a....", "image/gif", "JPEG, PNG or PDF"),
        (b"not a pdf", "application/pdf", "damaged"),
        (b"", "image/png", "10 MB"),
    ],
)
def test_rejected_uploads(seeded, conn, vault_env, data, mime, fragment) -> None:
    with pytest.raises(UploadError, match=fragment):
        process_upload(conn, seeded["meena"], data, mime, "aadhaar", vision_consent=True)
    assert conn.execute("select count(*) as n from documents").fetchone()["n"] == 0


def test_corrupt_image_is_rejected_cleanly(seeded, conn, vault_env) -> None:
    with pytest.raises(UploadError):
        process_upload(conn, seeded["meena"], b"\xff\xd8\xff not really a jpeg", "image/jpeg", "photo")


def test_exif_is_stripped_and_vault_holds_only_ciphertext(seeded, conn, vault_env, fake_claude) -> None:
    img = Image.new("RGB", (64, 64), (10, 200, 10))
    exif = Image.Exif()
    exif[0x010F] = "SPECIMEN-CAMERA"  # Make
    exif[0x0132] = "2026:11:14 10:00:00"  # DateTime
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    assert b"SPECIMEN-CAMERA" in buf.getvalue()
    assert b"SPECIMEN-CAMERA" not in strip_exif(buf.getvalue(), "image/jpeg")
    out = process_upload(conn, seeded["meena"], buf.getvalue(), "image/jpeg", "photo")
    blob = next(vault_env.rglob("*.bin")).read_bytes()
    assert not blob.startswith(b"\xff\xd8") and b"JFIF" not in blob and b"SPECIMEN-CAMERA" not in blob
    stored = vault.decrypt(blob)
    assert stored.startswith(b"\xff\xd8") and b"SPECIMEN-CAMERA" not in stored and out["status"] == "OK"


def test_decrypted_bytes_are_never_written_to_disk(seeded, conn, vault_env, fake_claude, monkeypatch) -> None:
    writes: list[bytes] = []
    real_write = Path.write_bytes

    def spy(self, data):
        writes.append(bytes(data))
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", spy)
    label = LABELS[0]
    plain = strip_exif((FIXTURES / label["file"]).read_bytes(), "image/jpeg")
    upload(conn, seeded["meena"], label, fake_claude)  # encrypt → store → decrypt in memory → vision
    assert len(writes) == 1 and writes[0] != plain and vault.decrypt(writes[0]) == plain
    assert not [p for p in vault_env.rglob("*") if p.is_file() and p.suffix != ".bin"]


# ---------------------------------------------------------------- requirements and tools


def test_conditional_documents_resolve_against_facts() -> None:
    pmegp = next(s for s in load_schemes() if s["id"] == "pmegp")
    assert resolve_documents(pmegp, {"category": "GEN", "project_cost": 500000}) == [
        "aadhaar",
        "photo",
        "passbook",
        "project_report",
        "quotation",
    ]
    assert "caste_cert" in resolve_documents(pmegp, {"category": "SC", "project_cost": 500000})
    assert "education_cert" in resolve_documents(pmegp, {"category": "GEN", "project_cost": 1200000})
    assert "caste_cert" not in resolve_documents(pmegp, {"category": "GEN", "project_cost": 1200000})
    mudra = next(s for s in load_schemes() if s["id"] == "mudra")
    assert resolve_documents(mudra, {}) == ["aadhaar", "passbook"]  # YAML has no documents yet → default


def test_docs_ready_only_when_every_user_paper_is_ok(seeded, conn, vault_env, fake_claude) -> None:
    from app.tracker.states import transition
    from app.tracker.store import DbTrackerStore, create_application, load_application

    case_id = seeded["meena"]  # OBC → caste certificate needed, cost 5 lakh → no education certificate
    app = create_application(conn, case_id, "pmegp")
    transition(app, "DOCS_PENDING", "agent", "", DbTrackerStore(conn))
    assert not required_docs_ok(conn, case_id, "pmegp")
    need = {
        d["doc_type"]: d
        for d in document_statuses(conn, case_id, next(s for s in load_schemes() if s["id"] == "pmegp"))
    }
    assert set(need) == {"aadhaar", "photo", "passbook", "project_report", "quotation", "caste_cert"}
    by_file = {lab["file"]: lab for lab in LABELS}
    for f in ("aadhaar_clear_4.jpg", "passbook_clear_1.jpg", "photo_clear.jpg", "quotation_clear.jpg"):
        upload(conn, case_id, by_file[f], fake_claude)
    assert not required_docs_ok(conn, case_id, "pmegp")  # caste certificate still missing
    upload(conn, case_id, by_file["caste_cert_clear.jpg"], fake_claude)
    assert required_docs_ok(conn, case_id, "pmegp")  # project_report is generated later, so not required here
    transition(load_application(conn, app.id), "DOCS_READY", "agent", "all papers OK", DbTrackerStore(conn))


def test_a_retake_blocks_docs_ready(seeded, conn, vault_env, fake_claude) -> None:
    by_file = {lab["file"]: lab for lab in LABELS}
    upload(conn, seeded["meena"], by_file["aadhaar_blurry.jpg"], fake_claude)
    statuses = document_statuses(conn, seeded["meena"], next(s for s in load_schemes() if s["id"] == "pmegp"))
    assert next(d for d in statuses if d["doc_type"] == "aadhaar")["status"] == "RETAKE"
    assert not required_docs_ok(conn, seeded["meena"], "pmegp")


def tool_ctx(conn, case_id: str, client) -> Ctx:
    return Ctx(
        case_id=case_id, conn=conn, schemes=load_schemes(), state_code="KA", language="en", client=client
    )


def test_tools_list_and_check(seeded, conn, vault_env, fake_claude) -> None:
    case_id = seeded["meena"]
    ctx = tool_ctx(conn, case_id, fake_claude)
    listed = dispatch("list_required_documents", {"scheme_id": "pmegp"}, ctx)
    assert {"doc_type": "aadhaar", "have": False, "status": None} in listed["documents"]
    out = process_upload(
        conn,
        case_id,
        (FIXTURES / "aadhaar_clear_1.jpg").read_bytes(),
        "image/jpeg",
        "aadhaar",
        check=False,
        vision_consent=True,
    )
    assert out["status"] == "PENDING"  # stored, check deferred (e.g. over budget)
    queue_vision(fake_claude, LABELS[0]["vision"])
    checked = dispatch("check_document", {"document_id": out["document_id"]}, ctx)
    assert checked["status"] == "OK" and ctx.meter.calls == 1
    assert "1234" not in json.dumps(checked) and "XXXX XXXX 9012" in json.dumps(checked)
    assert (
        "no such document"
        in dispatch("check_document", {"document_id": "00000000-0000-0000-0000-000000000000"}, ctx)["error"]
    )
    assert "document_id" in dispatch("check_document", {"document_id": "nope"}, ctx)["error"]
    assert "unknown scheme_id" in dispatch("list_required_documents", {"scheme_id": "x"}, ctx)["error"]


def test_documents_of_one_case_are_not_checkable_from_another(seeded, conn, vault_env, fake_claude) -> None:
    out = process_upload(
        conn, seeded["ravi"], (FIXTURES / "photo_clear.jpg").read_bytes(), "image/jpeg", "photo"
    )
    ctx = tool_ctx(conn, seeded["meena"], fake_claude)
    assert "no such document" in dispatch("check_document", {"document_id": out["document_id"]}, ctx)["error"]
