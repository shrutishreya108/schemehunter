"""Upload pipeline (LLD §12.1): validate → strip EXIF → hash → encrypt → vision → mask → name match → status.

Vision runs only for aadhaar and passbook (the two papers where a misread costs the user). Everything
the model returns is masked in code too, and only masked fields are stored.
"""

import base64
import hashlib
import io
from typing import Any
from uuid import uuid4

import psycopg
from PIL import Image, ImageOps

from app.brain import claude
from app.costs import UsageMeter
from app.db import jsonb
from app.docs import vault
from app.docs.masking import mask_fields, name_mismatch
from app.events import log_event
from app.i18n import bilingual

MAX_BYTES = 10 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "application/pdf"}
VISION_TYPES = {"aadhaar", "passbook"}
DOC_TYPES = {"aadhaar", "passbook", "photo", "quotation", "caste_cert", "income_proof", "other"}
MAX_EDGE_PX = 1568  # long edge sent to the model; caps image tokens
RETAKE_ISSUES = {"cut_off", "blur", "too_dark"}

DOCUMENT_CHECK_TOOL = {
    "name": "document_check_result",
    "description": "What this paper is, whether it is readable, and the fields on it.",
    "input_schema": {
        "type": "object",
        "properties": {
            "doc_type_detected": {
                "type": "string",
                "enum": [
                    "aadhaar",
                    "pan",
                    "passbook",
                    "caste_cert",
                    "photo",
                    "quotation",
                    "education_cert",
                    "other",
                ],
            },
            "readable": {"type": "boolean"},
            "quality_issues": {
                "type": "array",
                "items": {"type": "string", "enum": ["blur", "glare", "cut_off", "too_dark"]},
            },
            "fields": {
                "type": "object",
                "properties": {
                    "name_native": {"type": "string"},
                    "name_latin": {"type": "string"},
                    "dob": {"type": ["string", "null"]},
                    "address_line": {"type": "string"},
                    "district": {"type": "string"},
                    "id_last4": {"type": "string"},
                    "bank_name": {"type": "string"},
                    "ifsc": {"type": "string"},
                },
            },
            "confidence": {"type": "number"},
        },
        "required": ["doc_type_detected", "readable", "quality_issues", "fields", "confidence"],
    },
}


class UploadError(ValueError):
    """A rejected upload; the message is safe to show the user."""

    def __init__(self, en: str, hi: str):
        super().__init__(en)
        self.en, self.hi = en, hi


def validate_upload(data: bytes, mime: str) -> None:
    if mime not in ALLOWED_MIME:
        raise UploadError("Please upload a JPEG, PNG or PDF.", "कृपया JPEG, PNG या PDF भेजें।")
    if not data or len(data) > MAX_BYTES:
        raise UploadError("The file must be under 10 MB.", "फ़ाइल 10 MB से छोटी होनी चाहिए।")
    if mime == "application/pdf" and not data.startswith(b"%PDF"):
        raise UploadError("That PDF looks damaged.", "यह PDF ख़राब लग रही है।")


def strip_exif(data: bytes, mime: str) -> bytes:
    """Re-save the pixels only, dropping EXIF (GPS, camera, time). PDFs pass through unchanged."""
    if mime == "application/pdf":
        return data
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))  # bake in rotation before dropping it
        clean = Image.new(img.mode, img.size)
        clean.paste(img)
        out = io.BytesIO()
        clean.save(
            out,
            format="PNG" if mime == "image/png" else "JPEG",
            **({} if mime == "image/png" else {"quality": 92}),
        )
        return out.getvalue()
    except Exception as exc:  # unreadable image data
        raise UploadError("I couldn't open that image.", "यह फ़ोटो खुल नहीं पाई।") from exc


def downscale_for_vision(data: bytes, mime: str) -> tuple[bytes, str]:
    """Long edge ≤ MAX_EDGE_PX, as JPEG/PNG. PDFs aren't sent to vision (aadhaar/passbook must be photos)."""
    img = Image.open(io.BytesIO(data))
    img.load()
    if max(img.size) > MAX_EDGE_PX:
        img.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
    out = io.BytesIO()
    fmt = "PNG" if mime == "image/png" else "JPEG"
    (img.convert("RGB") if fmt == "JPEG" else img).save(out, format=fmt)
    return out.getvalue(), "image/png" if fmt == "PNG" else "image/jpeg"


