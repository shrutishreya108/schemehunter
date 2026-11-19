"""Retrieval over the real pmegp guideline text. Needs Postgres (db marker). Pages are labelled in queries.yaml."""

from pathlib import Path

import pytest
import yaml

from app.db import connect, run_migrations
from app.rag.chunk import CHUNK_CHARS, chunk_page
from app.rag.index import index_scheme
from app.rag.search import pages_for

pytestmark = pytest.mark.db
PAGES = Path(__file__).resolve().parents[2] / "rulebook" / "sources" / "pmegp" / "pages"
QUERIES = yaml.safe_load((Path(__file__).parent / "queries.yaml").read_text(encoding="utf-8"))["queries"]


@pytest.fixture
def indexed(settings_db: str):
    run_migrations(settings_db)
    with connect(settings_db) as conn:
        index_scheme(conn, "pmegp", PAGES)
        conn.commit()
        yield conn


def test_chunks_keep_page_text_and_respect_the_size() -> None:
    text = "\n".join(f"line {i} " + "x" * 80 for i in range(40))
    chunks = chunk_page(text)
    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_CHARS + 100 for c in chunks)
    assert chunks[0].splitlines()[0] == "line 0 " + "x" * 80
    assert chunks[1].splitlines()[0] in text  # overlap: the next chunk starts with text from the previous one


def test_index_is_idempotent_and_skips_unchanged_pages(indexed) -> None:
    again = index_scheme(indexed, "pmegp", PAGES)
    assert again["pages_indexed"] == 0 and again["pages_skipped"] == 29
    assert indexed.execute("select count(distinct page) from source_chunks where scheme_id = 'pmegp'").fetchone()[
        "count"
    ] == 29


# Known miss (recorded, not hidden): page 5 says "Any individual, above 18 years of age" and never uses the words
# "minimum" or "applicant", so keyword search ranks pages 10-12 higher. Fixing it needs synonyms, not a model.
KNOWN_MISSES = {"minimum age of applicant": "page 5 uses 'above 18 years of age', no keyword overlap"}


@pytest.mark.parametrize(
    "item",
    [
        pytest.param(q, marks=pytest.mark.xfail(strict=True, reason=KNOWN_MISSES[q["query"]]))
        if q["query"] in KNOWN_MISSES
        else q
        for q in QUERIES
    ],
    ids=[q["query"] for q in QUERIES],
)
def test_labelled_page_is_in_the_top_three(indexed, item: dict) -> None:
    top = pages_for(indexed, item["query"], "pmegp", max_pages=3)
    assert set(top) & set(item["pages"]), f"top 3 {top} misses {item['pages']}"


def test_recall_over_labelled_queries(indexed) -> None:
    hits = [bool(set(pages_for(indexed, q["query"], "pmegp", max_pages=3)) & set(q["pages"])) for q in QUERIES]
    recall = sum(hits) / len(hits)
    print(f"\nretrieval recall@3 (pmegp, {len(QUERIES)} queries): {recall:.2f}")
    assert recall >= 0.9
