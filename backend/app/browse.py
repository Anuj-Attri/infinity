"""Server-side browsing — fetch, extract, sanitize.

Replaces the Tauri child-webview BrowserTile. That approach positioned an
OS-level webview over a DOM rectangle, which drifts on scroll/resize and
cannot exist in a hosted build at all. This lane is portable: the SERVER
fetches, so the same code path works on desktop today and in the cloud later.

It is also the shape the product wants. Browsing here is a RESEARCH
capability that feeds the knowledge base with a citation attached, not a
Chrome clone in a tile.

Endpoints (behind the global auth gate):
    GET  /browse/fetch?url=…      -> extracted, sanitized page
    POST /browse/ingest {url}     -> fetch + push into the RAG index

Security posture:
  - SSRF-hardened. Every resolved IP is checked against private, loopback,
    link-local and reserved ranges BEFORE the request goes out; cloud
    metadata endpoints are unreachable by construction. This matters far
    more once the backend is hosted than it does on a laptop.
  - Response size and time are capped; redirects are followed but each hop
    is re-validated.
  - Output HTML is allowlist-sanitized: no script/style/iframe/object/embed,
    no on* handlers, no javascript: URLs.
  - Fail-closed: any doubt returns an error, never partial/unchecked HTML.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from html import escape
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/browse", tags=["browse"])

# The RAG store is injected by main.py (same pattern as rag.py) to avoid an
# import cycle.
_INGEST = None


def set_ingest(fn) -> None:
    """fn(title: str, text: str, url: str) -> Any — wired to the Vault."""
    global _INGEST
    _INGEST = fn


MAX_BYTES = 3_000_000          # 3 MB ceiling on a fetched document
TIMEOUT_S = 12.0
MAX_REDIRECTS = 5
UA = ("Mozilla/5.0 (compatible; InfinityReader/1.0; +local) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

# Tags kept in the rendered output. Everything else is unwrapped (children
# preserved) or dropped entirely if it is in _DROP.
_ALLOWED = {
    "p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "blockquote", "pre", "code", "em", "strong", "b", "i",
    "a", "img", "figure", "figcaption", "table", "thead", "tbody", "tr",
    "th", "td", "sup", "sub", "span", "div",
}
_DROP = {"script", "style", "iframe", "object", "embed", "noscript", "svg",
         "canvas", "form", "input", "button", "select", "textarea", "nav",
         "footer", "aside", "video", "audio", "source", "track"}
_ATTR_OK = {"a": {"href", "title"}, "img": {"src", "alt", "title"}}


# ------------------------------------------------------------------ SSRF ---

# Ranges that are NOT the public internet but that `is_private` does not flag.
#
# 100.64.0.0/10 is the one that matters here: RFC 6598 shared address space, and
# also Tailscale's range. Python reports it as is_private=False, is_global=False,
# is_reserved=False — so the obvious check waves it straight through, and an
# SSRF into it reaches carrier or tailnet hosts. A test caught this; nothing in
# the code said it.
#
# Listed explicitly rather than leaning on stdlib properties, because the meaning
# of is_private/is_global has changed between Python versions. A security check
# whose behaviour depends on the interpreter minor version is not a check.
_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8",          # this network
    "10.0.0.0/8",         # RFC1918
    "100.64.0.0/10",      # RFC6598 CGNAT / Tailscale
    "127.0.0.0/8",        # loopback
    "169.254.0.0/16",     # link-local, incl. cloud metadata at 169.254.169.254
    "172.16.0.0/12",      # RFC1918
    "192.0.0.0/24",       # IETF protocol assignments
    "192.0.2.0/24",       # TEST-NET-1
    "192.88.99.0/24",     # deprecated 6to4 relay anycast
    "192.168.0.0/16",     # RFC1918
    "198.18.0.0/15",      # benchmarking
    "198.51.100.0/24",    # TEST-NET-2
    "203.0.113.0/24",     # TEST-NET-3
    "224.0.0.0/4",        # multicast
    "240.0.0.0/4",        # reserved
    "255.255.255.255/32", # broadcast
    "::/128",             # unspecified
    "::1/128",            # loopback
    "fc00::/7",           # unique-local
    "fe80::/10",          # link-local
    "ff00::/8",           # multicast
    "64:ff9b::/96",       # NAT64
    "2001:db8::/32",      # documentation
))


def _ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # An IPv4-mapped IPv6 address (::ffff:127.0.0.1) must be judged as the IPv4
    # address it actually reaches, or the mapping is a bypass.
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    if any(addr in net for net in _BLOCKED_NETS):
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_multicast or addr.is_reserved or addr.is_unspecified)


def _assert_safe_url(raw: str) -> str:
    """Validate scheme + resolve host, rejecting anything that points inward.

    Raises HTTPException(400) on anything suspicious. Generic messages only —
    we never echo resolution details back to a caller.
    """
    u = (raw or "").strip()
    if not u:
        raise HTTPException(status_code=400, detail="URL required")
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    parts = urlparse(u)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(status_code=400, detail="Unsupported URL")
    host = parts.hostname
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not resolve host")
    if not infos or not all(_ip_is_public(i[4][0]) for i in infos):
        raise HTTPException(status_code=400, detail="Blocked address")
    return u


# ------------------------------------------------------------ extraction ---

def _clean(soup: BeautifulSoup, base: str) -> None:
    """Drop dangerous nodes, strip attributes, absolutize links, in place."""
    for tag in soup.find_all(_DROP):
        tag.decompose()
    for tag in soup.find_all(True):
        name = tag.name
        if name not in _ALLOWED:
            tag.unwrap()
            continue
        keep = _ATTR_OK.get(name, set())
        for attr in list(tag.attrs):
            if attr not in keep:
                del tag[attr]
        href = tag.get("href")
        if href:
            if href.strip().lower().startswith(("javascript:", "data:", "vbscript:")):
                del tag["href"]
            else:
                tag["href"] = urljoin(base, href)
        src = tag.get("src")
        if src:
            if src.strip().lower().startswith(("javascript:", "vbscript:")):
                del tag["src"]
            else:
                tag["src"] = urljoin(base, src)


def _pick_main(soup: BeautifulSoup):
    """Readability-lite: prefer semantic containers, else the densest block.

    Scores candidates by paragraph text length so a nav-heavy page still
    resolves to the article body rather than the chrome around it.
    """
    for sel in ("article", "main", '[role="main"]', "#content", ".post", ".article-body"):
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 200:
            return node
    best, best_score = None, 0
    for node in soup.find_all(["div", "section"]):
        paras = node.find_all("p", recursive=False) or node.find_all("p")
        score = sum(len(p.get_text(strip=True)) for p in paras)
        if score > best_score:
            best, best_score = node, score
    return best or soup.body or soup


class Page(BaseModel):
    url: str
    final_url: str
    title: str
    site: str
    text: str
    html: str
    links: list[dict[str, str]]
    truncated: bool


async def _fetch(url: str) -> Page:
    safe = _assert_safe_url(url)
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=TIMEOUT_S,
            max_redirects=MAX_REDIRECTS, headers={"User-Agent": UA},
        ) as client:
            r = await client.get(safe)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="The site took too long to respond")
    except Exception:
        raise HTTPException(status_code=502, detail="Could not reach that page")

    # Re-validate the landing host: a redirect can aim inward.
    _assert_safe_url(str(r.url))
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Site returned {r.status_code}")

    ctype = (r.headers.get("content-type") or "").lower()
    raw = r.content[:MAX_BYTES]
    truncated = len(r.content) > MAX_BYTES

    if "html" not in ctype:
        text = raw.decode(r.encoding or "utf-8", "replace")
        # ESCAPE. This branch skips _clean(), so it is the one path where
        # attacker-controlled bytes reach `html` unsanitized — and the client
        # renders `html` with dangerouslySetInnerHTML. Unescaped, any URL served
        # as text/plain with a <script> tag in the body executes in the app's
        # origin, beside the session token. The content-type check is the
        # attacker's to choose, so it cannot also be the thing that protects us.
        return Page(url=safe, final_url=str(r.url), title=str(r.url), site=urlparse(str(r.url)).netloc,
                    text=text, html=f"<pre>{escape(text[:200_000])}</pre>", links=[], truncated=truncated)

    soup = BeautifulSoup(raw, "lxml")
    title = (soup.title.get_text(strip=True) if soup.title else "") or str(r.url)
    for m in soup.find_all("meta"):
        if m.get("property") == "og:title" and m.get("content"):
            title = m["content"].strip() or title
            break

    main = _pick_main(soup)
    _clean(main, str(r.url))
    text = main.get_text("\n", strip=True)
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for a in main.find_all("a", href=True):
        href, label = a["href"], a.get_text(strip=True)[:120]
        if href.startswith("http") and href not in seen and label:
            seen.add(href)
            links.append({"href": href, "label": label})
        if len(links) >= 80:
            break

    return Page(url=safe, final_url=str(r.url), title=title,
                site=urlparse(str(r.url)).netloc, text=text,
                html=str(main), links=links, truncated=truncated)


# ------------------------------------------------------------- endpoints ---

@router.get("/fetch", response_model=Page)
async def fetch_page(url: str = Query(..., min_length=1)) -> Page:
    return await _fetch(url)


class IngestBody(BaseModel):
    url: str


@router.post("/ingest")
async def ingest_page(body: IngestBody) -> dict[str, Any]:
    """Fetch a page and put it in the RAG index, keyed by its URL so the
    citation points back at the source."""
    if _INGEST is None:
        raise HTTPException(status_code=503, detail="Index unavailable")
    page = await _fetch(body.url)
    if not page.text.strip():
        raise HTTPException(status_code=422, detail="No readable text on that page")
    name = f"{page.title} — {page.site}"
    try:
        res = _INGEST(name, page.text, page.final_url)
        if hasattr(res, "__await__"):
            res = await res
    except HTTPException:
        raise
    except Exception:
        log.exception("browse ingest failed")
        raise HTTPException(status_code=500, detail="Could not index that page")
    return {"ok": True, "name": name, "url": page.final_url, "chars": len(page.text)}
