"""Which papers a scheme needs for this person, and whether they are all OK (LLD §8, §9 guard)."""

import psycopg

from app.rules.facts import build_facts

DEFAULT_DOCUMENTS = ["aadhaar", "passbook"]  # schemes whose YAML has no `documents:` yet (curator TODO)
GENERATED = {"project_report"}  # made by us in slice 9, after the user's papers are OK

# conditional document → predicate over facts. Names come from the scheme YAML (`<doc>_if_<condition>`).
CONDITIONS = {
    "caste_cert_if_special": lambda f: f.get("category") in {"SC", "ST", "OBC"},
    "education_cert_if_cost_gt_10l": lambda f: (f.get("project_cost") or 0) > 1_000_000,
}


def resolve_documents(scheme: dict, facts: dict) -> list[str]:
    """Document types needed, conditionals resolved. Unknown conditionals are dropped, not guessed."""
    out: list[str] = []
    for name in scheme.get("documents") or DEFAULT_DOCUMENTS:
        if "_if_" in name:
            test = CONDITIONS.get(name)
            if not test or not test(facts):
                continue
            name = name.split("_if_")[0]
        if name not in out:
            out.append(name)
    return out


def load_facts(conn: psycopg.Connection, case_id: str) -> dict:
    rows = conn.execute("select key, value from profile_answers where case_id = %s", (case_id,)).fetchall()
    case = conn.execute("select state_code from cases where id = %s", (case_id,)).fetchone()
    return build_facts({r["key"]: r["value"] for r in rows}, state_code=case["state_code"] if case else None)


def document_statuses(conn: psycopg.Connection, case_id: str, scheme: dict) -> list[dict]:
    """[{doc_type, have, status}] for each needed paper; status is the best one uploaded (OK > RETAKE > PROBLEM)."""
    rank = {"OK": 3, "PENDING": 2, "RETAKE": 1, "PROBLEM": 0}
    have: dict[str, str] = {}
    for r in conn.execute("select doc_type, check_status from documents where case_id = %s", (case_id,)):
        if r["doc_type"] and rank.get(r["check_status"], 0) >= rank.get(have.get(r["doc_type"], ""), -1):
            have[r["doc_type"]] = r["check_status"]
    needed = resolve_documents(scheme, load_facts(conn, case_id))
    return [{"doc_type": d, "have": d in have, "status": have.get(d)} for d in needed]


def required_docs_ok(conn: psycopg.Connection, case_id: str, scheme_id: str) -> bool:
    """True when every paper the user must supply is uploaded and OK (generated papers are excluded)."""
    from app.rules.loader import load_schemes

    scheme = next((s for s in load_schemes() if s["id"] == scheme_id), None)
    if not scheme:
        return False
    rows = [r for r in document_statuses(conn, case_id, scheme) if r["doc_type"] not in GENERATED]
    return bool(rows) and all(r["status"] == "OK" for r in rows)
