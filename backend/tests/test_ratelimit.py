"""/auth/login is public by necessity and was unlimited.

bcrypt makes each guess cost ~100 ms, which slows an attacker but does not stop
one: a few thousand guesses an hour against a human-chosen master password is a
real attack. These tests pin the limit, the exemptions, and the two properties
that make a 429 usable rather than mysterious.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://app.test")
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    import app.paths, app.auth, app.ratelimit, app.main
    importlib.reload(app.paths); importlib.reload(app.auth)
    rl = importlib.reload(app.ratelimit)
    main = importlib.reload(app.main)
    rl.reset()
    return TestClient(main.app), rl


def test_login_is_limited(client):
    c, _ = client
    codes = [c.post("/auth/login", json={"password": "x", "device_name": "d"}).status_code
             for _ in range(14)]
    assert 429 in codes, "credential guessing is unlimited"
    assert codes.index(429) == 10, f"limit fired at {codes.index(429)}, expected 10"


def test_the_429_is_readable_by_a_browser(client):
    """A 429 without CORS headers is an opaque network error to the page."""
    c, _ = client
    hdr = {"Origin": "http://app.test"}
    r = None
    for _ in range(14):
        r = c.post("/auth/login", json={"password": "x", "device_name": "d"}, headers=hdr)
        if r.status_code == 429:
            break
    assert r.status_code == 429
    assert r.headers.get("access-control-allow-origin") == "http://app.test"
    assert int(r.headers["retry-after"]) > 0


def test_the_429_does_not_leak_how_close_you_are(client):
    """'3 attempts remaining' tells an attacker they found a live surface."""
    c, _ = client
    for _ in range(14):
        r = c.post("/auth/login", json={"password": "x", "device_name": "d"})
        if r.status_code == 429:
            detail = r.json()["detail"].lower()
            assert not any(ch.isdigit() for ch in detail), f"leaks a count: {detail}"
            assert "password" not in detail
            return
    pytest.fail("never rate limited")


def test_health_is_never_limited(client):
    """A limiter that can 429 the healthcheck can take the service down alone."""
    c, _ = client
    for _ in range(14):
        c.post("/auth/login", json={"password": "x", "device_name": "d"})
    assert all(c.get("/health").status_code == 200 for _ in range(30))


def test_buckets_are_per_route_class(client):
    """Exhausting login must not lock the user out of everything else."""
    c, _ = client
    for _ in range(14):
        c.post("/auth/login", json={"password": "x", "device_name": "d"})
    assert c.get("/auth/status").status_code == 200


def test_forwarded_headers_are_ignored_unless_a_proxy_is_trusted(client):
    """Otherwise an attacker rotates one header and gets unlimited buckets —
    which is worse than no limit, because it looks like protection."""
    c, _ = client
    codes = []
    for i in range(14):
        codes.append(c.post("/auth/login",
                            json={"password": "x", "device_name": "d"},
                            headers={"X-Forwarded-For": f"10.0.0.{i}"}).status_code)
    assert 429 in codes, "spoofed X-Forwarded-For bypassed the limit"


def test_behind_a_proxy_a_forged_forwarded_header_does_not_buy_a_new_bucket(client, monkeypatch):
    """The proxy appends the address it saw. A client that sends its own
    X-Forwarded-For only controls the entries to the LEFT of that."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    c, _ = client
    codes = []
    for i in range(14):
        codes.append(c.post("/auth/login",
                            json={"password": "x", "device_name": "d"},
                            headers={"X-Forwarded-For": f"10.0.0.{i}, 198.51.100.9"}).status_code)
    assert 429 in codes, "a forged left-hand X-Forwarded-For entry bypassed the limit"


def test_behind_a_proxy_different_clients_get_different_buckets(client, monkeypatch):
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    from app import ratelimit
    from starlette.requests import Request
    def req(xff):
        return Request({"type": "http", "headers": [(b"x-forwarded-for", xff.encode())],
                        "client": ("10.9.9.9", 1)})
    assert ratelimit.client_key(req("1.1.1.1")) == "1.1.1.1"
    assert ratelimit.client_key(req("evil, 2.2.2.2")) == "2.2.2.2"
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
    assert ratelimit.client_key(req("evil, 3.3.3.3, 172.16.0.1")) == "3.3.3.3"
