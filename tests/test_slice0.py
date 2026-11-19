"""Slice 0: app boots, /healthz answers, migrations apply once and are tracked."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import connect, migration_files, run_migrations


@pytest.fixture
def no_db(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_healthz_without_db(no_db) -> None:
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"ok": True, "db": "not configured"}


def test_not_built_yet_handler_returns_501(no_db) -> None:
    """Stubbed features (WhatsApp, the scraper) raise NotImplementedError; the API answers 501, not a crash."""
    from app.main import app

    @app.get("/_probe_not_built")
    def probe() -> None:
        raise NotImplementedError("stretch feature")

    try:
        with TestClient(app) as client:
            resp = client.get("/_probe_not_built")
            assert resp.status_code == 501 and resp.json() == {
                "error": "not built yet",
                "slice": "stretch feature",
            }
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") != "/_probe_not_built"]


def test_migration_files_in_order() -> None:
    assert [p.name for p in migration_files()] == [
        "001_init.sql",
        "002_seed_schemes_hash.sql",
        "003_rate_limits_artifacts.sql",
        "004_brain_run_costs.sql",
        "005_pending_attempts.sql",
        "006_email_signin.sql",
        "007_source_metadata.sql",
        "008_source_chunks.sql",
        "009_web_cache.sql",
        "010_source_kind.sql",
        "011_message_clock.sql",
    ]


@pytest.mark.db
def test_migrations_apply_once(settings_db: str) -> None:
    first = run_migrations(settings_db)
    assert run_migrations(settings_db) == []
    all_versions = [
        "001_init",
        "002_seed_schemes_hash",
        "003_rate_limits_artifacts",
        "004_brain_run_costs",
        "005_pending_attempts",
        "006_email_signin",
        "007_source_metadata",
        "008_source_chunks",
        "009_web_cache",
        "010_source_kind",
        "011_message_clock",
    ]
    assert first in ([], all_versions)
    with connect(settings_db) as conn:
        versions = [r["version"] for r in conn.execute("select version from schema_migrations order by 1")]
        tables = {
            r["t"]
            for r in conn.execute(
                "select table_name as t from information_schema.tables where table_schema = 'public'"
            )
        }
    assert versions == all_versions
    assert {
        "cases",
        "events",
        "jobs",
        "pending_actions",
        "brain_runs",
        "schema_migrations",
        "rate_limits",
        "artifacts",
    } <= tables


@pytest.mark.db
def test_healthz_with_db(settings_db: str) -> None:
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"ok": True, "db": "ok"}
