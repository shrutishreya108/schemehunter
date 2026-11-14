from pathlib import Path

import pytest
import yaml

from app.guard.scanner import scan
from app.rules.loader import load_schemes

PHRASES = yaml.safe_load((Path(__file__).parent / "phrases.yaml").read_text(encoding="utf-8"))
SCHEMES = load_schemes()


def test_fixture_sizes() -> None:
    assert len(PHRASES["banned"]) == 20
    assert len(PHRASES["invented_schemes"]) == 5


@pytest.mark.parametrize("text", PHRASES["banned"])
def test_banned_phrases_caught(text: str) -> None:
    result = scan(text, SCHEMES)
    assert not result["ok"] and result["hits"]


@pytest.mark.parametrize("text", PHRASES["invented_schemes"])
def test_invented_schemes_caught(text: str) -> None:
    result = scan(text, SCHEMES)
    assert not result["ok"] and result["unknown_schemes"], result


@pytest.mark.parametrize("text", PHRASES["clean"])
def test_clean_text_passes(text: str) -> None:
    assert scan(text, SCHEMES) == {"ok": True, "hits": [], "unknown_schemes": []}


def test_pluggable_extractor() -> None:
    """At runtime the Brain injects a MODEL_CHEAP extractor; the guard just subtracts known names."""
    fake_llm = lambda body: ["PMEGP", "Super Loan Yojana"]
    assert scan("anything", SCHEMES, extractor=fake_llm)["unknown_schemes"] == ["Super Loan Yojana"]


def test_blanking_names_does_not_hide_promises() -> None:
    """Only exact Rule Book names/aliases are blanked; a promise next to one still trips."""
    for text in (
        "we guarantee approval",
        "Under CGTMSE we guarantee approval",
        "Credit Guarantee Fund Trust for Micro and Small Enterprises (CGTMSE): we guarantee approval",
        "प्रधानमंत्री मुद्रा योजना में लोन पक्का है",
    ):
        assert scan(text, SCHEMES)["hits"], text
