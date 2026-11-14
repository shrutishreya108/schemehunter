"""Jinja HTML → WeasyPrint bytes; Noto Sans Devanagari; English fallback flag. Slice 9."""


def render(template: str, context: dict, lang: str = "hi") -> tuple[bytes, bool]:
    """Returns (pdf_bytes, fell_back_to_english)."""
    raise NotImplementedError("slice 9")
