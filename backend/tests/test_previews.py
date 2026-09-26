from __future__ import annotations

from urllib.parse import urlsplit

import httpx
import pytest

from app.previews import fetch as previews
from app.previews.fetch import FetchFailed, Preview, PreviewCache, Refused, check_url, parse

HEAD = """<html><head>
<title>Fallback title</title>
<meta property="og:title" content="OG title">
<meta name="twitter:description" content="From twitter">
<meta name="description" content="Plain description">
<meta property="og:image" content="/img/card.png">
<meta property="og:site_name" content="Example">
<link rel="shortcut icon" href="/fav.png">
</head><body>ignored</body></html>"""


def test_parse_prefers_og_then_twitter_then_plain_and_resolves_urls() -> None:
    p = parse(HEAD, "https://example.com/a/b")
    assert p.title == "OG title" and p.description == "From twitter"
    assert p.image == "https://example.com/img/card.png" and p.icon == "https://example.com/fav.png"
    assert p.site_name == "Example"
    bare = parse("<head><title> Just a title </title></head>", "https://x.org/")
    assert bare.title == "Just a title" and bare.icon == "https://x.org/favicon.ico"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/",
        "http://127.0.0.1/",
        "http://localhost:8000/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://user:pass@example.com/",
    ],
)
async def test_refuses_non_public_and_non_web_urls(url: str) -> None:
    with pytest.raises(Refused):
        await check_url(url)


async def fake_check(url: str) -> None:
    """Treat hosts starting with 'internal' as private; everything else as public."""
    if (urlsplit(url).hostname or "").startswith("internal"):
        raise Refused("address is not public")


def client(pages: dict[str, httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: pages[str(r.url)]))


async def test_follows_redirects_and_checks_every_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(previews, "check_url", fake_check)
    html = {"content-type": "text/html; charset=utf-8"}
    ok = client(
        {
            "https://short.link/x": httpx.Response(301, headers={"location": "https://site.com/p"}),
            "https://site.com/p": httpx.Response(200, headers=html, text=HEAD),
        }
    )
    p = await previews.fetch_preview("https://short.link/x", ok)
    assert p.url == "https://site.com/p" and p.title == "OG title"

    sneaky = client(
        {"https://short.link/y": httpx.Response(302, headers={"location": "http://internal.lan/"})}
    )
    with pytest.raises(Refused):
        await previews.fetch_preview("https://short.link/y", sneaky)

    loop = client({"https://a.com/": httpx.Response(302, headers={"location": "https://a.com/"})})
    with pytest.raises(Refused):
        await previews.fetch_preview("https://a.com/", loop)


async def test_non_html_and_errors_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(previews, "check_url", fake_check)
    pdf = client(
        {"https://s.com/f": httpx.Response(200, headers={"content-type": "application/pdf"})}
    )
    with pytest.raises(FetchFailed):
        await previews.fetch_preview("https://s.com/f", pdf)
    gone = client({"https://s.com/g": httpx.Response(404, headers={"content-type": "text/html"})})
    with pytest.raises(FetchFailed):
        await previews.fetch_preview("https://s.com/g", gone)


def test_cache_expires_and_is_bounded() -> None:
    cache = PreviewCache(ttl=3600, max_entries=2)
    for u in ("a", "b", "c"):
        cache.put(u, Preview(url=u))
    assert cache.get("a") is None and cache.get("c") is not None
    stale = PreviewCache(ttl=-1)
    stale.put("x", Preview(url="x"))
    assert stale.get("x") is None
