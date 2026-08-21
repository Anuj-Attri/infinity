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


def test_where_reports_the_region_the_container_woke_up_in(monkeypatch):
    """This readout is a measuring instrument, so it has to be trustworthy.

    Railway's API accepts a region change and then reports `region: null`
    forever, and the Deployment type has no region field at all. So the only way
    to answer "did the move to us-east4 actually happen?" is to ask the running
    container. If this ever silently returned None the answer would look like
    "not on Railway" rather than "we do not know", which is the wrong failure.
    """
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("RAILWAY_REPLICA_REGION", "us-east4-eqdc4a")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")

    w = hosting.where()
    assert w["platform"] == "railway"
    assert w["region"] == "us-east4-eqdc4a"
    assert w["environment"] == "production"
    assert w["behind_proxy"] is True


def test_where_says_unknown_rather_than_guessing(monkeypatch):
    """Off-platform, every field is None — not a plausible-looking default."""
    w = hosting.where()
    assert w["platform"] == "unknown"
    assert w["region"] is None
    assert w["replica"] is None
    assert w["deployment"] is None


def test_where_never_reports_a_secret(monkeypatch):
    """It is rendered in the operator console, which gets screenshotted."""
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "p")
    monkeypatch.setenv("ACCESS_TOKEN", "super-secret-door-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    blob = repr(hosting.where())
    assert "super-secret-door-token" not in blob
    assert "sk-or-secret" not in blob


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
