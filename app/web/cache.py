"""web_cache: fetched official pages, the TTL rule, stale copies, and committed snapshots (rulebook/sources/<id>/web/).

get_page never fetches a page that is younger than WEB_CACHE_TTL_H. When WEB_FETCH_ENABLED is false it never opens a
connection: it returns the cached row (flagged stale when old) or None.
"""

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import yaml

from app.config import get_settings
from app.rules.sources import SOURCES_DIR
from app.web.fetch import FetchError, Page, fetch_page

FETCH_WORKERS = 2


def web_pages(scheme_id: str, root: Path | None = None) -> list[dict]:
    """The curated pages in web.yaml. Only https URLs are accepted; anything else is dropped."""
    path = (root or SOURCES_DIR) / scheme_id / "web.yaml"
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for item in raw.get("pages") or []:
        url = str(item.get("url", ""))
        if urlsplit(url).scheme == "https":
            out.append({"url": url, "title": str(item.get("title", ""))})
    return out


def skip_text_for(scheme_id: str, root: Path | None = None) -> list[str]:
    """Menu and boilerplate lines listed for this scheme in web.yaml (skip_text)."""
    path = (root or SOURCES_DIR) / scheme_id / "web.yaml"
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [str(x) for x in raw.get("skip_text") or []]


def allowed_hosts(pages: list[dict]) -> set[str]:
    return {(urlsplit(p["url"]).hostname or "").lower() for p in pages}


def slug_for(url: str) -> str:
    u = urlsplit(url)
    return re.sub(r"[^a-z0-9]+", "-", f"{u.hostname}{u.path}".lower()).strip("-")[:120]


def ttl() -> timedelta:
    return timedelta(hours=get_settings().WEB_CACHE_TTL_H)


def fetching_enabled() -> bool:
    return get_settings().WEB_FETCH_ENABLED


def lookup(conn: psycopg.Connection, url: str) -> dict | None:
    row = conn.execute("select * from web_cache where url = %s", (url,)).fetchone()
    return dict(row) if row else None


