"""Build the chunk index for one scheme from rulebook/sources/<scheme>/pages/NN.txt.

Idempotent: a page whose sha256 is unchanged is skipped; chunks of pages that no longer exist are deleted.
Called by sync_sources, so make seed and demo-reset keep it current.
"""

import hashlib
from pathlib import Path

import psycopg

from app.rag.chunk import chunk_page


def _page_files(pages_dir: Path) -> dict[int, Path]:
    if not pages_dir.is_dir():
        return {}
    return {int(p.stem): p for p in pages_dir.glob("*.txt") if p.stem.isdigit()}


def index_scheme(conn: psycopg.Connection, scheme_id: str, pages_dir: Path) -> dict[str, int]:
    files = _page_files(pages_dir)
    counts = {"pages_indexed": 0, "pages_skipped": 0, "pages_removed": 0, "chunks": 0}
    current = conn.execute(
        "select page, max(content_sha256) as sha from source_chunks where scheme_id = %s group by page",
        (scheme_id,),
    ).fetchall()
    stored = {r["page"]: r["sha"] for r in current}
    for page, path in sorted(files.items()):
        text = path.read_text(encoding="utf-8")
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if stored.get(page) == sha:
            counts["pages_skipped"] += 1
            continue
        conn.execute("delete from source_chunks where scheme_id = %s and page = %s", (scheme_id, page))
        pieces = chunk_page(text)
        for ord_, piece in enumerate(pieces, 1):
            conn.execute(
                "insert into source_chunks (scheme_id, page, ord, text, content_sha256) values (%s, %s, %s, %s, %s)",
                (scheme_id, page, ord_, piece, sha),
            )
        counts["pages_indexed"] += 1
        counts["chunks"] += len(pieces)
    gone = [p for p in stored if p not in files]
    if gone:
        conn.execute(
            "delete from source_chunks where scheme_id = %s and page = any(%s)", (scheme_id, gone)
        )
        counts["pages_removed"] = len(gone)
    return counts
