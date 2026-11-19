"""Safe fetch of one curated official page. Plain code: no model, and page content is never executed.

Rules: https on port 443 only; the host must be in the scheme's allowlist; every resolved address must be public
(no private, loopback, link-local (incl. 169.254.169.254), multicast, reserved or unspecified). The connection is
pinned to the validated IP, with the real host sent as Host and TLS SNI. Redirects are followed by hand (max 3), and
each hop is checked again. robots.txt is honoured for the product token "SchemeHunter". 8 s in total, one retry,
1 request per second per host, 1 MB download cap, text/html or text/plain only.
"""

import hashlib
import ipaddress
import re
import socket
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup

USER_AGENT = "SchemeHunter/1.0 (+https://schemehunter.com; support@schemehunter.com)"
ROBOTS_TOKEN = "SchemeHunter"
TOTAL_TIMEOUT_S = 8.0
RETRIES = 1
MAX_REDIRECTS = 3
MAX_BYTES = 1_000_000
MAX_TEXT = 12_000
MIN_INTERVAL_S = 1.0
ALLOWED_TYPES = ("text/html", "text/plain")
# 169.254.169.254 (cloud metadata) is inside link-local, but it is listed here too so the intent is explicit.
BLOCKED_NETWORKS = [ipaddress.ip_network("169.254.169.254/32")]

_transport: httpx.BaseTransport | None = None  # tests inject a fake; None means the real network
_rate_lock = threading.Lock()
_last_request: dict[str, float] = {}


class FetchError(Exception):
    """Any refusal or failure. The message is short and safe to show on /admin/web."""


@dataclass
class Page:
    url: str
    title: str
    text: str
    sha256: str
    fetched_at: datetime
    status: int


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = transport


def _default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def public_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if any(addr in net for net in BLOCKED_NETWORKS):
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def check_url(url: str, allowed_hosts: set[str]) -> str:
    """Return the lower-case host if the URL is https on port 443 (or none), with no credentials, in the allowlist."""
    u = urlsplit(url)
    if u.scheme != "https":
        raise FetchError("refused: not https")
    if u.port not in (None, 443) or u.username or u.password:
        raise FetchError("refused: port or credentials")
    host = (u.hostname or "").lower()
    if not host or host not in allowed_hosts:
        raise FetchError(f"refused: host {host or '?'} is not in this scheme's list")
    return host


def pinned_ip(host: str, resolver=None) -> str:
    ips = (resolver or _default_resolver)(host)
    if not ips:
        raise FetchError("refused: host did not resolve")
    if not all(public_address(ip) for ip in ips):
        raise FetchError("refused: host resolves to a non-public address")
    return ips[0]


def _throttle(host: str, clock, sleep) -> None:
    with _rate_lock:
        wait = _last_request.get(host, float("-inf")) + MIN_INTERVAL_S - clock()
        _last_request[host] = clock() + max(wait, 0.0)  # the slot we will use
    if wait > 0:
        sleep(wait)


def _pinned_request(client: httpx.Client, url: str, host: str, ip: str) -> httpx.Request:
    u = urlsplit(url)
    target = f"https://[{ip}]" if ":" in ip else f"https://{ip}"
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    return client.build_request("GET", target + path, headers={"Host": host}, extensions={"sni_hostname": host})


def _once(url: str, host: str, ip: str, deadline: float, clock, sleep, attempt: int):
    """One request. Returns (status, location, content_type, body). Raises FetchError on network failure."""
    left = deadline - clock()
    if left <= 0:
        raise FetchError("timed out")
    _throttle(host, clock, sleep)
    client = httpx.Client(
        transport=_transport,
        timeout=max(0.5, left),
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
    )
    try:
        with client:
            resp = client.send(_pinned_request(client, url, host, ip), stream=True)
            try:
                if 300 <= resp.status_code < 400 and resp.headers.get("location"):
                    return resp.status_code, resp.headers["location"], "", b""
                if resp.status_code >= 500 and attempt < RETRIES:
                    return resp.status_code, None, "retry", b""
                ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
                body = bytearray()
                for chunk in resp.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise FetchError("too large (over 1 MB)")
                return resp.status_code, None, ctype, bytes(body)
            finally:
                resp.close()
    except httpx.TimeoutException as exc:
        raise FetchError("timed out") from exc
    except httpx.TransportError as exc:
        raise FetchError(f"network error: {type(exc).__name__}") from exc


def _get(url: str, allowed: set[str], deadline: float, clock, sleep, resolver):
    """GET with validated redirects (max MAX_REDIRECTS) and one retry. Returns (status, content_type, body, final_url)."""
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        host = check_url(current, allowed)
        ip = pinned_ip(host, resolver)
        for attempt in range(RETRIES + 1):
            status, location, ctype, body = _once(current, host, ip, deadline, clock, sleep, attempt)
            if ctype != "retry":
                break
        if location:
            current = urljoin(current, location)
            continue
        return status, ctype, body, current
    raise FetchError("too many redirects")


