"""Apply a scheme's field mapping → data for the form pack + step list (LLD §13.2). Pure code, no AI.

The official PDF is not edited: the pack mirrors the form's sections and the user copies values across.
"""

from datetime import date
from pathlib import Path
from typing import Any

import yaml

MAPPINGS_DIR = Path(__file__).parent / "mappings"
GENERIC_STEPS = [
    {"en": "Open the scheme's portal or office", "hi": "योजना का पोर्टल या दफ़्तर खोलें"},
    {"en": "Send me the reference number when you are done", "hi": "हो जाने पर मुझे संदर्भ संख्या भेजें"},
]


def load_mapping(scheme_id: str) -> dict | None:
    path = MAPPINGS_DIR / f"{scheme_id}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None


def steps_for(scheme_id: str, lang: str) -> list[str]:
    steps = (load_mapping(scheme_id) or {}).get("steps") or GENERIC_STEPS
    return [s.get(lang) or s["en"] for s in steps]


def inr_plain(value: Any) -> str:
    from app.finance.project_report import inr

    return inr(float(value))


def format_value(raw: Any, spec: dict) -> str | None:
    """One field's display value, or None when the fact is missing."""
    if raw is None or raw == "":
        return None
    if mapped := spec.get("map"):
        raw = mapped.get(raw, raw)
    fmt = spec.get("format")
    if fmt == "DD/MM/YYYY":
        d = date.fromisoformat(str(raw))
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    if fmt == "inr":
        return inr_plain(raw)
    return str(raw)


def apply_mapping(mapping: dict, facts: dict) -> list[dict]:
    """[{key, label{en,hi}, value|None}] in mapping order. Missing facts stay None: never invented."""
    out = []
    for key, spec in mapping["fields"].items():
        if "join" in spec:
            parts = [str(facts[f]) for f in spec["join"] if facts.get(f)]
            has_required = all(
                facts.get(f) for f in spec.get("require", [])
            )  # e.g. a bare state is not an address
            value = spec.get("sep", " ").join(parts) if parts and has_required else None
        else:
            value = format_value(facts.get(spec["from"]), spec)
        out.append({"key": key, "label": spec.get("label") or {"en": key, "hi": key}, "value": value})
    return out


def missing_fields(fields: list[dict]) -> list[str]:
    return [f["key"] for f in fields if not f["value"]]
