"""Chat text is shown as plain text. The model sometimes writes Markdown; this removes the symbols so users see words."""

import re

_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),
    (re.compile(r"__(.+?)__", re.DOTALL), r"\1"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", re.DOTALL), r"\1"),
    (re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])", re.DOTALL), r"\1"),
    (re.compile(r"`([^`\n]+)`"), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE), ""),
    (re.compile(r"^\s*[-*]\s+", re.MULTILINE), "• "),
]


def plain_text(body: str) -> str:
    """Remove Markdown emphasis, code marks and heading marks; keep the words and the line breaks."""
    out = body or ""
    for pattern, repl in _PATTERNS:
        out = pattern.sub(repl, out)
    return out