def robots_allows(host: str, path_url: str, deadline: float, clock, sleep, resolver) -> bool:
    """A 404 or a non-text/plain response means no rules. Otherwise Disallow for our product token is honoured."""
    status, ctype, body, _ = _get(f"https://{host}/robots.txt", {host}, deadline, clock, sleep, resolver)
    if status != 200 or ctype != "text/plain":
        return True
    parser = robotparser.RobotFileParser()
    parser.parse(body.decode("utf-8", errors="replace").splitlines())
    return parser.can_fetch(ROBOTS_TOKEN, path_url)


_CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]")
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)
DROP_TAGS = ["script", "style", "nav", "header", "footer", "form", "noscript", "svg", "iframe", "template"]
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "blockquote", "dt", "dd", "caption", "figcaption"}


def _clean(line: str) -> str:
    line = unicodedata.normalize("NFC", _CONTROL.sub("", line))
    return re.sub(r"\s+", " ", line).strip()


def _is_hidden(tag) -> bool:
    if tag.has_attr("hidden"):
        return True
    if tag.name == "input" and (tag.get("type") or "").lower() == "hidden":
        return True
    return bool(_HIDDEN_STYLE.search(tag.get("style") or ""))


def clean_lines(lines: list[str], skip_text: tuple[str, ...] | list[str] = ()) -> list[str]:
    """Drop menu and boilerplate lines, then short leading lines before the first real sentence.

    skip_text entries match a whole line, or the start of a line for long boilerplate (case-insensitive).
    A line of 1-3 words before the first line of 6+ words is dropped (menu labels in plain divs).
    """
    skips = [_clean(x).casefold() for x in skip_text if _clean(x)]

    def skipped(line: str) -> bool:
        low = line.casefold()
        return any(low == k or (len(k) > 25 and low.startswith(k)) for k in skips)

    kept = [line for line in lines if not skipped(line)]
    first_long = next((i for i, line in enumerate(kept) if len(line.split()) >= 6), None)
    if first_long is not None:
        kept = [line for line in kept[:first_long] if len(line.split()) > 3] + kept[first_long:]
    return kept


def html_to_text(html: str, skip_text: tuple[str, ...] | list[str] = ()) -> tuple[str, str]:
    """(title, text). Headings, paragraphs, list items and table rows become lines. Nothing is executed."""
    soup = BeautifulSoup(html, "lxml")
    title = _clean(soup.title.get_text(" ")) if soup.title else ""
    for tag in soup(DROP_TAGS):
        tag.extract()
    for tag in [t for t in soup.find_all(True) if _is_hidden(t)]:
        tag.extract()
    lines: list[str] = []
    for el in soup.find_all(list(BLOCK_TAGS) + ["tr"]):
        if any(p.name in BLOCK_TAGS for p in el.parents):
            continue  # its text is already in an outer block
        if el.name == "tr":
            cells = [_clean(c.get_text(" ")) for c in el.find_all(["td", "th"], recursive=False)]
            line = " | ".join(c for c in cells if c)
        else:
            line = _clean(el.get_text(" "))
        if line:
            lines.append(line)
    if not lines:
        lines = [x for x in (_clean(line) for line in soup.get_text("\n").splitlines()) if x]
    return title, "\n".join(clean_lines(lines, skip_text))[:MAX_TEXT]


def fetch_page(
    url: str,
    allowed_hosts: set[str],
    *,
    skip_text: tuple[str, ...] | list[str] = (),
    resolver=None,
    clock=time.monotonic,
    sleep=time.sleep,
) -> Page:
    """Fetch one curated page under every rule above. Raises FetchError on any refusal or failure."""
    deadline = clock() + TOTAL_TIMEOUT_S
    host = check_url(url, allowed_hosts)
    if not robots_allows(host, urlsplit(url).path or "/", deadline, clock, sleep, resolver):
        raise FetchError("refused: robots.txt disallows this page")
    status, ctype, body, final = _get(url, allowed_hosts, deadline, clock, sleep, resolver)
    if status != 200:
        raise FetchError(f"status {status}")
    if ctype not in ALLOWED_TYPES:
        raise FetchError(f"refused: content type {ctype or 'missing'}")
    raw = body.decode("utf-8", errors="replace")
    if ctype == "text/html":
        title, text = html_to_text(raw, skip_text)
    else:
        lines = [x for x in (_clean(line) for line in raw.splitlines()) if x]
        title, text = "", "\n".join(clean_lines(lines, skip_text))[:MAX_TEXT]
    if not text:
        raise FetchError("no readable text on the page")
    return Page(
        url=final,
        title=title,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        fetched_at=datetime.now(UTC),
        status=status,
    )
