"""Shared fixtures.

DB tests are marked `db` and skipped unless DATABASE_URL is set. They never touch that database's
data: they create a throwaway database on the same server (schemehunter_test_<random>) and drop it after.
"""

import os
import secrets
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def pytest_collection_modifyitems(config, items) -> None:
    if os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="needs Postgres: set DATABASE_URL")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def db_url() -> Iterator[str]:
    base = os.environ["DATABASE_URL"]
    name = f"schemehunter_test_{secrets.token_hex(4)}"
    admin = make_conninfo(base, dbname="postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'create database "{name}"')
    url = make_conninfo(base, dbname=name)
    assert conninfo_to_dict(url)["dbname"] == name
    try:
        yield url
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(f'drop database if exists "{name}" with (force)')


@pytest.fixture
def settings_db(db_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Point app settings at the throwaway DB for code that reads DATABASE_URL itself."""
    from app.config import get_settings

    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin")
    get_settings.cache_clear()
    yield db_url
    get_settings.cache_clear()


# ---------------------------------------------------------------- Claude: FakeClaude for every test


@pytest.fixture(autouse=True)
def fake_claude(monkeypatch: pytest.MonkeyPatch):
    """Every test gets a FakeClaude, and any attempt to build a real Anthropic client fails loudly."""
    from fake_claude import FakeClaude

    from app.brain import claude

    def no_network(*args, **kwargs):
        raise RuntimeError("tests must never construct a real Anthropic client")

    try:
        import anthropic

        monkeypatch.setattr(anthropic, "Anthropic", no_network)
    except ImportError:
        pass
    monkeypatch.setattr(claude, "AnthropicClient", no_network)
    fake = FakeClaude()
    claude.set_client(fake)
    yield fake
    claude.set_client(None)


# ---------------------------------------------------------------- SMTP: local fake server


class _CollectingHandler:
    def __init__(self) -> None:
        self.messages: list = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


@pytest.fixture
def smtp_server():
    """aiosmtpd on 127.0.0.1:<free port>, plain SMTP (adapter uses SMTP_SECURITY=none against it)."""
    import socket

    from aiosmtpd.controller import Controller

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    handler = _CollectingHandler()
    controller = Controller(handler, hostname="127.0.0.1", port=port)
    controller.start()
    controller.handler = handler
    try:
        yield controller
    finally:
        controller.stop()


@pytest.fixture
def smtp_settings(smtp_server, monkeypatch: pytest.MonkeyPatch):
    from app.config import get_settings

    for key, value in {
        "SMTP_HOST": "127.0.0.1",
        "SMTP_PORT": str(smtp_server.port),
        "SMTP_SECURITY": "none",
        "SMTP_USER": "",
        "SMTP_PASSWORD": "",
        "EMAIL_FROM": "hello@example.in",
        "CONTACT_TO": "hello@example.in",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield smtp_server
    get_settings.cache_clear()


# ---------------------------------------------------------------- seeded database


@pytest.fixture
def seeded(settings_db: str) -> dict:
    """Fresh demo data (demo-reset) in the throwaway DB. Returns {persona_key: case_id}."""
    from app.seed import seed

    return {k: str(v) for k, v in seed(settings_db, reset=True).items()}


@pytest.fixture
def conn(settings_db: str):
    from app.db import connect

    with connect(settings_db) as c:
        yield c
