"""Documents we make ourselves (project report, prefill pack): encrypted into the vault like uploads."""

import hashlib
from uuid import uuid4

from app.db import jsonb
from app.docs import vault
from app.events import log_event
from app.i18n import bilingual


def store_generated(conn, case_id: str, data: bytes, doc_type: str, label: dict[str, str]) -> str:
    """Encrypt and insert a generated PDF (status OK). Identical bytes return the existing row's id."""
    digest = hashlib.sha256(data).hexdigest()
    existing = conn.execute(
        "select id from documents where case_id = %s and sha256 = %s", (case_id, digest)
    ).fetchone()
    if existing:
        return str(existing["id"])
    doc_id = str(uuid4())
    path = vault.path_for(case_id, doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(vault.encrypt(data))
    conn.execute(
        "insert into documents (id, case_id, doc_type, file_path, sha256, mime, check_status, check_result) "
        "values (%s, %s, %s, %s, %s, 'application/pdf', 'OK', %s)",
        (doc_id, case_id, doc_type, str(path), digest, jsonb({"generated": True, "reason": label})),
    )
    log_event(
        conn,
        case_id,
        "agent",
        "DOC_UPLOADED",
        bilingual(f"Made your {doc_type.replace('_', ' ')}", f"आपका {label['hi']} तैयार किया"),
        payload={"document_id": doc_id, "generated": True},
    )
    return doc_id
