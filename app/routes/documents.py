"""POST /documents (upload pipeline, LLD §12) and GET /documents/{id}/file. Slice 5."""

from fastapi import APIRouter

router = APIRouter()


@router.post("/documents")
def upload_document() -> None:
    raise NotImplementedError("slice 5")


@router.get("/documents/{document_id}/file")
def document_file(document_id: str) -> None:
    raise NotImplementedError("slice 5")
