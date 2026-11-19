"""Every verified evidence row must be proved against the extracted PDF text (no model involved).

A passage counts only if it appears verbatim in pages/NN.txt after whitespace is collapsed and case is folded.
"""

import re
from pathlib import Path

import pytest
import yaml

from app.rules.sources import SOURCES_DIR


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _scheme_dirs() -> list[Path]:
    return sorted(p for p in SOURCES_DIR.iterdir() if p.is_dir())


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def _rule_ids(scheme_id: str) -> set[str]:
    raw = _load(SOURCES_DIR.parent / "schemes" / f"{scheme_id}.yaml")
    ids = {r["id"] for r in (raw.get("eligibility") or {}).get("all") or []}
    return ids | set(raw) | set(raw.get("benefit_calc") or {})  # top-level blocks such as benefit, edp, documents


ROWS = [
    (d.name, row)
    for d in _scheme_dirs()
    for row in (_load(d / "evidence.yaml").get("evidence") or [])
    if row.get("status") == "verified"
]


@pytest.mark.parametrize(
    "scheme_id, row", ROWS, ids=[f"{s}:{r.get('rule_id')}:p{r.get('page')}" for s, r in ROWS] or ["none"]
)
def test_verified_passage_is_on_its_page(scheme_id: str, row: dict) -> None:
    folder = SOURCES_DIR / scheme_id
    docs = {d["id"] for d in (_load(folder / "source.yaml").get("documents") or []) if d.get("id")}
    assert row["source_id"] in docs, "source_id is not in source.yaml"
    assert row["rule_id"] in _rule_ids(scheme_id), "rule_id is not a rule or block of this scheme"
    page_file = folder / "pages" / f"{int(row['page']):02d}.txt"
    assert page_file.is_file(), f"no extracted text for page {row['page']}"
    assert _norm(row["passage"]) in _norm(page_file.read_text(encoding="utf-8")), "passage is not on that page"


@pytest.mark.parametrize("folder", _scheme_dirs(), ids=lambda p: p.name)
def test_scheme_without_pages_has_no_verified_rows(folder: Path) -> None:
    pages = list((folder / "pages").glob("[0-9][0-9]*.txt")) if (folder / "pages").is_dir() else []
    verified = [r for r in (_load(folder / "evidence.yaml").get("evidence") or []) if r.get("status") == "verified"]
    if not pages:
        assert not verified, f"{folder.name} has verified rows but no page text"
