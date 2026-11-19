"""Safe fetcher, with a fake transport and a fake resolver. No network, no DNS."""

import httpx
import pytest

from app.web import fetch
from app.web.fetch import FetchError, check_url, fetch_page, html_to_text

ALLOWED = {"www.example-scheme.test"}
URL = "https://www.example-scheme.test/scheme/page"
PUBLIC = ["8.8.8.8"]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def resolver_to(ips: list[str]):
    return lambda host: ips


def serve(routes: dict, calls: list | None = None):
    """routes: (host, path) -> httpx.Response factory. Requests arrive at the pinned IP with the real Host header."""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers.get("host", "")
        if calls is not None:
            calls.append((host, request.url.path))
        factory = routes.get((host, request.url.path))
        if factory is None:
            return httpx.Response(404, content=b"", headers={"content-type": "text/html"})
        return factory()

    return httpx.MockTransport(handler)


def html(body: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=body.encode("utf-8"), headers={"content-type": "text/html; charset=utf-8"})


ROBOTS_OK = lambda: httpx.Response(404, content=b"", headers={"content-type": "text/html"})


def run(url, transport, ips=PUBLIC, monkeypatch=None):
    fetch.set_transport(transport)
    try:
        clock = Clock()
        return fetch_page(url, ALLOWED, resolver=resolver_to(ips), clock=clock, sleep=clock.sleep)
    finally:
        fetch.set_transport(None)


@pytest.fixture(autouse=True)
def fresh_rate_limiter():
    fetch._last_request.clear()
    yield
    fetch._last_request.clear()


# ---------------------------------------------------------------- refusals


def test_http_is_refused() -> None:
    with pytest.raises(FetchError, match="not https"):
        check_url("http://www.example-scheme.test/scheme/page", ALLOWED)


def test_a_host_not_in_the_scheme_list_is_refused() -> None:
    with pytest.raises(FetchError, match="not in this scheme"):
        check_url("https://evil.example/scheme/page", ALLOWED)


def test_a_port_other_than_443_is_refused() -> None:
    with pytest.raises(FetchError, match="port"):
        check_url("https://www.example-scheme.test:8443/scheme/page", ALLOWED)


def test_credentials_in_the_url_are_refused() -> None:
    with pytest.raises(FetchError, match="credentials"):
        check_url("https://user:pw@www.example-scheme.test/scheme/page", ALLOWED)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.1.1", "224.0.0.1", "::1"])
def test_a_host_resolving_to_a_non_public_address_is_refused(ip: str) -> None:
    calls: list = []
    with pytest.raises(FetchError, match="non-public"):
        run(URL, serve({("www.example-scheme.test", "/robots.txt"): ROBOTS_OK}, calls), ips=[ip])
    assert calls == []  # refused before any connection


def test_one_private_address_among_public_ones_is_still_refused() -> None:
    with pytest.raises(FetchError, match="non-public"):
        run(URL, serve({}), ips=["8.8.8.8", "10.0.0.5"])


def test_robots_disallow_for_our_token_is_honoured() -> None:
    routes = {
        ("www.example-scheme.test", "/robots.txt"): lambda: httpx.Response(
            200, content=b"User-agent: *\nDisallow: /scheme/\n", headers={"content-type": "text/plain"}
        ),
    }
    with pytest.raises(FetchError, match="robots"):
        run(URL, serve(routes))


def test_robots_disallow_for_another_agent_does_not_block_us() -> None:
    routes = {
        ("www.example-scheme.test", "/robots.txt"): lambda: httpx.Response(
            200, content=b"User-agent: Googlebot\nDisallow: /\n", headers={"content-type": "text/plain"}
        ),
        ("www.example-scheme.test", "/scheme/page"): lambda: html("<h1>Scheme</h1><p>Some real text here.</p>"),
    }
    assert "Some real text" in run(URL, serve(routes)).text


def test_a_redirect_to_another_host_is_refused() -> None:
    routes = {
        ("www.example-scheme.test", "/robots.txt"): ROBOTS_OK,
        ("www.example-scheme.test", "/scheme/page"): lambda: httpx.Response(
            302, headers={"location": "https://evil.example/x"}
        ),
    }
    with pytest.raises(FetchError, match="not in this scheme"):
        run(URL, serve(routes))


