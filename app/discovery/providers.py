"""Scheme providers (integration §2, §5): SchemeProvider → MySchemeApiProvider (live) or DemoSchemeProvider (labelled).

The live client takes every endpoint detail from configuration. Nothing about the myScheme API is hard-coded,
because the official documentation has not been confirmed. Until the path, the query-field names, the auth header
names and the response field names are configured, the live client refuses to run (NotConfigured) instead of
guessing. Credentials stay in backend settings and are never returned to the browser.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from app.config import get_settings
from app.discovery.models import SOURCE_DEMO, SOURCE_MYSCHEME, NormalizedScheme
from app.rules.loader import load_schemes

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Base for everything a provider can report. The message is safe to show: it holds no credentials."""

    code = "provider_error"


class NotConfigured(ProviderError):
    code = "not_configured"


class AuthFailed(ProviderError):
    code = "auth_failed"


class RateLimited(ProviderError):
    code = "rate_limited"

    def __init__(self, retry_after: int | None = None):
        super().__init__("The scheme service is busy. Try again shortly.")
        self.retry_after = retry_after


class Unavailable(ProviderError):
    code = "unavailable"


class InvalidResponse(ProviderError):
    code = "invalid_response"


@dataclass
class SearchResult:
    schemes: list[NormalizedScheme]
    source: str  # SOURCE_MYSCHEME or SOURCE_DEMO
    retrieved_at: datetime | None
    skipped: int = 0  # items dropped because they had no usable name


class SchemeProvider(Protocol):
    def search(self, profile: dict[str, Any]) -> SearchResult: ...


