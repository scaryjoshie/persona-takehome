"""Fetch a web page's preview (title, description, image) for link cards in the thread.

Standalone: it knows nothing about users or conversations. It fetches arbitrary URLs from
our server, so it is guarded against being used to reach private networks (SSRF):
- http and https only; the host is resolved and every address it resolves to must be
  public (no private, loopback, link-local, reserved, or cloud-metadata addresses);
- redirects are followed by hand, at most 3, and each hop is checked the same way;
- a 4 s budget for the whole fetch, at most 512 KB read, only text/html is parsed, and
  reading stops at </head>;
- a plain browser-like User-Agent; no cookies or credentials are sent.

Known gap: the host is resolved for the check and again by the HTTP client when it
connects, so a DNS server that answers differently the second time could slip past.
Pinning the connection to the checked address would close it; not done for the demo.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections import OrderedDict
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)
MAX_BYTES = 512 * 1024
MAX_REDIRECTS = 3
TIMEOUT = 4.0


class Preview(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    url: str  # the final URL, after redirects
    title: str | None = None
    description: str | None = None
    image: str | None = None
    site_name: str | None = None
    icon: str | None = None


class Refused(Exception):
    """The URL is not allowed (bad scheme, private address, too many redirects…)."""


class FetchFailed(Exception):
    """The page could not be fetched or was not HTML."""


class PreviewCache:
    def __init__(self, *, ttl: float = 3600, max_entries: int = 512) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._entries: OrderedDict[str, tuple[float, Preview]] = OrderedDict()

    def get(self, url: str) -> Preview | None:
        hit = self._entries.get(url)
        if hit is None or time.monotonic() - hit[0] > self._ttl:
            self._entries.pop(url, None)
            return None
        self._entries.move_to_end(url)
        return hit[1]

    def put(self, url: str, preview: Preview) -> None:
        self._entries[url] = (time.monotonic(), preview)
        self._entries.move_to_end(url)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)


async def fetch_preview(url: str, client: httpx.AsyncClient) -> Preview:
    try:
        return await asyncio.wait_for(_fetch(url, client), TIMEOUT)
    except TimeoutError as exc:
        raise FetchFailed("timed out") from exc


async def _fetch(url: str, client: httpx.AsyncClient) -> Preview:
    for _ in range(MAX_REDIRECTS + 1):
        await check_url(url)
        async with client.stream(
            "GET",
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            follow_redirects=False,
        ) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise FetchFailed("redirect without a location")
                url = urljoin(url, location)
                continue
            if response.status_code >= 400:
                raise FetchFailed(f"status {response.status_code}")
            if "text/html" not in response.headers.get("content-type", ""):
                raise FetchFailed("not html")
            head = await _read_head(response)
        return parse(head, url)
    raise Refused("too many redirects")


async def _read_head(response: httpx.Response) -> str:
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        chunks.append(chunk)
        size += len(chunk)
        if size >= MAX_BYTES or b"</head>" in chunk.lower():
            break
    raw = b"".join(chunks)[:MAX_BYTES]
    return raw.decode(response.encoding or "utf-8", errors="replace")


async def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Refused("only http and https URLs")
    if parts.username or parts.password:
        raise Refused("credentials in URLs are not allowed")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parts.hostname, port, type=socket.SOCK_STREAM
        )
    except OSError as exc:
        raise FetchFailed("host did not resolve") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global or address.is_multicast:
            raise Refused("address is not public")


class _HeadParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title: str | None = None
        self.icon: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"].strip()
        elif tag == "link" and "icon" in a.get("rel", "").lower().split() and self.icon is None:
            self.icon = a.get("href") or None
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title = (self.title or "") + data.strip()


def parse(html: str, url: str) -> Preview:
    p = _HeadParser()
    p.feed(html)
    m = p.meta

    def first(*keys: str) -> str | None:
        return next((m[k] for k in keys if m.get(k)), None)

    def absolute(link: str | None) -> str | None:
        return urljoin(url, link) if link else None

    return Preview(
        url=url,
        title=first("og:title", "twitter:title") or p.title,
        description=first("og:description", "twitter:description", "description"),
        image=absolute(first("og:image", "og:image:url", "twitter:image", "twitter:image:src")),
        site_name=first("og:site_name"),
        icon=absolute(p.icon or "/favicon.ico"),
    )
