"""Read rulebook/schemes/*.yaml, hash each file, validate its shape."""

import hashlib
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import ROOT
from app.rules.engine import EDU_ORDER, OPS

SCHEMES_DIR = ROOT / "rulebook" / "schemes"


class SchemeError(ValueError):
    pass


class Bilingual(BaseModel):
    en: str
    hi: str


class Benefit(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["subsidy", "loan", "guarantee"]
    text: Bilingual | None = None
    verified: bool | None = None
    source_note: str | None = None


class Rule(BaseModel):
    """A top-level eligibility rule: an id, provenance, and one node (leaf/all/any/when)."""

    model_config = ConfigDict(extra="allow")
    id: str
    page: int | None = None
    verified: bool
    source_note: str


class Scheme(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: Bilingual
    level: Literal["central", "state"]
    state: str | None = None
    authority: str | None = None
    benefit: Benefit
    apply_mode: Literal["portal", "bank", "office"]
    portal_url: str | None = None
    status_mode: Literal["user_forwards", "manual", "scrape"]
    window: Any = None
    documents: list[str] = Field(default_factory=list)
    source_pdf: str | None = None
    last_verified: date | None = None
    eligibility: dict
    benefit_calc: dict[str, list[dict]] | None = None

    @model_validator(mode="after")
    def check_rules(self) -> "Scheme":
        if self.level == "state" and not self.state:
            raise ValueError("state-level scheme needs `state`")
        rules = self.eligibility.get("all")
        if not isinstance(rules, list) or not rules:
            raise ValueError("eligibility.all must be a non-empty list")
        ids = [Rule.model_validate(r).id for r in rules]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate rule ids")
        for r in rules:
            check_node(r, r["id"])
        for key, entries in (self.benefit_calc or {}).items():
            check_calc_entries(key, entries)
        return self


NODE_META = {"id", "page", "verified", "source_note"}


def check_node(node: Any, where: str) -> None:
    if not isinstance(node, dict):
        raise TypeError(f"{where}: node must be a mapping")
    keys = set(node) - NODE_META
    if keys == {"all"} or keys == {"any"}:
        children = node[next(iter(keys))]
        if not isinstance(children, list) or not children:
            raise ValueError(f"{where}: {keys} needs a non-empty list")
        for child in children:
            check_node(child, where)
    elif keys == {"when", "then"}:
        check_node(node["when"], where)
        check_node(node["then"], where)
    elif keys == {"fact", "op", "value"}:
        if node["op"] not in OPS:
            raise ValueError(f"{where}: unknown op {node['op']!r}")
        if node["op"] in ("in", "not_in") and not isinstance(node["value"], list):
            raise ValueError(f"{where}: {node['op']} needs a list value")
        if node["op"] == "gte_level" and node["value"] not in EDU_ORDER:
            raise ValueError(f"{where}: unknown education level {node['value']!r}")
    else:
        raise ValueError(f"{where}: unrecognised node keys {sorted(keys)}")


def check_calc_entries(key: str, entries: list[dict]) -> None:
    for i, entry in enumerate(entries):
        where = f"benefit_calc.{key}[{i}]"
        if "verified" not in entry or "source_note" not in entry:
            raise ValueError(f"{where}: needs verified and source_note")
        if "default" in entry:
            if i != len(entries) - 1:
                raise ValueError(f"{where}: default must be last")
        else:
            if "when" not in entry or "value" not in entry:
                raise ValueError(f"{where}: needs when + value, or default")
            check_node(entry["when"], where)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_scheme(path: Path) -> dict:
    """Parse and validate one scheme file; returns the plain dict plus `_hash` and `_path`."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    try:
        Scheme.model_validate(raw)
    except (ValueError, TypeError) as exc:
        raise SchemeError(f"{path.name}: {exc}") from exc
    if raw["id"] != path.stem:
        raise SchemeError(f"{path.name}: id {raw['id']!r} must match file name")
    return {**raw, "_hash": file_hash(path), "_path": str(path)}


def load_schemes(schemes_dir: Path = SCHEMES_DIR) -> list[dict]:
    """All schemes, sorted by file name so evaluation order is stable."""
    return [load_scheme(p) for p in sorted(schemes_dir.glob("*.yaml"))]
