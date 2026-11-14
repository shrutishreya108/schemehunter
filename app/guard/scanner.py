"""Second guard, in code (LLD §11): scan every outbound body before it becomes a pending action.

- Banned promise phrases (English + Hindi).
- Scheme names not in the Rule Book.

Unknown-scheme detection is pluggable: the Brain passes a MODEL_CHEAP-backed extractor at runtime
(this module must not import anthropic). The default extractor is a deterministic regex that catches
"<Words> Yojana/Scheme/Programme/Mission" and "... योजना" mentions, so the guard still works offline.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable

BANNED = [
    r"\bguarantee",
    r"\bwill be approved",
    r"\byou will (get|receive)\b",
    r"निश्चित",
    r"पक्का",
    r"गारंटी",
    r"ज़रूर मिलेगा",
]

SchemeExtractor = Callable[[str], list[str]]

_LATIN_SCHEME = re.compile(
    r"\b((?:[A-Z][\w-]*\s+){1,6}(?:Yojana|Yojna|Scheme|Programme|Program|Mission|Abhiyan))\b"
)
_HINDI_SCHEME = re.compile(r"((?:[\u0900-\u097F]+\s+){1,5}योजना)")
_FILLER = {"the", "a", "an", "this", "that", "our", "your", "any", "under", "for", "of", "new", "government"}


def normalise(name: str) -> str:
    return " ".join(re.sub(r"[^\w\s\u0900-\u097F]", " ", name.casefold()).split())


def strip_filler(name: str) -> str:
    words = normalise(name).split()
    while words and words[0] in _FILLER:
        words.pop(0)
    return " ".join(words)


def regex_scheme_mentions(body: str) -> list[str]:
    """Deterministic fallback extractor for scheme-like names."""
    found = [m.group(1).strip() for m in _LATIN_SCHEME.finditer(body)]
    found += [m.group(1).strip() for m in _HINDI_SCHEME.finditer(body)]
    return found


def known_names(schemes: Iterable[dict]) -> set[str]:
    """Every name and alias from the Rule Book, normalised."""
    names: set[str] = set()
    for s in schemes:
        for value in (s["name"]["en"], s["name"]["hi"], *s.get("aliases", [])):
            names.add(normalise(value))
            names.add(normalise(re.sub(r"\(.*?\)", " ", value)))  # "Udyogini (Karnataka)" → "udyogini"
    names.discard("")
    return names


def is_known(mention: str, names: set[str]) -> bool:
    m = strip_filler(mention)
    for suffix in ("", " yojana", " scheme", " programme", " योजना"):
        base = m.removesuffix(suffix.strip()).strip() if suffix else m
        if base in names:
            return True
    return any(name in m for name in names if len(name) >= 4)


def unknown_scheme_mentions(
    body: str, names: set[str], extractor: SchemeExtractor = regex_scheme_mentions
) -> list[str]:
    return [m for m in extractor(body) if not is_known(m, names)]


def remove_known_names(body: str, schemes: Iterable[dict]) -> str:
    """Blank out Rule Book names so "Credit Guarantee Fund Trust" doesn't trip the "guarantee" rule."""
    raw = sorted(
        {v for s in schemes for v in (s["name"]["en"], s["name"]["hi"], *s.get("aliases", []))},
        key=len,
        reverse=True,
    )
    for value in raw:
        body = re.sub(re.escape(value), " ", body, flags=re.IGNORECASE)
    return body


def banned_hits(body: str) -> list[str]:
    """NFC on both sides so nukta forms (ज़ vs ज + ़) compare equal."""
    text = unicodedata.normalize("NFC", body)
    return [p for p in BANNED if re.search(unicodedata.normalize("NFC", p), text, re.IGNORECASE)]


def scan(body: str, schemes: Iterable[dict], extractor: SchemeExtractor = regex_scheme_mentions) -> dict:
    """{"ok", "hits", "unknown_schemes"}. Fail → reasons go back to Claude for a rewrite (max 2)."""
    schemes = list(schemes)
    hits = banned_hits(remove_known_names(body, schemes))
    unknown = unknown_scheme_mentions(body, known_names(schemes), extractor)
    return {"ok": not hits and not unknown, "hits": hits, "unknown_schemes": unknown}
