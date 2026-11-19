"""Full-text search over guideline chunks. Returns pages in rank order; no model is involved."""

import re

import psycopg

_SQL = (
    "select scheme_id, page, ord, text, ts_rank_cd(tsv, q) as rank "
    "from source_chunks, websearch_to_tsquery('english', %s) q "
    "where tsv @@ q and (%s::text[] is null or scheme_id = any(%s::text[])) "
    "order by rank desc, scheme_id, page, ord limit %s"
)


def search(
    conn: psycopg.Connection, query: str, scheme_ids: list[str] | None = None, k: int = 6
) -> list[dict]:
    """All words must match first; if nothing matches, any word may match (ranked by ts_rank_cd)."""
    if not query.strip():
        return []
    rows = conn.execute(_SQL, (query, scheme_ids, scheme_ids, k)).fetchall()
    if not rows:
        words = re.findall(r"\w+", query)
        if words:
            rows = conn.execute(_SQL, (" or ".join(words), scheme_ids, scheme_ids, k)).fetchall()
    return [dict(r) for r in rows]


def pages_for(conn: psycopg.Connection, query: str, scheme_id: str, max_pages: int = 4) -> list[int]:
    """Distinct pages of the best-ranked chunks, in rank order."""
    pages: list[int] = []
    for row in search(conn, query, [scheme_id], k=max_pages * 5):
        if row["page"] not in pages:
            pages.append(row["page"])
        if len(pages) >= max_pages:
            break
    return pages
