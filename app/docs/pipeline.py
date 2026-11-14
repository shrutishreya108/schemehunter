"""Upload pipeline (LLD §12.1): validate → strip EXIF → hash → encrypt → vision → mask → name match → status."""

MAX_BYTES = 10 * 1024 * 1024
ALLOWED_MIME = {"image/jpeg", "image/png", "application/pdf"}
VISION_TYPES = {"aadhaar", "passbook"}


def process_upload(case_id: str, data: bytes, mime: str, doc_type: str | None = None) -> dict:
    raise NotImplementedError("slice 5")


def strip_exif(data: bytes, mime: str) -> bytes:
    raise NotImplementedError("slice 5")


def decide_status(check: dict, expected_type: str | None, mismatch: bool) -> str:
    raise NotImplementedError("slice 5")
