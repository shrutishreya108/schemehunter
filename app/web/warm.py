"""Warm the web cache and export snapshots (make warm-web).

  python -m app.web.warm --export-only [--scheme id]   fetch and write rulebook/sources/<id>/web/*.txt; no database
  python -m app.web.warm [--scheme id] [--export]      fetch into web_cache (needs DATABASE_URL); --export also writes files

Fetching follows every rule in fetch.py. Refused or failed pages are printed and skipped; the run continues.
Prints per page: status, character count, and whether the snapshot is new, unchanged or changed.
"""

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.db import connect
from app.rules.sources import SOURCES_DIR
from app.web.cache import (
    allowed_hosts,
    body_for,
    fetching_enabled,
    parse_snapshot,
    record_failure,
    skip_text_for,
    snapshot_path,
    store_page,
    web_pages,
    write_snapshot,
)
from app.web.fetch import FetchError, Page, fetch_page


def schemes_with_web(only: str | None, root: Path | None = None) -> list[str]:
    base = root or SOURCES_DIR
    ids = sorted(d.name for d in base.iterdir() if d.is_dir() and (d / "web.yaml").exists())
    return [only] if only else ids


def _state(path: Path, body_sha: str | None) -> str:
    old = parse_snapshot(path) if path.exists() else None
    if old is None:
        return "new"
    return "unchanged" if old["sha256"] == body_sha else "changed"


def _report(scheme_id: str, url: str, status: str) -> None:
    print(f"{scheme_id}  {url}  {status}")


def run_export_only(scheme: str | None, root: Path | None = None) -> int:
    failures = 0
    for scheme_id in schemes_with_web(scheme, root):
        pages = web_pages(scheme_id, root)
        hosts = allowed_hosts(pages)
        for item in pages:
            url = item["url"]
            try:
                page = fetch_page(url, hosts, skip_text=skip_text_for(scheme_id))
            except FetchError as exc:
                failures += 1
                _report(scheme_id, url, f"error: {exc}")
                continue
            path = snapshot_path(scheme_id, url, root)
            state = _state(path, _body_sha(page.title, page.text))
            write_snapshot(path, url, page.title, page.text, page.fetched_at)
            _report(scheme_id, url, f"status {page.status}, {len(page.text)} characters, {state}")
    return 1 if failures else 0


def _body_sha(title: str, text: str) -> str:
    return hashlib.sha256(body_for(title, text).encode("utf-8")).hexdigest()


def run_warm(scheme: str | None, export: bool) -> int:
    failures = 0
    with connect(get_settings().DATABASE_URL) as conn:
        for scheme_id in schemes_with_web(scheme):
            pages = web_pages(scheme_id)
            hosts = allowed_hosts(pages)
            for item in pages:
                url = item["url"]
                try:
                    page: Page = fetch_page(url, hosts, skip_text=skip_text_for(scheme_id))
                except FetchError as exc:
                    failures += 1
                    record_failure(conn, scheme_id, url, str(exc))
                    _report(scheme_id, url, f"error: {exc}")
                    continue
                store_page(conn, scheme_id, page)
                line = f"status {page.status}, {len(page.text)} characters"
                if export:
                    path = snapshot_path(scheme_id, url)
                    state = _state(path, _body_sha(page.title, page.text))
                    write_snapshot(path, url, page.title, page.text, page.fetched_at)
                    line += f", snapshot {state}"
                _report(scheme_id, url, line)
        conn.commit()
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scheme")
    parser.add_argument("--export", action="store_true", help="also write snapshot files (needs the database)")
    parser.add_argument("--export-only", action="store_true", help="write snapshot files without a database")
    args = parser.parse_args(argv)
    if not fetching_enabled():
        print("WEB_FETCH_ENABLED is false: nothing fetched. Set it to true on a machine that can reach the sites.")
        return 2
    started = datetime.now(UTC)
    code = run_export_only(args.scheme) if args.export_only else run_warm(args.scheme, args.export)
    print(f"done in {(datetime.now(UTC) - started).total_seconds():.1f} s")
    return code


if __name__ == "__main__":
    sys.exit(main())
