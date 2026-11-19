"""A web page is never evidence: verified rows must cite a pdf-kind document (static check + database check)."""

import pytest
import yaml

from app.rules.sources import SOURCES_DIR, sync_sources, verified_rule_ids


def _folders():
    return sorted(p for p in SOURCES_DIR.iterdir() if p.is_dir())


@pytest.mark.parametrize("folder", _folders(), ids=lambda p: p.name)
def test_verified_rows_cite_only_pdf_sources(folder) -> None:
    src = folder / "source.yaml"
    ev = folder / "evidence.yaml"
    if not ev.exists():
        return
    kinds = {d["id"]: d.get("source_kind", "pdf") for d in (yaml.safe_load(src.read_text(encoding="utf-8")) or {}).get("documents") or []} if src.exists() else {}
    rows = (yaml.safe_load(ev.read_text(encoding="utf-8")) or {}).get("evidence") or []
    for row in rows:
        if row.get("status") == "verified":
            assert kinds.get(row["source_id"]) == "pdf", f"{folder.name}:{row['rule_id']} cites a non-pdf source"


@pytest.mark.db
def test_a_verified_row_on_a_web_source_does_not_count(seeded, conn, tmp_path) -> None:
    folder = tmp_path / "udyogini_ka"  # a scheme with no verified rows, so the answer must be empty
    folder.mkdir()
    (folder / "source.yaml").write_text(
        "documents:\n  - id: demo-web\n    title: Demo page\n    official_url: https://example.org/p\n"
        "    issuing_authority: Demo\n    version: '1'\n    retrieval_date: 2026-01-01\n    source_kind: web\n",
        encoding="utf-8",
    )
    (folder / "evidence.yaml").write_text(
        "evidence:\n  - rule_id: age\n    source_id: demo-web\n    page: 1\n    passage: 'x'\n    status: verified\n",
        encoding="utf-8",
    )
    sync_sources(conn, root=tmp_path)
    assert verified_rule_ids(conn, "udyogini_ka") == set()