def decide_status(check: dict, expected_type: str | None, mismatch: bool) -> str:
    """LLD §12.3: RETAKE if unreadable or cut off / blurred / too dark; PROBLEM on wrong type or name."""
    issues = set(check.get("quality_issues") or [])
    if not check.get("readable", True) or issues & RETAKE_ISSUES:
        return "RETAKE"
    detected = check.get("doc_type_detected")
    if expected_type and detected and detected != expected_type:
        return "PROBLEM"
    if mismatch:
        return "PROBLEM"
    return "OK"


def reason_for(status: str, check: dict, expected_type: str | None) -> dict[str, str]:
    """One line for the green/yellow/red chip, in both languages (code-generated, no model text)."""
    issues = set(check.get("quality_issues") or [])
    if status == "OK":
        return bilingual("Looks good.", "बिल्कुल ठीक है।")
    if status == "RETAKE":
        if "cut_off" in issues:
            return bilingual("The edges are cut off. Please take it again.", "किनारे कटे हैं। कृपया फिर से खींचिए।")
        if "blur" in issues:
            return bilingual("It's blurry. Please take it again.", "धुंधली है। कृपया फिर से खींचिए।")
        if "too_dark" in issues:
            return bilingual(
                "It's too dark. Please take it again in light.", "बहुत अँधेरा है। रोशनी में फिर से खींचिए।"
            )
        return bilingual("I couldn't read it. Please take it again.", "मैं पढ़ नहीं पाई। कृपया फिर से खींचिए।")
    detected = check.get("doc_type_detected") or "something else"
    if expected_type and detected != expected_type:
        return bilingual(
            f"This looks like {detected}, not {expected_type}.",
            f"यह {detected} जैसा लग रहा है, {expected_type} नहीं।",
        )
    return bilingual("The name doesn't match your other papers.", "नाम आपके दूसरे कागज़ों से नहीं मिल रहा।")


def known_names(conn: psycopg.Connection, case_id: str, exclude_doc_id: str | None = None) -> list[str]:
    """Latin names already on file: the profile name, plus those on other documents of this case."""
    names: list[str] = []
    row = conn.execute(
        "select value from profile_answers where case_id = %s and key = 'name_latin'", (case_id,)
    ).fetchone()
    if row and isinstance(row["value"], str):
        names.append(row["value"])
    for r in conn.execute(
        "select extracted from documents where case_id = %s and check_status = 'OK' and id <> coalesce(%s::uuid, gen_random_uuid())",
        (case_id, exclude_doc_id),
    ):
        n = ((r["extracted"] or {}).get("name_latin") or "").strip()
        if n:
            names.append(n)
    return names


def vision_check(data: bytes, mime: str, expected_type: str, meter: UsageMeter, client: Any = None) -> dict:
    """One forced-schema vision call (MODEL_REASON, max_tokens 1200). Returns the raw tool input."""
    from app.config import get_settings

    small, small_mime = downscale_for_vision(data, mime)
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": small_mime,
                        "data": base64.b64encode(small).decode(),
                    },
                },
                {"type": "text", "text": f"The user says this is their {expected_type}. Check it."},
            ],
        }
    ]
    return claude.forced_tool_call(
        DOCUMENT_CHECK_TOOL,
        [{"type": "text", "text": claude.prompt("document_check.md")}],
        messages,
        meter,
        purpose="vision",
        model=get_settings().MODEL_REASON,
        max_tokens=1200,
        client=client,
    )


