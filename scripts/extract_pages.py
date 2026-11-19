"""Extract per-page text from rulebook/sources/<scheme>/guidelines.pdf into pages/NN.txt (make extract-pages).

Uses pdftotext -layout when it is on PATH (better for tables), otherwise pypdf (dev extra). Never invents text:
an empty or near-empty page gets an empty file and is reported (likely scanned; OCR is not supported).
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "rulebook" / "sources"
SCANNED_BELOW = 200  # characters per page


def page_texts_pdftotext(pdf: Path) -> list[str]:
    out = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), "-"], capture_output=True, check=True
    ).stdout.decode("utf-8")
    pages = out.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]  # pdftotext ends every page with a form feed
    return pages


def page_texts_pypdf(pdf: Path) -> list[str]:
    from pypdf import PdfReader

    return [p.extract_text(extraction_mode="layout") or "" for p in PdfReader(str(pdf)).pages]


def collapse(text: str) -> str:
    lines = (re.sub(r"[ \t ]+", " ", line).strip() for line in text.splitlines())
    return "\n".join(line for line in lines if line) + "\n"


def extract(pdf: Path) -> None:
    scheme = pdf.parent.name
    texts = page_texts_pdftotext(pdf) if shutil.which("pdftotext") else page_texts_pypdf(pdf)
    out_dir = pdf.parent / "pages"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("[0-9][0-9]*.txt"):
        old.unlink()
    short = []
    for number, raw in enumerate(texts, start=1):
        body = collapse(raw)
        if len(body.strip()) < SCANNED_BELOW:
            short.append(number)
            body = ""  # never fabricate text for a page we could not read
        (out_dir / f"{number:02d}.txt").write_text(body, encoding="utf-8")
    print(f"{scheme}: {len(texts)} pages, {len(texts) - len(short)} with text, {len(short)} under {SCANNED_BELOW} chars")
    if short:
        print(f"  likely scanned, written empty: pages {', '.join(f'{n:02d}' for n in short)}")


def main() -> int:
    pdfs = sorted(SOURCES.glob("*/guidelines.pdf"))
    if not pdfs:
        print("no guidelines.pdf found under rulebook/sources/*/ (add the official PDFs first)")
        return 0
    for pdf in pdfs:
        extract(pdf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
