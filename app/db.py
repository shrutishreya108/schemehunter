"""Postgres access: connections, migrations, small query helpers."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.config import ROOT, get_settings

SQL_DIR = ROOT / "sql"


@contextmanager
def connect(url: str | None = None) -> Iterator[psycopg.Connection]:
    """Yield a connection with dict rows; commits on clean exit, rolls back on error.

    One connection per request/job is enough for this load; add psycopg_pool if that changes.
    """
    with psycopg.connect(url or get_settings().DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def migration_files(sql_dir: Path = SQL_DIR) -> list[Path]:
    return sorted(sql_dir.glob("[0-9][0-9][0-9]_*.sql"))


def applied_versions(conn: psycopg.Connection) -> set[str]:
    exists = conn.execute("select to_regclass('public.schema_migrations') as t").fetchone()
    if not exists or exists["t"] is None:
        return set()
    return {r["version"] for r in conn.execute("select version from schema_migrations").fetchall()}


def run_migrations(url: str | None = None, sql_dir: Path = SQL_DIR) -> list[str]:
    """Apply sql/NNN_*.sql in order, each in its own transaction. Returns versions applied now.

    001_init.sql itself creates schema_migrations, so the table is only read if it exists.
    """
    applied_now: list[str] = []
    with connect(url) as conn:
        for path in migration_files(sql_dir):
            version = path.stem
            if version in applied_versions(conn):
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("insert into schema_migrations (version) values (%s)", (version,))
            applied_now.append(version)
    return applied_now


def fetch_all(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> list[dict]:
    return conn.execute(sql, params).fetchall()


def fetch_one(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> dict | None:
    return conn.execute(sql, params).fetchone()


def execute(conn: psycopg.Connection, sql: str, params: tuple | dict = ()) -> int:
    return conn.execute(sql, params).rowcount


def jsonb(value: Any) -> Jsonb:
    return Jsonb(value)
