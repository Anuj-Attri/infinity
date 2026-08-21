"""Nothing is publicly reachable before accounts exist.

There is no account layer yet: one master password, state shared process-wide,
and four routes that address a resource by id without asking who is asking
(tests/test_tenancy_debt.py). That is a reasonable shape for one person and not
something to leave open on the internet.

ACCESS_TOKEN is a door, not authentication. It identifies nobody and replaces
nothing — the auth gate underneath still applies, and these tests pin that the
two layers are INDEPENDENT. A door that also unlocked the building would be
worse than no door, because it would look like defence in depth while being one
secret.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

TOKEN = "door-key-for-tests"


@pytest.fixture
def locked(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCESS_TOKEN", TOKEN)
    monkeypatch.delenv("MARK4_DEV_ACCOUNT", raising=False)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    import app.paths, app.auth, app.ratelimit, app.gatekeeper, app.main
    importlib.reload(app.paths); importlib.reload(app.auth)
    importlib.reload(app.ratelimit)
    importlib.reload(app.gatekeeper)
    m = importlib.reload(app.main)
    app.ratelimit.reset()
    # A FRESH client per request: TestClient persists cookies, and reusing one
    # made an earlier manual probe report a "leak" that was a logged-in request.
    return lambda: TestClient(m.app, follow_redirects=False)


@pytest.fixture
def unlocked(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ACCESS_TOKEN", raising=False)
    import app.paths, app.auth, app.ratelimit, app.gatekeeper, app.main
    importlib.reload(app.paths); importlib.reload(app.auth)
    importlib.reload(app.ratelimit)
    importlib.reload(app.gatekeeper)
    m = importlib.reload(app.main)
    app.ratelimit.reset()
    return lambda: TestClient(m.app, follow_redirects=False)


# --------------------------------------------------------------------------
# The door
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/auth/status", "/auth/login", "/echo/stream", "/vault/stats"])
def test_everything_is_invisible_without_the_key(locked, path):
    r = locked().get(path)
    assert r.status_code == 404, f"{path} was reachable without the access token"


def test_it_is_a_404_not_a_403(locked):
    """A 403 confirms there is something here worth getting into.

    Same reasoning as the uniform login error: never tell someone they found
    something.
    """
    r = locked().get("/auth/status")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
    body = r.text.lower()
    for word in ("access", "token", "forbidden", "denied", "unauthorized"):
        assert word not in body, f"the 404 hints at a door: {body[:80]}"


def test_a_wrong_key_is_the_same_as_no_key(locked):
    assert locked().get("/auth/status", headers={"X-Access-Token": "wrong"}).status_code == 404


def test_a_wrong_length_key_is_still_a_404(locked):
    """Length must not change the status — no oracle, no 500."""
    assert locked().get(
        "/auth/status", headers={"X-Access-Token": "x"}
    ).status_code == 404
    assert locked().get(
        "/auth/status", headers={"X-Access-Token": "x" * 200}
    ).status_code == 404


def test_the_healthcheck_still_answers(locked):
    """Railway's healthcheck cannot present a token, and a deployment whose
    healthcheck 404s never goes live."""
    r = locked().get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


@pytest.mark.parametrize("how", ["header", "cookie"])
def test_the_key_opens_the_door(locked, how):
    kw = ({"headers": {"X-Access-Token": TOKEN}} if how == "header"
          else {"cookies": {"infinity_access": TOKEN}})
    assert locked().get("/auth/status", **kw).status_code == 200


def test_the_query_param_sets_a_cookie_and_disappears(locked):
    """Legacy one-click. Prefer /access# for waitlist — query strings are logged."""
    r = locked().get("/auth/status?k=" + TOKEN)
    assert r.status_code == 307
    loc = r.headers["location"]
    assert "k=" not in loc, f"the token survived in the redirect URL: {loc}"

    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie, "an XSS could read the door key"
    assert "lax" in cookie.lower() or "strict" in cookie.lower()


def test_access_fragment_flow_sets_httponly_cookie(locked):
    """Waitlist path: GET /access is reachable; POST claims the cookie.

    The fragment itself never reaches the server — that is the whole point —
    so this test drives the POST the page would make.
    """
    page = locked().get("/access")
    assert page.status_code == 200
    assert "location.hash" in page.text

    r = locked().post("/access", json={"k": TOKEN})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "infinity_access" in cookie

    # Cookie opens the rest of the app.
    c = locked()
    claim = c.post("/access", json={"k": TOKEN})
    assert claim.status_code == 200
    assert c.get("/auth/status").status_code == 200


def test_access_wrong_key_is_404(locked):
    assert locked().post("/access", json={"k": "nope"}).status_code == 404


def test_secure_cookie_when_proxy_says_https(monkeypatch, tmp_path):
    """Railway terminates TLS; the container sees http unless we trust the proto."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    monkeypatch.delenv("MARK4_DEV_ACCOUNT", raising=False)
    import app.paths, app.auth, app.ratelimit, app.gatekeeper, app.main
    importlib.reload(app.paths); importlib.reload(app.auth)
    importlib.reload(app.ratelimit)
    importlib.reload(app.gatekeeper)
    m = importlib.reload(app.main)
    app.ratelimit.reset()
    c = TestClient(m.app, follow_redirects=False)
    r = c.post(
        "/access",
        json={"k": TOKEN},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert r.status_code == 200
    assert "secure" in r.headers["set-cookie"].lower()


def test_wrong_guesses_eventually_lock_the_ip(locked):
    """Failed door checks sit outside the path rate limiter — this is their cap.

    Still a 404 (never 429), including after lockout, so the budget is invisible.
    """
    from app import ratelimit as rl
    # Burn the budget with wrong headers.
    for i in range(rl.DOOR_RULE.limit):
        assert locked().get(
            "/auth/status", headers={"X-Access-Token": f"wrong-{i}"}
        ).status_code == 404
    # Correct key is also refused until the window slides.
    assert locked().get(
        "/auth/status", headers={"X-Access-Token": TOKEN}
    ).status_code == 404


# --------------------------------------------------------------------------
# The door is not the lock
# --------------------------------------------------------------------------

def test_the_key_does_not_grant_a_session(locked):
    """The whole point. Past the door, auth still applies."""
    r = locked().get("/vault/stats", headers={"X-Access-Token": TOKEN})
    assert r.status_code == 401, (
        "the access token granted access to a protected route — it is a door, "
        "not authentication, and conflating them makes one secret look like two"
    )


def test_public_routes_are_still_public_behind_the_door(locked):
    r = locked().get("/auth/status", headers={"X-Access-Token": TOKEN})
    assert r.status_code == 200


# --------------------------------------------------------------------------
# Unset = open, which is what local development needs
# --------------------------------------------------------------------------

def test_unset_leaves_the_app_open(unlocked):
    """Local dev needs no configuration, and a developer on loopback is not the
    threat. A DEPLOYMENT being open until this is set is why the console reports
    it and the runbook lists it first."""
    assert unlocked().get("/auth/status").status_code == 200
    assert unlocked().get("/vault/stats").status_code == 401   # auth still applies
