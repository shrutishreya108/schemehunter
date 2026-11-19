"""Official source documents and rule evidence (reliability §3). Plain code, no AI.

Layout (per scheme, all optional):
  rulebook/sources/<scheme>/source.yaml    documents: [ {id, title, official_url, issuing_authority, publication_date,
                                            effective_date, retrieval_date, version, local_path} ]
  rulebook/sources/<scheme>/evidence.yaml  evidence: [ {rule_id, source_id, page, passage, status, last_verified} ]

Nothing is invented: a field that is not in the file stays NULL and is shown as "unavailable". The sha256 is
computed from the local file during sync; it is never typed by hand. A rule counts as verified only if its
evidence row says "verified" AND the source document is complete (see SOURCE_REQUIRED).
"""

import hashlib
from pathlib import Path

import psycopg
import yaml

from app.config import ROOT

SOURCES_DIR = ROOT / "rulebook" / "sources"
META_FIELDS = (
    "title",
    "official_url",
    "issuing_authority",
    "publication_date",
    "effective_date",
    "retrieval_date",
    "version",
    "local_path",
)
# A citation is only "verified" when these are all present. Missing one means: unavailable, not verified.
SOURCE_REQUIRED = ("title", "official_url", "issuing_authority", "version", "retrieval_date", "sha256")


def file_sha256(path: Path) -> str | None:
    if not path or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}


def read_scheme_sources(scheme_dir: Path) -> tuple[list[dict], list[dict]]:
    docs = _yaml(scheme_dir / "source.yaml").get("documents") or []
    evidence = _yaml(scheme_dir / "evidence.yaml").get("evidence") or []
    return docs, evidence


def upsert_source(conn: psycopg.Connection, scheme_id: str, doc: dict, scheme_dir: Path) -> None:
    local = doc.get("local_path")
    sha = file_sha256(scheme_dir / local) if local else None
    values = [doc["id"], scheme_id] + [doc.get(f) for f in META_FIELDS] + [sha]
    conn.execute(
        "insert into source_documents (id, scheme_id, title, official_url, issuing_authority, publication_date, "
        "effective_date, retrieval_date, version, local_path, sha256, synced_at) "
        "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) on conflict (id) do update set "
        "scheme_id = excluded.scheme_id, title = excluded.title, official_url = excluded.official_url, "
        "issuing_authority = excluded.issuing_authority, publication_date = excluded.publication_date, "
        "effective_date = excluded.effective_date, retrieval_date = excluded.retrieval_date, "
        "version = excluded.version, local_path = excluded.local_path, sha256 = excluded.sha256, synced_at = now()",
        (
            values[0],
            values[1],
            values[2],
            values[3],
            values[4],
            values[5],
            values[6],
            values[7],
            values[8],
            values[9],
            values[10],
        ),
    )


def upsert_evidence(conn: psycopg.Connection, scheme_id: str, ev: dict, known_sources: set[str]) -> bool:
    """Returns False (and stores nothing) if the evidence points at a source that is not in source.yaml."""
    if ev.get("source_id") not in known_sources or ev.get("status") not in ("verified", "unverified"):
        return False
    conn.execute(
        "insert into rule_evidence (scheme_id, rule_id, source_id, page, passage, status, last_verified) "
        "values (%s, %s, %s, %s, %s, %s, %s) on conflict (scheme_id, rule_id, source_id) do update set "
        "page = excluded.page, passage = excluded.passage, status = excluded.status, last_verified = excluded.last_verified",
        (
            scheme_id,
            ev["rule_id"],
            ev["source_id"],
            ev.get("page"),
            ev.get("passage"),
            ev["status"],
            ev.get("last_verified"),
        ),
    )
    return True


def sync_sources(conn: psycopg.Connection, root: Path = SOURCES_DIR) -> dict[str, int]:
    """Read every scheme's source and evidence files into the database. Safe to run many times."""
    counts = {"documents": 0, "evidence": 0, "skipped_evidence": 0}
    if not root.is_dir():
        return counts
    for scheme_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        docs, evidence = read_scheme_sources(scheme_dir)
        known = set()
        for doc in docs:
            if not doc.get("id"):
                continue
            upsert_source(conn, scheme_dir.name, doc, scheme_dir)
            known.add(doc["id"])
            counts["documents"] += 1
        for ev in evidence:
            if upsert_evidence(conn, scheme_dir.name, ev, known):
                counts["evidence"] += 1
            else:
                counts["skipped_evidence"] += 1
    return counts


def verified_rule_ids(conn: psycopg.Connection, scheme_id: str) -> set[str]:
    """Rules with a 'verified' evidence row whose source document is complete."""
    required = " and ".join(f"d.{f} is not null" for f in SOURCE_REQUIRED)
    rows = conn.execute(
        f"select e.rule_id from rule_evidence e join source_documents d on d.id = e.source_id "
        f"where e.scheme_id = %s and e.status = 'verified' and {required}",
        (scheme_id,),
    ).fetchall()
    return {r["rule_id"] for r in rows}


def source_citation(conn: psycopg.Connection, source_id: str) -> dict | None:
    """Everything needed to cite a source, with missing values left as None (never filled in)."""
    row = conn.execute("select * from source_documents where id = %s", (source_id,)).fetchone()
    return dict(row) if row else None


def is_complete(doc: dict | None) -> bool:
    return bool(doc) and all(doc.get(f) for f in SOURCE_REQUIRED)