def _clean(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    t = re.sub(r"\s+", " ", text).strip()
    return t or None


# ---------------------------------------------------------------- live client


class MySchemeApiProvider:
    """Live client. Every endpoint detail comes from settings (MYSCHEME_*). Unset → NotConfigured."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.s = get_settings()
        self.transport = transport

    def missing_settings(self) -> list[str]:
        s = self.s
        need = {
            "MYSCHEME_BASE_URL": s.MYSCHEME_BASE_URL,
            "MYSCHEME_SEARCH_PATH": s.MYSCHEME_SEARCH_PATH,
            "MYSCHEME_CLIENT_ID": s.MYSCHEME_CLIENT_ID,
            "MYSCHEME_API_KEY": s.MYSCHEME_API_KEY,
            "MYSCHEME_CLIENT_ID_HEADER": s.MYSCHEME_CLIENT_ID_HEADER,
            "MYSCHEME_API_KEY_HEADER": s.MYSCHEME_API_KEY_HEADER,
            "MYSCHEME_QUERY_MAP": s.MYSCHEME_QUERY_MAP,
            "MYSCHEME_RESULTS_KEY": s.MYSCHEME_RESULTS_KEY,
            "MYSCHEME_FIELD_MAP": s.MYSCHEME_FIELD_MAP,
        }
        return [k for k, v in need.items() if not v]

    def search(self, profile: dict[str, Any]) -> SearchResult:
        missing = self.missing_settings()
        if missing:
            raise NotConfigured("Live myScheme is not configured yet: " + ", ".join(missing))
        query_map = _json_setting("MYSCHEME_QUERY_MAP", self.s.MYSCHEME_QUERY_MAP)
        field_map = _json_setting("MYSCHEME_FIELD_MAP", self.s.MYSCHEME_FIELD_MAP)
        params = {query_map[k]: v for k, v in profile.items() if k in query_map and v not in (None, "")}
        timeout = httpx.Timeout(
            connect=self.s.MYSCHEME_CONNECT_TIMEOUT_S,
            read=self.s.MYSCHEME_READ_TIMEOUT_S,
            write=5.0,
            pool=5.0,
        )
        headers = {
            self.s.MYSCHEME_CLIENT_ID_HEADER: self.s.MYSCHEME_CLIENT_ID,
            self.s.MYSCHEME_API_KEY_HEADER: self.s.MYSCHEME_API_KEY,
            "Accept": "application/json",
        }
        url = self.s.MYSCHEME_BASE_URL.rstrip("/") + "/" + self.s.MYSCHEME_SEARCH_PATH.lstrip("/")
        try:
            with httpx.Client(transport=self.transport, timeout=timeout) as client:
                resp = client.get(url, params=params, headers=headers)
        except httpx.TimeoutException:
            raise Unavailable("The scheme service did not answer in time.") from None
        except httpx.HTTPError as exc:
            raise Unavailable(f"The scheme service could not be reached ({type(exc).__name__}).") from None
        if resp.status_code in (401, 403):
            raise AuthFailed("The scheme service rejected our credentials.")
        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After")
            raise RateLimited(int(retry) if retry and retry.isdigit() else None)
        if resp.status_code >= 500:
            raise Unavailable("The scheme service is having problems right now.")
        if resp.status_code != 200:
            raise InvalidResponse(f"Unexpected answer from the scheme service (status {resp.status_code}).")
        try:
            body = resp.json()
        except ValueError:
            raise InvalidResponse("The scheme service sent something that is not JSON.") from None
        return normalize_live(body, field_map, self.s.MYSCHEME_RESULTS_KEY)


def _json_setting(name: str, raw: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except ValueError:
        raise NotConfigured(f"{name} is not valid JSON") from None
    if not isinstance(value, dict) or not all(isinstance(v, str) and v for v in value.values()):
        raise NotConfigured(f"{name} must map names to non-empty strings")
    return value


def normalize_live(body: Any, field_map: dict[str, str], results_key: str) -> SearchResult:
    """Validate the envelope and each item; keep only items with a usable name. Nothing is invented."""
    if not isinstance(body, dict) or not isinstance(body.get(results_key), list):
        raise InvalidResponse("The scheme service's answer did not have the expected list.")
    now = datetime.now(UTC)
    out: list[NormalizedScheme] = []
    skipped = 0
    for item in body[results_key]:
        if not isinstance(item, dict):
            skipped += 1
            continue
        name = _clean(item.get(field_map.get("name", "")))
        if not name:
            skipped += 1
            continue
        docs = item.get(field_map.get("documents", "")) if field_map.get("documents") else None
        key = _clean(item.get(field_map.get("id", ""))) or name
        out.append(
            NormalizedScheme(
                key=key,
                name=name,
                source=SOURCE_MYSCHEME,
                department=_clean(item.get(field_map.get("department", ""))),
                description=_clean(item.get(field_map.get("description", ""))),
                benefits=_clean(item.get(field_map.get("benefits", ""))),
                eligibility_summary=_clean(item.get(field_map.get("eligibility", ""))),
                documents=[
                    d for d in (docs if isinstance(docs, list) else []) if isinstance(d, str) and d.strip()
                ],
                official_url=_clean(item.get(field_map.get("url", ""))),
                retrieved_at=now,
            )
        )
    return SearchResult(schemes=out, source=SOURCE_MYSCHEME, retrieved_at=now, skipped=skipped)


# ---------------------------------------------------------------- demo provider


class DemoSchemeProvider:
    """The seeded rulebook, shown as demo data. Never labelled live, and it has no retrieval time."""

    def search(self, profile: dict[str, Any]) -> SearchResult:
        out = []
        for s in load_schemes():
            docs = [d for d in (s.get("documents") or []) if "_if_" not in d]
            out.append(
                NormalizedScheme(
                    key=s["id"],
                    name=s["name"]["en"],
                    source=SOURCE_DEMO,
                    department=s.get("authority"),
                    description=(s.get("benefit", {}).get("text") or {}).get("en"),
                    benefits=(s.get("benefit", {}).get("text") or {}).get("en"),
                    eligibility_summary=None,  # the rules engine decides; the demo never claims a summary
                    documents=docs,
                    official_url=s.get("portal_url"),
                    retrieved_at=None,
                    internal_id=s["id"],
                    aliases=list(s.get("aliases") or []),
                )
            )
        return SearchResult(schemes=out, source=SOURCE_DEMO, retrieved_at=None)


# ---------------------------------------------------------------- choosing the provider


def get_provider(mode: str | None = None) -> tuple[SchemeProvider, str | None]:
    """Returns (provider, note). The note explains a fallback; it is shown to the user, never hidden."""
    mode = mode or get_settings().DISCOVERY_MODE
    if mode == "demo":
        return DemoSchemeProvider(), None
    live = MySchemeApiProvider()
    missing = live.missing_settings()
    if missing:
        return DemoSchemeProvider(), "Live myScheme is not configured yet, so this is demo data."
    return live, None