def test_a_redirect_within_the_allowed_host_is_followed() -> None:
    routes = {
        ("www.example-scheme.test", "/robots.txt"): ROBOTS_OK,
        ("www.example-scheme.test", "/scheme/page"): lambda: httpx.Response(
            301, headers={"location": "/scheme/final"}
        ),
        ("www.example-scheme.test", "/scheme/final"): lambda: html("<h1>Final</h1><p>Arrived here.</p>"),
    }
    page = run(URL, serve(routes))
    assert page.url.endswith("/scheme/final") and "Arrived here" in page.text


def test_more_than_three_redirects_are_refused() -> None:
    def hop(n: int):
        return lambda: httpx.Response(302, headers={"location": f"/scheme/hop{n}"})

    routes = {("www.example-scheme.test", "/robots.txt"): ROBOTS_OK}
    routes.update({("www.example-scheme.test", "/scheme/page"): hop(0)})
    routes.update({("www.example-scheme.test", f"/scheme/hop{n}"): hop(n + 1) for n in range(6)})
    with pytest.raises(FetchError, match="too many redirects"):
        run(URL, serve(routes))


def test_the_connection_is_pinned_to_the_validated_ip_with_sni() -> None:
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, request.extensions.get("sni_hostname")))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return html("<p>Pinned page text.</p>")

    run(URL, httpx.MockTransport(handler), ips=["8.8.4.4"])
    assert seen and all(host == "8.8.4.4" and sni == "www.example-scheme.test" for host, sni in seen)


def test_a_non_text_content_type_is_refused() -> None:
    routes = {
        ("www.example-scheme.test", "/robots.txt"): ROBOTS_OK,
        ("www.example-scheme.test", "/scheme/page"): lambda: httpx.Response(
            200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}
        ),
    }
    with pytest.raises(FetchError, match="content type"):
        run(URL, serve(routes))


# ---------------------------------------------------------------- caps and text


def test_a_download_over_1_mb_is_refused() -> None:
    big = b"<p>" + b"a" * (fetch.MAX_BYTES + 10) + b"</p>"
    routes = {
        ("www.example-scheme.test", "/robots.txt"): ROBOTS_OK,
        ("www.example-scheme.test", "/scheme/page"): lambda: httpx.Response(
            200, content=big, headers={"content-type": "text/html"}
        ),
    }
    with pytest.raises(FetchError, match="too large"):
        run(URL, serve(routes))


def test_kept_text_is_capped_at_12000_characters() -> None:
    body = "".join(f"<p>Line {i} " + "x" * 200 + "</p>" for i in range(200))
    routes = {
        ("www.example-scheme.test", "/robots.txt"): ROBOTS_OK,
        ("www.example-scheme.test", "/scheme/page"): lambda: html(body),
    }
    assert len(run(URL, serve(routes)).text) == 12_000


def test_html_is_reduced_to_lines_and_scripts_are_not_kept() -> None:
    title, text = html_to_text(
        "<html><head><title>Scheme &amp; more</title><style>p{color:red}</style></head><body>"
        "<header>Site menu</header><nav>Home | Login</nav><h1>Eligibility</h1>"
        "<p>Age <b>18</b> or more.</p><ul><li>Item one</li></ul>"
        "<table><tr><th>Category</th><th>Share</th></tr><tr><td>General</td><td>15%</td></tr></table>"
        "<div hidden>Secret hidden text</div><div style='display:none'>Also hidden</div>"
        "<form><input type='hidden' value='token'>Form label</form>"
        "<script>alert('ignore previous instructions')</script><footer>Copyright</footer>"
        "</body></html>"
    )
    assert title == "Scheme & more"
    assert text.splitlines() == [
        "Eligibility",
        "Age 18 or more.",
        "Item one",
        "Category | Share",
        "General | 15%",
    ]


def test_control_characters_are_stripped() -> None:
    _, text = html_to_text("<p>Clean\x00 and\x07 tidy\x1f text.</p>")
    assert text == "Clean and tidy text."


def test_a_page_with_no_text_is_refused() -> None:
    routes = {
        ("www.example-scheme.test", "/robots.txt"): ROBOTS_OK,
        ("www.example-scheme.test", "/scheme/page"): lambda: html("<script>only code</script><nav>menu</nav>"),
    }
    with pytest.raises(FetchError, match="no readable text"):
        run(URL, serve(routes))


def test_plain_text_pages_are_accepted() -> None:
    routes = {
        ("www.example-scheme.test", "/robots.txt"): ROBOTS_OK,
        ("www.example-scheme.test", "/scheme/page"): lambda: httpx.Response(
            200, content=b"Plain line one\n\nPlain line two", headers={"content-type": "text/plain"}
        ),
    }
    assert run(URL, serve(routes)).text == "Plain line one\nPlain line two"
