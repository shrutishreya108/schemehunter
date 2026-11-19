"""web_cache: TTL, stale copies, the no-network switch, and snapshots. Needs Postgres (db marker)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.db import connect, run_migrations
from app.web import cache, fetch

pytestmark = pytest.mark.db
SCHEME = "mudra"
URL = "https://financialservices.gov.in/pradhan-mantri-mudra-yojana-pmmy"
NOW = datetime.now(UTC)


@pytest.fixture
def db(settings_db: str, monkeypatch: pytest.MonkeyPatch):
    run_migrations(settings_db)
    calls: list[str] = []

    def ok(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=b"<title>Mudra page</title><h1>Loans</h1><p>Mudra loans for small units.</p>",
            headers={"content-type": "text/html"},
        )

    monkeypatch.setattr(fetch, "_transport", httpx.MockTransport(ok))
    monkeypatch.setattr(fetch, "_default_resolver", lambda host: ["8.8.8.8"])
    with connect(settings_db) as conn:
        conn.execute("delete from web_cache")  # seed may have loaded the committed snapshots
        conn.commit()
        yield conn, calls
    get_settings.cache_clear()


def row_at(conn, url: str, when: datetime, text: str = "Old text.", error: str | None = None) -> None:
    conn.execute(
        "insert into web_cache (url, scheme_id, title, text, content_sha256, fetched_at, last_status, error) "
        "values (%s, %s, 'Old title', %s, 'x', %s, 200, %s) on conflict (url) do update set "
        "text = excluded.text, fetched_at = excluded.fetched_at, error = excluded.error",
        (url, SCHEME, text, when, error),
    )


def test_a_fresh_row_is_returned_with_no_network(db) -> None:
    conn, calls = db
    row_at(conn, URL, NOW - timedelta(hours=1))
    page = cache.get_page(conn, SCHEME, URL)
    assert page and page["stale"] is False and page["text"] == "Old text."
    assert calls == []


def test_an_old_row_is_refetched_and_replaced(db) -> None:
    conn, calls = db
    row_at(conn, URL, NOW - timedelta(days=3))
    page = cache.get_page(conn, SCHEME, URL)
    assert page["stale"] is False and "Mudra loans" in page["text"]
    assert calls  # went to the (fake) network


def test_a_failed_fetch_returns_the_old_copy_as_stale_and_keeps_its_text(db, monkeypatch) -> None:
    conn, _ = db
    row_at(conn, URL, NOW - timedelta(days=3))
    monkeypatch.setattr(fetch, "_transport", httpx.MockTransport(lambda r: httpx.Response(503)))
    page = cache.get_page(conn, SCHEME, URL)
    assert page["stale"] is True and page["text"] == "Old text."
    stored = cache.lookup(conn, URL)
    assert stored["text"] == "Old text." and stored["error"] and stored["fetched_at"] < NOW - timedelta(days=2)


def test_a_failed_fetch_with_no_row_returns_none(db, monkeypatch) -> None:
    conn, _ = db
    monkeypatch.setattr(fetch, "_transport", httpx.MockTransport(lambda r: httpx.Response(503)))
    assert cache.get_page(conn, SCHEME, URL) is None
    assert cache.lookup(conn, URL)["error"]  # the failure is recorded, with no text


def test_with_fetching_off_the_fake_transport_is_never_called(db, monkeypatch) -> None:
    conn, calls = db
    monkeypatch.setenv("WEB_FETCH_ENABLED", "false")
    get_settings.cache_clear()
    assert cache.get_page(conn, SCHEME, URL) is None  # empty cache
    row_at(conn, URL, NOW - timedelta(days=9))
    page = cache.get_page(conn, SCHEME, URL)  # stale cache
    assert page["stale"] is True and page["text"] == "Old text."
    assert calls == []


def test_refresh_many_serves_fresh_rows_without_fetching(db) -> None:
    conn, calls = db
    row_at(conn, URL, NOW - timedelta(minutes=5))
    got = cache.refresh_many(conn, SCHEME, [URL])
    assert got[URL]["stale"] is False and calls == []


def test_snapshot_round_trip_keeps_title_text_and_its_date(tmp_path: Path) -> None:
    when = datetime(2026, 11, 1, 9, 30, tzinfo=UTC)
    path = tmp_path / "page.txt"
    cache.write_snapshot(path, URL, "Mudra title", "Line one.\nLine two.", when)
    snap = cache.parse_snapshot(path)
    assert snap["url"] == URL and snap["title"] == "Mudra title"
    assert snap["text"] == "Line one.\nLine two." and snap["fetched_at"] == when


def test_a_snapshot_with_a_wrong_checksum_is_not_loaded(tmp_path: Path) -> None:
    path = tmp_path / "page.txt"
    cache.write_snapshot(path, URL, "T", "Body.", datetime(2026, 1, 1, tzinfo=UTC))
    path.write_text(path.read_text(encoding="utf-8").replace("Body.", "Edited."), encoding="utf-8")
    assert cache.parse_snapshot(path) is None


def test_loading_a_snapshot_keeps_its_own_fetched_at_and_never_overwrites_newer(db, tmp_path: Path) -> None:
    conn, _ = db
    scheme_dir = tmp_path / SCHEME
    (scheme_dir / "web").mkdir(parents=True)
    (scheme_dir / "web.yaml").write_text(f"pages:\n  - url: {URL}\n    title: T\n", encoding="utf-8")
    old_time = datetime(2026, 3, 1, tzinfo=UTC)
    cache.write_snapshot(scheme_dir / "web" / "p.txt", URL, "T", "From the file.", old_time)
    assert cache.load_snapshots(conn, SCHEME, root=tmp_path) == 1
    row = cache.lookup(conn, URL)
    assert row["fetched_at"] == old_time and row["text"] == "From the file."
    row_at(conn, URL, datetime(2026, 6, 1, tzinfo=UTC), text="Newer copy.")
    assert cache.load_snapshots(conn, SCHEME, root=tmp_path) == 0
    assert cache.lookup(conn, URL)["text"] == "Newer copy."


def test_a_snapshot_for_a_url_not_in_web_yaml_is_ignored(db, tmp_path: Path) -> None:
    conn, _ = db
    scheme_dir = tmp_path / SCHEME
    (scheme_dir / "web").mkdir(parents=True)
    (scheme_dir / "web.yaml").write_text(f"pages:\n  - url: {URL}\n", encoding="utf-8")
    cache.write_snapshot(scheme_dir / "web" / "other.txt", "https://evil.example/x", "T", "x", NOW)
    assert cache.load_snapshots(conn, SCHEME, root=tmp_path) == 0
