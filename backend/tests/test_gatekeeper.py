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

@pytest.mark.parametrize("path", ["/auth/status", "/auth/login", "/echo/stream",
                                  "/vault/stats", "/admin/diagnostics", "/memory"])
def test_every_api_path_is_invisible_without_the_key(locked, path):
    """The surface cannot be enumerated. `/` is deliberately excluded — see below."""
    r = locked().get(path)
    assert r.status_code == 404, f"{path} was reachable without the access token"


def test_a_person_landing_on_the_root_is_sent_to_the_door(locked):
    """The failure that prompted this: the owner opened their own deployment in
    a browser and got a raw JSON 404 with no way forward.

    404-for-everything is still right for API paths — a stranger probing them
    learns nothing. Applied to "/" it produced a locked door with no handle. The
    redirect discloses exactly one thing, that a gated app lives here, which is
    already implied for anyone holding the URL; the key is still required for
    every byte behind it.
    """
    r = locked().get("/")
    assert r.status_code == 307
    assert r.headers["location"] == "/access"


def test_the_root_redirect_does_not_extend_to_writes(locked):
    """Only a GET of "/" is a person landing. A POST is a client, and a client
    that has lost its key should see the same nothing as a stranger."""
    assert locked().post("/", json={}).status_code == 404


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
    from app.gatekeeper import QUERY
    r = locked().get(f"/auth/status?{QUERY}=" + TOKEN)
    assert r.status_code == 307
    loc = r.headers["location"]
    assert TOKEN not in loc, f"the token survived in the redirect URL: {loc}"

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

def test_unset_serves_loopback_only(unlocked):
    """Unconfigured means LOCAL ONLY, not open.

    This used to pass everything through when ACCESS_TOKEN was unset, so a
    deployment was open until someone remembered a variable — the same shape as
    the /auth/setup hole. Forgetting is the common case, and a control whose safe
    state depends on remembering is not a control.
    """
    assert unlocked().get("/auth/status").status_code == 200   # loopback: fine
    assert unlocked().get("/vault/stats").status_code == 401   # auth still applies


def test_unset_serves_NOTHING_to_a_remote_caller(unlocked, remote_client):
    """The correction. An unconfigured deployment is inert, not exposed."""
    import app.main as m
    c = remote_client(m.app)
    for path in ("/", "/auth/status", "/auth/setup", "/vault/stats"):
        assert c.get(path).status_code == 404, (
            f"{path} answered a stranger on an UNCONFIGURED deployment"
        )
    # The healthcheck must still work, or the deploy never goes live.
    assert c.get("/health").status_code == 200


def test_the_door_parameter_cannot_collide_with_a_route_parameter(locked):
    """It was called "k", and /vault/search?q=...&k=3 passes a RESULT COUNT.

    The door read that 3 as a wrong token, answered 404, and burned the lockout
    budget — a working request refused by a name clash. A door parameter shares
    a namespace with every route's parameters, so it must be named like it
    knows that.
    """
    from app.gatekeeper import QUERY
    assert QUERY not in {"k", "q", "id", "key", "token", "limit", "page"}, (
        f"the door's query parameter is {QUERY!r}, which a real route could use"
    )
    assert QUERY.startswith("__"), "give it a prefix no route parameter would take"


def test_a_valid_cookie_survives_an_unrelated_query_parameter(locked):
    """The bug, end to end.

    _presented() used to return the FIRST credential it found and stop, so a
    stray query parameter masked a perfectly good cookie and the request was
    refused without the cookie ever being looked at.
    """
    c = locked()
    c.cookies.set("infinity_access", TOKEN)
    r = c.get("/auth/status?k=3&q=anything")
    assert r.status_code == 200, (
        f"a request with a valid cookie was refused because of an unrelated "
        f"query parameter (got {r.status_code})"
    )


def test_one_wrong_credential_does_not_mask_a_right_one(locked):
    """Directly tests the any-match rule, independent of the parameter's name.

    A stale bookmark carrying an OLD door key, plus a current valid cookie, is
    the realistic version of this: the holder has a working credential and one
    that has been rotated out. First-credential-wins refuses them on the stale
    one and never looks at the good one. Accepting is not a weakening — every
    candidate is still compared in full, in constant time, against the same
    secret.
    """
    from app.gatekeeper import QUERY
    c = locked()
    c.cookies.set("infinity_access", TOKEN)
    r = c.get(f"/auth/status?{QUERY}=an-old-rotated-key", follow_redirects=False)
    # 307 is the correct answer here, not 200: any request carrying the door
    # parameter is redirected to a clean URL so the key stops appearing in the
    # address bar, in history, and in the Referer of every outbound link. What
    # matters is that it was NOT refused.
    assert r.status_code != 404, (
        "a stale key in the query string masked a valid cookie — the request "
        "was refused without the cookie ever being checked"
    )
    assert r.status_code == 307
    assert "an-old-rotated-key" not in r.headers["location"]


def test_all_wrong_credentials_are_still_refused(locked):
    """The any-match rule must not become an any-credential rule."""
    from app.gatekeeper import QUERY
    c = locked()
    c.cookies.set("infinity_access", "also-wrong")
    r = c.get(f"/auth/status?{QUERY}=wrong-too",
              headers={"X-Access-Token": "wrong-as-well"})
    assert r.status_code == 404, "three wrong keys opened the door"


# ── the door needs a handle ───────────────────────────────────────────────────

def test_access_without_a_fragment_is_a_usable_entry_screen(locked):
    """It used to render the literal text "Not Found".

    Combined with `/` returning a bare JSON 404, the only way into a deployed
    instance was to hand-assemble a URL from a secret in a gitignored file. The
    owner opened their own app and got {"detail":"Not Found"} — a locked door
    with no handle, which is not a gate, it is a deliberate outage.
    """
    body = locked().get("/access").text
    assert "Not Found" not in body, "the entry screen is still a dead end"
    assert 'id="k"' in body, "no field to type a key into"
    assert "<form" in body, "no way to submit it"


def test_the_entry_screen_gives_nothing_away(locked):
    """Someone who guesses this path should learn only that a gated app exists.

    No version, no route names, no hint about what is inside — and above all
    never the key itself, which the page must ask for rather than confirm.
    """
    body = locked().get("/access").text
    assert TOKEN not in body, "the entry screen leaked the access key"
    for tell in ("openrouter", "railway", "sqlite", "traceback", "/vault", "/admin"):
        assert tell not in body.lower(), f"the entry screen mentions {tell!r}"


def test_an_invite_link_still_signs_you_in_without_typing(locked):
    """The fragment path is what gets emailed, and it must keep working."""
    body = locked().get("/access").text
    assert "location.hash" in body, "the invite-link path was dropped"
    assert "history.replaceState" in body, (
        "the key is not stripped from the address bar, so it lingers in history "
        "and leaks through the Referer of every outbound link"
    )


def test_the_entry_screen_is_self_contained(locked):
    """A strict-CSP page with no third-party anything.

    An external font or script on the door would hand a third party the
    referrer of every private-beta visit, and would break the page entirely if
    that host were blocked.
    """
    body = locked().get("/access").text
    assert "http://" not in body.replace("http://www.w3.org", "")
    assert "https://" not in body.replace("https://www.w3.org", "")