def is_fresh(row: dict | None, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return bool(row and row["text"] and row["fetched_at"] and now - row["fetched_at"] < ttl())


def is_old_snapshot(fetched_at: datetime | None, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    return bool(fetched_at) and now - fetched_at > timedelta(days=get_settings().WEB_SNAPSHOT_WARN_DAYS)


def view(row: dict, stale: bool) -> dict:
    return {
        "url": row["url"],
        "title": row["title"] or "",
        "text": row["text"],
        "content_sha256": row["content_sha256"],
        "fetched_at": row["fetched_at"],
        "stale": stale,
        "old": is_old_snapshot(row["fetched_at"]),
        "last_status": row["last_status"],
        "error": row["error"],
    }


def store_page(conn: psycopg.Connection, scheme_id: str, page: Page) -> None:
    conn.execute(
        "insert into web_cache (url, scheme_id, title, text, content_sha256, fetched_at, last_status, error) "
        "values (%s, %s, %s, %s, %s, %s, %s, null) on conflict (url) do update set scheme_id = excluded.scheme_id, "
        "title = excluded.title, text = excluded.text, content_sha256 = excluded.content_sha256, "
        "fetched_at = excluded.fetched_at, last_status = excluded.last_status, error = null",
        (page.url, scheme_id, page.title, page.text, page.sha256, page.fetched_at, page.status),
    )


def record_failure(conn: psycopg.Connection, scheme_id: str, url: str, error: str) -> None:
    """Keep the good text and its date; only the error and the time of the attempt change."""
    conn.execute(
        "insert into web_cache (url, scheme_id, last_status, error) values (%s, %s, null, %s) "
        "on conflict (url) do update set error = excluded.error, last_status = null",
        (url, scheme_id, error),
    )


def _fallback(row: dict | None) -> dict | None:
    return view(row, True) if row and row["text"] else None


def get_page(conn: psycopg.Connection, scheme_id: str, url: str) -> dict | None:
    """Fresh cache → no network. Otherwise fetch (if enabled); on failure the old copy is returned as stale."""
    row = lookup(conn, url)
    if is_fresh(row):
        return view(row, False)
    if not fetching_enabled():
        return _fallback(row)
    hosts = allowed_hosts(web_pages(scheme_id))
    try:
        page = fetch_page(url, hosts, skip_text=skip_text_for(scheme_id))
    except FetchError as exc:
        record_failure(conn, scheme_id, url, str(exc))
        return _fallback(row)
    store_page(conn, scheme_id, page)
    return view(lookup(conn, url), False)


def refresh_many(conn: psycopg.Connection, scheme_id: str, urls: list[str], budget_s: float = 10.0) -> dict:
    """Pages for the answer tool: fresh rows directly; the rest fetched in parallel within one time budget.

    Network work runs in threads; every database write happens here, in the calling thread.
    """
    results: dict[str, dict | None] = {}
    pending: list[tuple[str, dict | None]] = []
    for url in urls:
        row = lookup(conn, url)
        if is_fresh(row):
            results[url] = view(row, False)
        else:
            pending.append((url, row))
    if not pending:
        return results
    if not fetching_enabled():
        for url, row in pending:
            results[url] = _fallback(row)
        return results
    hosts = allowed_hosts(web_pages(scheme_id))
    executor = ThreadPoolExecutor(max_workers=FETCH_WORKERS)
    futures = {executor.submit(fetch_page, url, hosts, skip_text=skip_text_for(scheme_id)): (url, row) for url, row in pending}
    done, _ = wait(futures, timeout=budget_s)
    for future, (url, row) in futures.items():
        if future not in done:
            future.cancel()
            record_failure(conn, scheme_id, url, "timed out")
            results[url] = _fallback(row)
            continue
        try:
            store_page(conn, scheme_id, future.result())
            results[url] = view(lookup(conn, url), False)
        except FetchError as exc:
            record_failure(conn, scheme_id, url, str(exc))
            results[url] = _fallback(row)
        except Exception as exc:  # noqa: BLE001 - a bug must not break the answer; it is recorded
            record_failure(conn, scheme_id, url, f"error: {type(exc).__name__}")
            results[url] = _fallback(row)
    executor.shutdown(wait=False, cancel_futures=True)
    return results


# ---------------------------------------------------------------- snapshots (committed files, no database needed)

SNAPSHOT_HEADER_LINES = 3  # url, fetched_at, sha256; then the body (an optional "Title: ..." line, then the text)


def snapshot_path(scheme_id: str, url: str, root: Path | None = None) -> Path:
    return (root or SOURCES_DIR) / scheme_id / "web" / f"{slug_for(url)}.txt"


def body_for(title: str, text: str) -> str:
    return (f"Title: {title}\n" if title else "") + text


def write_snapshot(path: Path, url: str, title: str, text: str, fetched_at: datetime) -> str:
    body = body_for(title, text)
    sha = hashlib.sha256(body.encode("utf-8")).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"{url}\nfetched_at: {fetched_at.astimezone(UTC).isoformat(timespec='seconds')}\nsha256: {sha}\n{body}"
    path.write_text(content, encoding="utf-8", newline="\n")
    return sha


def parse_snapshot(path: Path) -> dict | None:
    """None if the file is malformed or its body does not match its sha256 (never loaded)."""
    parts = path.read_text(encoding="utf-8").split("\n", SNAPSHOT_HEADER_LINES)
    if len(parts) < SNAPSHOT_HEADER_LINES + 1 or not parts[1].startswith("fetched_at: ") or not parts[2].startswith("sha256: "):
        return None
    url, body = parts[0].strip(), parts[3]
    if hashlib.sha256(body.encode("utf-8")).hexdigest() != parts[2][len("sha256: ") :].strip():
        return None
    title = ""
    text = body
    if body.startswith("Title: "):
        first, _, text = body.partition("\n")
        title = first[len("Title: ") :]
    try:
        fetched = datetime.fromisoformat(parts[1][len("fetched_at: ") :].strip())
    except ValueError:
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return {"url": url, "title": title, "text": text, "fetched_at": fetched, "sha256": parts[2][8:].strip()}


def load_snapshots(conn: psycopg.Connection, scheme_id: str, root: Path | None = None) -> int:
    """Load a scheme's snapshot files into web_cache, unless the table already has an equal or newer copy.

    The file's fetched_at is kept (never now()). Only URLs listed in web.yaml are loaded.
    """
    allowed = {p["url"] for p in web_pages(scheme_id, root)}
    folder = (root or SOURCES_DIR) / scheme_id / "web"
    loaded = 0
    for path in sorted(folder.glob("*.txt")) if folder.is_dir() else []:
        snap = parse_snapshot(path)
        if not snap or snap["url"] not in allowed:
            continue
        row = lookup(conn, snap["url"])
        if row and row["fetched_at"] and row["fetched_at"] >= snap["fetched_at"]:
            continue
        conn.execute(
            "insert into web_cache (url, scheme_id, title, text, content_sha256, fetched_at, last_status, error) "
            "values (%s, %s, %s, %s, %s, %s, 200, null) on conflict (url) do update set scheme_id = excluded.scheme_id, "
            "title = excluded.title, text = excluded.text, content_sha256 = excluded.content_sha256, "
            "fetched_at = excluded.fetched_at, last_status = excluded.last_status, error = null",
            (snap["url"], scheme_id, snap["title"], snap["text"], snap["sha256"], snap["fetched_at"]),
        )
        loaded += 1
    return loaded


def load_all_snapshots(conn: psycopg.Connection, root: Path | None = None) -> int:
    base = root or SOURCES_DIR
    return sum(load_snapshots(conn, d.name, root) for d in sorted(base.iterdir()) if d.is_dir())
