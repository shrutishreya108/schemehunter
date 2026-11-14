"""Who official mail goes to: seed/contacts.yaml (bank branch, nodal officer, district office). Demo: your own inboxes."""

from pathlib import Path

import yaml

CONTACTS_FILE = Path(__file__).resolve().parent.parent.parent / "seed" / "contacts.yaml"
AUDIENCE_KEYS = {"bank": ["bank_branch"], "officer": ["nodal_officer", "district_office"]}


def load_contacts(path: Path = CONTACTS_FILE) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def contact_for(scheme_id: str, audience: str, path: Path = CONTACTS_FILE) -> dict | None:
    """First contact for this audience that handles the scheme. The model never chooses an address."""
    wanted = AUDIENCE_KEYS.get(audience, [])
    for key in wanted:
        for c in load_contacts(path):
            if c["key"] == key and scheme_id in c.get("schemes", []):
                return c
    return None
