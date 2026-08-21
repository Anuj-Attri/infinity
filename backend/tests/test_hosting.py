"""Proxy trust is inferred from the platform, never from a request.

Trusting X-Forwarded-For with nothing upstream is worse than having no rate
limit: a client sets the header itself, rotates it, and gets unlimited buckets
while the logs show a limiter working. So it must never be on by default.

But it does not have to be REMEMBERED either. Railway injects its own variables
into the container, and a request cannot forge an environment variable. That is
a different class of evidence from a header, and these tests pin the difference.
"""

from __future__ import annotations

import pytest

from app import hosting


def test_off_by_default(monkeypatch):
    """Nothing upstream means forwarded headers are ignored."""
    assert hosting.on_railway() is False
    assert hosting.behind_proxy() is False


@pytest.mark.parametrize("marker", [
    "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID", "RAILWAY_ENVIRONMENT_ID",
    "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PUBLIC_DOMAIN",
])
def test_any_railway_marker_implies_a_proxy(monkeypatch, marker):
    monkeypatch.setenv(marker, "something")
    assert hosting.on_railway() is True
    assert hosting.behind_proxy() is True


def test_an_explicit_setting_wins_in_both_directions(monkeypatch):
    """A different host, or a test, must be able to force it either way."""
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "0")
    assert hosting.behind_proxy() is False, "explicit 0 did not override detection"

    monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    assert hosting.behind_proxy() is True, "explicit 1 did not enable it"


def test_an_empty_marker_is_not_a_marker(monkeypatch):
    """An env var set to "" is how a platform variable looks when it is absent."""
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "")
    assert hosting.on_railway() is False


def test_a_request_cannot_talk_its_way_into_being_trusted(monkeypatch, tmp_path):
    """The whole point: headers are evidence a client controls, so they must not
    be able to switch on the trusting of headers."""
    import importlib
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.paths, app.auth, app.ratelimit, app.gatekeeper, app.main
    importlib.reload(app.paths); importlib.reload(app.auth)
    rl = importlib.reload(app.ratelimit)
    importlib.reload(app.gatekeeper)
    m = importlib.reload(app.main)
    rl.reset()

    from fastapi.testclient import TestClient
    c = TestClient(m.app)
    # Rotate a spoofed forwarded-for AND claim to be Railway via headers.
    codes = [
        c.post("/auth/login",
               json={"password": "x", "device_name": "d"},
               headers={
                   "X-Forwarded-For": f"10.0.0.{i}",
                   "X-Railway-Project-Id": "pretend",
                   "RAILWAY_PROJECT_ID": "pretend",
               }).status_code
        for i in range(14)
    ]
    assert 429 in codes, "a request talked itself into a fresh rate-limit bucket"