def apply_check(
    conn: psycopg.Connection,
    case_id: str,
    doc_id: str,
    raw: dict,
    expected_type: str | None,
) -> dict:
    """Mask → name match → status → store. Raw model output never reaches the DB unmasked."""
    fields = mask_fields(dict(raw.get("fields") or {}))
    check = {
        "doc_type_detected": raw.get("doc_type_detected"),
        "readable": bool(raw.get("readable", True)),
        "quality_issues": list(raw.get("quality_issues") or []),
        "confidence": raw.get("confidence"),
    }
    mismatch = name_mismatch(known_names(conn, case_id, doc_id), fields)
    status = decide_status(check, expected_type, mismatch)
    reason = reason_for(status, check, expected_type)
    check["reason"] = reason
    conn.execute(
        "update documents set check_status = %s, check_result = %s, extracted = %s, "
        "doc_type = coalesce(doc_type, %s) where id = %s",
        (status, jsonb(check), jsonb(fields), check["doc_type_detected"], doc_id),
    )
    log_event(
        conn,
        case_id,
        "system",
        "DOC_CHECKED",
        bilingual(
            f"{expected_type or 'Document'} checked: {status}",
            f"{expected_type or 'कागज़'} जाँचा गया: {status}",
        ),
        payload={"document_id": doc_id, "status": status},
    )
    return {
        "document_id": doc_id,
        "status": status,
        "doc_type_detected": check["doc_type_detected"],
        "issues": check["quality_issues"],
        "reason": reason,
        "fields_masked": fields,
    }


def check_stored_document(
    conn: psycopg.Connection, case_id: str, doc_id: str, meter: UsageMeter, client: Any = None
) -> dict:
    """Run the check for a stored document (used by upload, and by the check_document tool)."""
    row = conn.execute(
        "select doc_type, mime, file_path from documents where id = %s and case_id = %s", (doc_id, case_id)
    ).fetchone()
    if not row:
        raise LookupError("no such document")
    expected = row["doc_type"]
    if expected in VISION_TYPES and row["mime"] == "application/pdf":
        # Aadhaar and passbook must be photos we can check; an unchecked PDF is never marked OK.
        raw = {"doc_type_detected": expected, "readable": False, "quality_issues": [], "fields": {}}
    elif expected in VISION_TYPES:
        from pathlib import Path

        data = vault.decrypt(Path(row["file_path"]).read_bytes())  # decrypted in memory only
        raw = vision_check(data, row["mime"], expected, meter, client)
    else:
        # Not worth a model call: basic readability only (an image that opens, or a PDF).
        raw = {"doc_type_detected": expected or "other", "readable": True, "quality_issues": [], "fields": {}}
    return apply_check(conn, case_id, doc_id, raw, expected if expected in VISION_TYPES else None)


def process_upload(
    conn: psycopg.Connection,
    case_id: str,
    data: bytes,
    mime: str,
    doc_type: str | None = None,
    meter: UsageMeter | None = None,
    client: Any = None,
    check: bool = True,
) -> dict:
    """Store one upload and (if `check`) check it. Duplicate upload returns the existing row."""
    validate_upload(data, mime)
    digest = hashlib.sha256(data).hexdigest()
    existing = conn.execute(
        "select id, doc_type, check_status, check_result from documents where case_id = %s and sha256 = %s",
        (case_id, digest),
    ).fetchone()
    if existing:
        return {
            "document_id": str(existing["id"]),
            "status": existing["check_status"],
            "duplicate": True,
            "reason": (existing["check_result"] or {}).get("reason"),
        }
    if doc_type is not None and doc_type not in DOC_TYPES:
        raise UploadError("I don't know that kind of paper.", "यह किस तरह का कागज़ है, मुझे समझ नहीं आया।")
    clean = strip_exif(data, mime)
    doc_id = str(uuid4())
    path = vault.path_for(case_id, doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(vault.encrypt(clean))
    conn.execute(
        "insert into documents (id, case_id, doc_type, file_path, sha256, mime) values (%s, %s, %s, %s, %s, %s)",
        (doc_id, case_id, doc_type, str(path), digest, mime),
    )
    log_event(
        conn,
        case_id,
        "user",
        "DOC_UPLOADED",
        bilingual(f"{doc_type or 'Document'} uploaded", f"{doc_type or 'कागज़'} अपलोड हुआ"),
        payload={"document_id": doc_id},
    )
    if not check:
        return {"document_id": doc_id, "status": "PENDING", "duplicate": False}
    result = check_stored_document(conn, case_id, doc_id, meter or UsageMeter(), client)
    return {**result, "duplicate": False}
