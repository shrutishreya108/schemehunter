"""/documents: upload page, POST /documents (pipeline, LLD §12) and GET /documents/{id}/file (decrypt in memory)."""

import logging
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from app.access import check_csrf, current_case_id, rate_limit
from app.brain.runner import deferral_reason, end_run, start_run
from app.costs import UsageMeter
from app.db import connect
from app.docs import vault
from app.docs.pipeline import DOC_TYPES, MAX_BYTES, VISION_TYPES, UploadError, process_upload
from app.docs.requirements import document_statuses
from app.routes.render import lang_for, render
from app.rules.loader import load_schemes

router = APIRouter()
log = logging.getLogger(__name__)
CHIP = {"OK": "ok", "RETAKE": "retake", "PROBLEM": "problem", "PENDING": "pending"}
ACTIVE_ONLY = "state not in ('CLOSED','MISSED','RETRY_NEXT_CYCLE','SANCTIONED')"


def case_language(conn, case_id: str) -> str | None:
    row = conn.execute("select language from cases where id = %s", (case_id,)).fetchone()
    return row["language"] if row else None


def document_rows(conn, case_id: str, lang: str) -> list[dict]:
    rows = conn.execute(
        "select id, doc_type, check_status, check_result, created_at from documents "
        "where case_id = %s order by created_at desc",
        (case_id,),
    ).fetchall()
    return [
        {
            "id": str(r["id"]),
            "doc_type": r["doc_type"] or "other",
            "chip": CHIP.get(r["check_status"], "pending"),
            "status": r["check_status"],
            "reason": ((r["check_result"] or {}).get("reason") or {}).get(lang) or "",
            "when": r["created_at"],
        }
        for r in rows
    ]


def needed_rows(conn, case_id: str) -> list[dict]:
    """For each active application, which papers are still missing."""
    schemes = {s["id"]: s for s in load_schemes()}
    ids = [
        r["scheme_id"]
        for r in conn.execute(
            f"select scheme_id from applications where case_id = %s and {ACTIVE_ONLY} order by created_at",
            (case_id,),
        )
    ]
    return [
        {"scheme": sid, "docs": document_statuses(conn, case_id, schemes[sid])}
        for sid in ids
        if sid in schemes
    ]


def page(request: Request, conn, case_id: str, error: dict | None = None):
    lang = lang_for(request, case_language(conn, case_id))
    return render(
        request,
        "documents.html",
        lang,
        case_id,
        docs=document_rows(conn, case_id, lang),
        needed=needed_rows(conn, case_id),
        doc_types=sorted(DOC_TYPES),
        error=error[lang] if error else None,
    )


@router.get("/documents")
def documents_page(request: Request, case_id: Annotated[str, Depends(current_case_id)]):
    with connect() as conn:
        return page(request, conn, case_id)


@router.post("/documents")
async def upload_document(
    request: Request,
    case_id: Annotated[str, Depends(current_case_id)],
    file: Annotated[UploadFile, File()],
    doc_type: Annotated[str, Form()] = "",
    csrf: Annotated[str | None, Form()] = None,
):
    check_csrf(case_id, request.headers.get("x-csrf-token") or csrf)
    data = await file.read(MAX_BYTES + 1)  # never buffer more than one byte over the cap
    mime = file.content_type or ""
    error = None
    with connect() as conn:
        if not rate_limit(conn, f"upload:{case_id}", 20, 60):
            error = {"en": "Too many uploads. Wait a minute.", "hi": "बहुत सारे अपलोड। एक मिनट रुकिए।"}
        else:
            deferred = doc_type in VISION_TYPES and deferral_reason(conn, case_id, False) is not None
            meter = UsageMeter()
            run_id = (
                None if deferred else start_run(conn, case_id, f"check {doc_type or 'document'}", "upload")
            )
            status = "DONE"
            try:
                process_upload(conn, case_id, data, mime, doc_type or None, meter=meter, check=not deferred)
            except UploadError as exc:
                error, status = {"en": exc.en, "hi": exc.hi}, "ERROR"
            except Exception:
                log.exception("document check failed")
                status = "ERROR"
                error = {
                    "en": "I could not check that file. Please try again.",
                    "hi": "मैं इस फ़ाइल की जाँच नहीं कर पाई। कृपया फिर कोशिश करें।",
                }
            finally:
                if run_id:
                    end_run(conn, run_id, 1, status, meter)
    if error is None and not request.headers.get("hx-request"):
        return RedirectResponse("/documents", status_code=303)
    with connect() as conn:
        return page(request, conn, case_id, error)


@router.get("/documents/{document_id}/file")
def document_file(document_id: str, case_id: Annotated[str, Depends(current_case_id)]):
    try:
        UUID(document_id)
    except ValueError:
        raise HTTPException(404, "not found") from None
    with connect() as conn:
        row = conn.execute(
            "select mime, file_path from documents where id = %s and case_id = %s", (document_id, case_id)
        ).fetchone()
    if not row:
        raise HTTPException(404, "not found")
    body = vault.decrypt(Path(row["file_path"]).read_bytes())  # in memory only, never written back
    return Response(
        body,
        media_type=row["mime"],
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )
