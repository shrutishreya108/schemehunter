"""Load the Rule Book and seed/ into Postgres. Idempotent. Prints the three case links.

    python -m app.seed            # upsert schemes, (re)load showcase personas
    python -m app.seed --reset    # demo-reset: truncate everything first

Personas get deterministic ids (uuid5) and deterministic link tokens (HMAC of SESSION_SECRET), so
running this twice leaves the database in the same state and the printed links keep working.
"""

import argparse
import json
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
import yaml

from app.config import ROOT, get_settings
from app.db import connect, jsonb, run_migrations
from app.events import log_event
from app.rules.engine import evaluate_all
from app.rules.facts import build_facts
from app.rules.loader import load_schemes

SEED_DIR = ROOT / "seed"
NS = uuid5(NAMESPACE_URL, "https://schemehunter/seed")
TABLES = [
    "artifacts",
    "login_challenges",
    "identities",
    "rate_limits",
    "events",
    "pending_actions",
    "messages",
    "jobs",
    "brain_runs",
    "matches",
    "applications",
    "documents",
    "profile_answers",
    "cases",
    "source_chunks",
    "rule_evidence",
    "source_documents",
    "schemes",
]
UNKEYED_TABLES = ["events", "pending_actions", "messages", "jobs", "brain_runs"]  # case_id without FK


def read_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def case_id_for(key: str) -> UUID:
    return uuid5(NS, f"case:{key}")


def application_id_for(key: str, scheme_id: str) -> UUID:
    return uuid5(NS, f"application:{key}:{scheme_id}")


def json_safe(value):
    """YAML gives date objects for dob; store ISO strings in jsonb."""
    return value.isoformat() if isinstance(value, date) else value


def upsert_schemes(conn: psycopg.Connection, schemes: list[dict]) -> None:
    for s in schemes:
        spec = {k: v for k, v in s.items() if not k.startswith("_")}
        conn.execute(
            "insert into schemes (id, name, spec, spec_hash, last_verified) values (%s, %s, %s, %s, %s) "
            "on conflict (id) do update set name = excluded.name, spec = excluded.spec, "
            "spec_hash = excluded.spec_hash, last_verified = excluded.last_verified",
            (s["id"], jsonb(s["name"]), jsonb(yaml_to_json(spec)), s["_hash"], s.get("last_verified")),
        )


def yaml_to_json(value):
    if isinstance(value, dict):
        return {k: yaml_to_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [yaml_to_json(v) for v in value]
    return json_safe(value)


def delete_case(conn: psycopg.Connection, case_id: UUID) -> None:
    for table in UNKEYED_TABLES:
        conn.execute(f"delete from {table} where case_id = %s", (case_id,))
    conn.execute("delete from cases where id = %s", (case_id,))  # cascades to answers, docs, matches, apps


def insert_case(conn: psycopg.Connection, persona: dict, now: datetime) -> UUID:
    case_id = case_id_for(persona["key"])
    state = "ONBOARDING" if persona.get("stage") == "fresh" else "ACTIVE"
    conn.execute(
        "insert into cases (id, display_name, language, state_code, email, state, "
        "consent_version, consent_at, created_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            case_id,
            persona.get("display_name"),
            persona.get("language", "en"),
            persona.get("state_code"),
            persona.get("email"),
            state,
            "demo-v0",
            now,
            now,
        ),
    )
    if persona.get("email"):  # demo personas sign in like anyone else: a verified email identity
        conn.execute(
            "insert into identities (case_id, kind, value_norm, verified_at) values (%s, 'email', %s, %s) "
            "on conflict (kind, value_norm) do nothing",
            (case_id, persona["email"].lower(), now),
        )
    for key, value in persona["profile"].items():
        conn.execute(
            "insert into profile_answers (case_id, key, value, source) values (%s, %s, %s, 'seed')",
            (case_id, key, jsonb(json_safe(value))),
        )
    return case_id


def insert_matches(conn: psycopg.Connection, case_id: UUID, persona: dict, schemes: list[dict]) -> list[dict]:
    facts = build_facts(persona["profile"], state_code=persona.get("state_code"))
    results = evaluate_all(schemes, facts)
    for r in results:
        conn.execute(
            "insert into matches (case_id, scheme_id, result, rule_results, missing_facts, benefit) "
            "values (%s, %s, %s, %s, %s, %s)",
            (
                case_id,
                r["scheme_id"],
                r["result"],
                jsonb(
                    {
                        "rules": r["rules"],
                        "deciding_rule": r["deciding_rule"],
                        "deciding_page": r["deciding_page"],
                    }
                ),
                jsonb(r["missing_facts"]),
                jsonb(r["benefit"]),
            ),
        )
    return results


def insert_history(conn: psycopg.Connection, case_id: UUID, key: str, history: dict, now: datetime) -> None:
    app = history.get("application")
    app_id = None
    if app:
        app_id = application_id_for(key, app["scheme_id"])
        conn.execute(
            "insert into applications (id, case_id, scheme_id, state, ref_number, deadline, cycle, "
            "last_movement_at, created_at, updated_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                app_id,
                case_id,
                app["scheme_id"],
                app["state"],
                app.get("ref_number"),
                app.get("deadline"),
                app.get("cycle"),
                now - timedelta(days=app["last_movement_days_ago"]),
                now - timedelta(days=app["created_days_ago"]),
                now - timedelta(days=app["last_movement_days_ago"]),
            ),
        )
    for e in history.get("events", []):
        log_event(
            conn,
            case_id,
            e["actor"],
            e["type"],
            {"en": e["en"], "hi": e["hi"]},
            payload=e.get("payload"),
            application_id=app_id if e.get("app") else None,
            created_at=now - timedelta(days=e["days_ago"]),
        )
    for j in history.get("jobs", []):
        conn.execute(
            "insert into jobs (type, case_id, application_id, payload, run_at) values (%s, %s, %s, %s, %s)",
            (
                j["type"],
                case_id,
                app_id,
                jsonb(j.get("payload") or {}),
                now + timedelta(minutes=j["run_in_minutes"]),
            ),
        )


def seed_persona(conn: psycopg.Connection, persona: dict, schemes: list[dict], now: datetime) -> UUID:
    case_id = case_id_for(persona["key"])
    delete_case(conn, case_id)
    insert_case(conn, persona, now)
    insert_matches(conn, case_id, persona, schemes)
    history_path = SEED_DIR / "histories" / f"{persona['key']}.yaml"
    if history_path.exists():
        insert_history(conn, case_id, persona["key"], read_yaml(history_path), now)
    return case_id


def load_artifacts(conn: psycopg.Connection, seeded: dict[str, UUID], schemes: list[dict]) -> int:
    """Pre-generated demo artifacts (seed/artifacts/manifest.yaml), so demos don't spend Claude credit.

    match_explanation entries are stamped with the scheme's current spec hash, so explain_match treats them
    as cached; if the scheme file changes, the stamp no longer matches and a fresh explanation is generated.
    """
    hashes = {s["id"]: s["_hash"] for s in schemes}
    loaded = 0
    for item in read_yaml(SEED_DIR / "artifacts" / "manifest.yaml"):
        case_id = seeded.get(item["persona"])
        if case_id is None:
            continue
        content = json.loads((SEED_DIR / "artifacts" / item["file"]).read_text(encoding="utf-8"))
        if item["target"] == "match_explanation":
            content = {**content, "spec_hash": hashes[item["scheme_id"]]}
            conn.execute(
                "update matches set explanation = %s where case_id = %s and scheme_id = %s",
                (jsonb(content), case_id, item["scheme_id"]),
            )
        else:
            app_id = application_id_for(item["persona"], item["scheme_id"])
            exists = conn.execute("select 1 from applications where id = %s", (app_id,)).fetchone()
            conn.execute(
                "insert into artifacts (case_id, application_id, scheme_id, kind, content, source) "
                "values (%s, %s, %s, %s, %s, 'seed')",
                (case_id, app_id if exists else None, item["scheme_id"], item["kind"], jsonb(content)),
            )
        loaded += 1
    return loaded


def load_cost_inputs(conn: psycopg.Connection, seeded: dict[str, UUID]) -> int:
    """seed/costs/<persona>.yaml → an artifact (kind cost_inputs) that build_project_report reads."""
    loaded = 0
    for key, case_id in seeded.items():
        path = SEED_DIR / "costs" / f"{key}.yaml"
        if path.exists():
            conn.execute(
                "insert into artifacts (case_id, kind, content, source) values (%s, 'cost_inputs', %s, 'seed')",
                (case_id, jsonb(json_safe(read_yaml(path)))),
            )
            loaded += 1
    return loaded


def truncate_all(conn: psycopg.Connection) -> None:
    conn.execute(f"truncate {', '.join(TABLES)} restart identity cascade")


def case_email(key: str) -> str:
    return next(p["email"] for p in read_yaml(SEED_DIR / "personas.yaml") if p["key"] == key)


def seed(url: str | None = None, reset: bool = False, now: datetime | None = None) -> dict[str, UUID]:
    now = now or datetime.now(UTC)
    run_migrations(url)
    schemes = load_schemes()
    showcase = read_yaml(SEED_DIR / "showcase.yaml")
    personas = {p["key"]: p for p in read_yaml(SEED_DIR / "personas.yaml")}
    seeded: dict[str, UUID] = {}
    with connect(url) as conn, conn.transaction():
        if reset:
            truncate_all(conn)
        upsert_schemes(conn, schemes)
        from app.rules.sources import sync_sources

        sync_sources(conn)
        for key in showcase["personas"]:
            seeded[key] = seed_persona(conn, personas[key], schemes, now)
        load_artifacts(conn, seeded, schemes)
        load_cost_inputs(conn, seeded)
    return seeded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="truncate all tables first (demo-reset)")
    args = parser.parse_args(argv)
    started = time.monotonic()
    seeded = seed(reset=args.reset)
    print(f"seeded {len(seeded)} cases in {time.monotonic() - started:.2f}s")
    for key, case_id in seeded.items():
        print(
            f"  {key:<7} case {case_id}  sign in at {get_settings().APP_BASE_URL.rstrip('/')}/signin as {case_email(key)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
