"""Scheme discovery (integration §7): mocked HTTP only. No test in this file reaches the network.

Endpoint names, header names and field names used below are TEST VALUES set through settings. They are not the
myScheme API's real names, which have not been confirmed.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.access import SESSION_COOKIE, csrf_token, sign_session
from app.config import get_settings
from app.discovery import matching
from app.discovery.models import SOURCE_DEMO, SOURCE_MYSCHEME, NormalizedScheme
from app.discovery.providers import (
    AuthFailed,
    DemoSchemeProvider,
    InvalidResponse,
    MySchemeApiProvider,
    NotConfigured,
    RateLimited,
    Unavailable,
    get_provider,
    normalize_live,
)
from app.rules.loader import load_schemes

RULEBOOK = load_schemes()
SECRET = "test-secret-key-do-not-leak"
FIELD_MAP = {
    "name": "title",
    "id": "code",
    "department": "dept",
    "description": "desc",
    "benefits": "benefit",
    "eligibility": "elig",
    "documents": "docs",
    "url": "link",
}


@pytest.fixture
def live_settings(monkeypatch):
    values = {
        "MYSCHEME_BASE_URL": "https://api.example.test",
        "MYSCHEME_SEARCH_PATH": "/schemes",
        "MYSCHEME_CLIENT_ID": "test-client",
        "MYSCHEME_API_KEY": SECRET,
        "MYSCHEME_CLIENT_ID_HEADER": "X-Test-Client",
        "MYSCHEME_API_KEY_HEADER": "X-Test-Key",
        "MYSCHEME_QUERY_MAP": json.dumps({"state_code": "state", "gender": "gender"}),
        "MYSCHEME_RESULTS_KEY": "data",
        "MYSCHEME_FIELD_MAP": json.dumps(FIELD_MAP),
    }
    for k, v in values.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield values
    get_settings.cache_clear()


def transport(status=200, body=None, raw=None, exc=None, seen=None, headers=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if exc is not None:
            raise exc
        if raw is not None:
            return httpx.Response(status, content=raw, headers=headers)
        return httpx.Response(status, json=body if body is not None else {}, headers=headers)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------- authentication and configuration


def test_without_configuration_the_live_client_refuses_to_run(monkeypatch) -> None:
    for k in ("MYSCHEME_BASE_URL", "MYSCHEME_SEARCH_PATH", "MYSCHEME_API_KEY"):
        monkeypatch.setenv(k, "")
    get_settings.cache_clear()
    with pytest.raises(NotConfigured):
        MySchemeApiProvider(transport=transport()).search({})
    get_settings.cache_clear()


def test_credentials_are_sent_in_the_configured_headers_only(live_settings) -> None:
    seen: list[httpx.Request] = []
    MySchemeApiProvider(transport=transport(body={"data": []}, seen=seen)).search(
        {"state_code": "KA", "dob": "1991"}
    )
    req = seen[0]
    assert req.headers["X-Test-Client"] == "test-client" and req.headers["X-Test-Key"] == SECRET
    assert req.url.params.get("state") == "KA"
    assert "dob" not in req.url.params  # only fields the API is configured to receive are sent


def test_auth_failure_is_reported_as_auth_failed(live_settings) -> None:
    for status in (401, 403):
        with pytest.raises(AuthFailed):
            MySchemeApiProvider(transport=transport(status=status)).search({})


def test_rate_limit_is_reported_with_retry_after(live_settings) -> None:
    with pytest.raises(RateLimited) as err:
        MySchemeApiProvider(transport=transport(status=429, headers={"Retry-After": "30"})).search({})
    assert err.value.retry_after == 30


def test_timeout_is_unavailable_not_a_crash(live_settings) -> None:
    with pytest.raises(Unavailable):
        MySchemeApiProvider(transport=transport(exc=httpx.ReadTimeout("slow"))).search({})


def test_server_error_is_unavailable(live_settings) -> None:
    with pytest.raises(Unavailable):
        MySchemeApiProvider(transport=transport(status=503)).search({})


def test_non_json_and_wrong_envelope_are_invalid_responses(live_settings) -> None:
    with pytest.raises(InvalidResponse):
        MySchemeApiProvider(transport=transport(raw=b"<html>nope</html>")).search({})
    with pytest.raises(InvalidResponse):
        MySchemeApiProvider(transport=transport(body={"other": []})).search({})


def test_error_messages_never_contain_the_api_key(live_settings) -> None:
    for kwargs in ({"status": 401}, {"status": 429}, {"exc": httpx.ConnectError("x")}, {"status": 500}):
        try:
            MySchemeApiProvider(transport=transport(**kwargs)).search({})
        except Exception as exc:  # noqa: BLE001 - we are checking what the exception says
            assert SECRET not in str(exc)


# ---------------------------------------------------------------- successful response and normalisation


def test_a_valid_response_is_normalised_with_a_retrieval_time(live_settings) -> None:
    body = {
        "data": [
            {
                "title": "Sample Credit Scheme",
                "code": "S1",
                "dept": "Dept A",
                "desc": "A loan.",
                "benefit": "Interest support",
                "elig": "Age 18+",
                "docs": ["Aadhaar"],
                "link": "https://x.test",
            }
        ]
    }
    out = MySchemeApiProvider(transport=transport(body=body)).search({})
    item = out.schemes[0]
    assert item.name == "Sample Credit Scheme" and item.key == "S1" and item.department == "Dept A"
    assert item.documents == ["Aadhaar"] and item.official_url == "https://x.test"
    assert item.source == SOURCE_MYSCHEME and item.retrieved_at is not None and item.live


def test_items_without_a_name_are_skipped_and_counted_not_invented(live_settings) -> None:
    body = {"data": [{"code": "no-name"}, "not a dict", {"title": "  "}, {"title": "Real One"}]}
    out = MySchemeApiProvider(transport=transport(body=body)).search({})
    assert [s.name for s in out.schemes] == ["Real One"] and out.skipped == 3


def test_missing_fields_stay_empty_not_filled_in() -> None:
    out = normalize_live({"data": [{"title": "Only A Name"}]}, FIELD_MAP, "data")
    item = out.schemes[0]
    assert item.department is None and item.benefits is None and item.documents == []
    assert item.eligibility_summary is None and item.official_url is None


# ---------------------------------------------------------------- matching and duplicates


def test_exact_names_and_aliases_match_the_rulebook() -> None:
    pmegp = next(s for s in RULEBOOK if s["id"] == "pmegp")
    by_name = NormalizedScheme(key="a", name="PM Employment Generation Programme", source=SOURCE_MYSCHEME)
    by_alias = NormalizedScheme(key="b", name="PMEGP", source=SOURCE_MYSCHEME)
    unknown = NormalizedScheme(key="c", name="Some Other Scheme", source=SOURCE_MYSCHEME)
    assert matching.match_internal(by_name, RULEBOOK) == "pmegp"
    assert matching.match_internal(by_alias, RULEBOOK) == "pmegp"
    assert matching.match_internal(unknown, RULEBOOK) is None
    assert pmegp["id"] in {s["id"] for s in RULEBOOK}


def test_duplicates_are_shown_once(live_settings) -> None:
    items = [
        NormalizedScheme(key="1", name="PMEGP", source=SOURCE_MYSCHEME),
        NormalizedScheme(key="2", name="PM Employment Generation Programme", source=SOURCE_MYSCHEME),
        NormalizedScheme(key="1", name="Something else", source=SOURCE_MYSCHEME),
    ]
    kept, dups = matching.dedupe(items, RULEBOOK)
    assert len(kept) == 1 and dups == 2 and kept[0].internal_id == "pmegp"


def test_an_ambiguous_name_is_not_matched_to_anything() -> None:
    twin = [
        {"id": "x1", "name": {"en": "Same Name", "hi": "x"}, "aliases": []},
        {"id": "x2", "name": {"en": "Same Name", "hi": "y"}, "aliases": []},
    ]
    assert (
        matching.match_internal(NormalizedScheme(key="k", name="Same Name", source=SOURCE_MYSCHEME), twin)
        is None
    )


# ---------------------------------------------------------------- demo fallback and labels


def test_demo_provider_is_labelled_and_never_claims_live_data() -> None:
    out = DemoSchemeProvider().search({})
    assert out.source == SOURCE_DEMO and out.retrieved_at is None
    assert all(not s.live for s in out.schemes)
    for s in out.schemes:
        assert "Demo data" in s.source_label("en") and "live (" not in s.source_label("en")
        assert s.eligibility_summary is None  # the demo never invents an eligibility summary


def test_unconfigured_live_mode_falls_back_to_demo_with_a_visible_note(monkeypatch) -> None:
    monkeypatch.setenv("MYSCHEME_BASE_URL", "")
    get_settings.cache_clear()
    provider, note = get_provider("live")
    assert isinstance(provider, DemoSchemeProvider) and note and "demo data" in note
    get_settings.cache_clear()


# ---------------------------------------------------------------- the pages


@pytest.fixture
def client(seeded, settings_db, monkeypatch):
    monkeypatch.setenv("DISCOVERY_MODE", "demo")
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        c.cookies.set(SESSION_COOKIE, sign_session(seeded["meena"]))
        yield c
    get_settings.cache_clear()


def test_demo_page_says_it_is_demo_data_and_shows_each_scheme(client) -> None:
    page = client.get("/discover").text
    assert "Demo data. This is not live from myScheme." in page
    assert "Source: myScheme (live)" not in page  # no false live claim
    assert "PM Employment Generation Programme" in page
    assert "Demo data: not live from myScheme" in page


def test_matched_scheme_shows_our_rules_result_with_the_preliminary_warning(client) -> None:
    page = client.get("/discover/pmegp").text
    assert "SchemeHunter&#39;s check" in page
    assert "Preliminary" in page or "not yet been checked" in page  # PMEGP rules are not verified yet


def test_check_button_runs_the_rules_engine_and_redirects(client, seeded, conn) -> None:
    csrf = csrf_token(seeded["meena"])
    r = client.post("/discover/pmegp/check", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/discover/pmegp"
    assert (
        conn.execute("select count(*) as n from matches where case_id = %s", (seeded["meena"],)).fetchone()[
            "n"
        ]
        >= 1
    )


def test_check_needs_csrf(client, seeded) -> None:
    assert client.post("/discover/pmegp/check", data={"csrf": "wrong"}).status_code == 403


def test_credentials_never_reach_the_page(seeded, settings_db, monkeypatch, live_settings) -> None:
    from app.main import app

    monkeypatch.setenv("DISCOVERY_MODE", "live")
    get_settings.cache_clear()
    with TestClient(app) as c:
        c.cookies.set(SESSION_COOKIE, sign_session(seeded["meena"]))
        page = c.get("/discover").text
    assert SECRET not in page and "test-client" not in page
    get_settings.cache_clear()


def test_profile_fields_sent_to_the_api_exclude_identifying_details(seeded, conn, live_settings) -> None:
    from app.routes.discover import discovery_profile

    out = discovery_profile(conn, seeded["meena"])
    assert "name_latin" not in out and "dob" not in out and "address_line" not in out
    assert set(out) <= {"state_code", "gender"}
