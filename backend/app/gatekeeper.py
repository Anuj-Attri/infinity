"""The outermost lock: nothing is publicly reachable before accounts exist.

Owner's instruction, and it is the right one: **do not make anything publicly
reachable until security and accounts are wired.** Right now the app has a
single master password and no account layer at all — every piece of state is
shared, and four routes address resources by id without asking who is asking
(see tests/test_tenancy_debt.py). That is a fine shape for one person and not
something to leave open on the internet.

So the whole deployment sits behind a pre-shared key. `ACCESS_TOKEN` is not
authentication — it does not identify anyone and it is not a substitute for the
auth gate underneath it. It is a door: without it the URL behaves as though
nothing is there.

WHY 404 AND NOT 403. A 403 confirms there is a service here worth getting into,
which invites attention. A 404 says nothing. Same reason the login error is
uniform: never tell someone they found something.

HOW TO PRESENT IT:
  - `X-Access-Token:` header     — curl and CI
  - `/access#<token>`            — waitlist / browser (fragment never hits
    server or proxy access logs; a tiny page POSTs it and sets the cookie)
  - `?k=<token>` once            — legacy one-click; the token IS in access
    logs, so prefer `/access#` for anything you email or paste
  - `infinity_access` cookie     — what either browser path leaves behind

UNSET = OPEN. Local development needs no configuration, and a developer running
on loopback is not the threat. That does mean a deployment is open until the
variable is set, which is why the operator console reports it in red and the
runbook lists it first.

THIS COMES OUT AT M3. When Supabase Auth and the account layer land, real
identity replaces a shared secret. A single key that everyone with the link
holds is exactly the thing this product exists to replace — keeping it after
that point would be embarrassing.
"""

from __future__ import annotations

import hashlib
import os
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import ratelimit

COOKIE = "infinity_access"
HEADER = "x-access-token"
QUERY = "k"

# Railway's healthcheck has no way to present a token, and a deployment whose
# healthcheck 404s is a deployment that never goes live. It returns no user data.
# /access is the waitlist door: reachable without a cookie so the fragment can
# be posted; it never serves app state.
_EXEMPT = frozenset({"/health", "/access"})

router = APIRouter(tags=["access"])

# Minimal page: read the fragment (never sent to the server), POST it, leave.
# No framework, no analytics, no third-party script — the point is that the
# long-lived secret does not appear in Railway/Cloudflare access logs.
_ACCESS_HTML = """<!doctype html>
<meta charset="utf-8">
<title>Enter</title>
<body>
<script>
(function () {
  var t = location.hash.replace(/^#/, "");
  history.replaceState(null, "", location.pathname);
  if (!t) {
    document.body.textContent = "Not Found";
    return;
  }
  fetch("/access", {
    method: "POST",
    headers: {"content-type": "application/json"},
    body: JSON.stringify({k: t}),
    credentials: "same-origin",
  }).then(function (r) {
    if (r.ok) location.replace("/");
    else document.body.textContent = "Not Found";
  }).catch(function () {
    document.body.textContent = "Not Found";
  });
})();
</script>
</body>
"""


def _token() -> str:
    return (os.environ.get("ACCESS_TOKEN") or "").strip()


def is_locked() -> bool:
    return bool(_token())


def _matches(supplied: str, expected: str) -> bool:
    """Length-constant compare: hash both sides, then compare digests.

    Raw compare_digest on unequal-length strings returns False quickly and can
    leak length via timing. Digests are fixed-size, so the compare is uniform.
    """
    a = hashlib.sha256(supplied.encode("utf-8")).digest()
    b = hashlib.sha256(expected.encode("utf-8")).digest()
    return secrets.compare_digest(a, b)


def _cookie_secure(request: Request) -> bool:
    """HttpOnly cookies on a public HTTPS deploy must carry Secure.

    Railway (and Cloudflare) terminate TLS and speak HTTP to the container, so
    request.url.scheme is often "http". When TRUST_PROXY_HEADERS=1, honour
    X-Forwarded-Proto — the same opt-in rule as the rate limiter, because an
    untrusted client can spoof that header too.
    """
    if request.url.scheme == "https":
        return True
    if os.environ.get("TRUST_PROXY_HEADERS") == "1":
        proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        if proto.lower() == "https":
            return True
    return False


def _set_door_cookie(resp, request: Request, value: str) -> None:
    resp.set_cookie(
        COOKIE, value,
        httponly=True,          # unreachable from JavaScript, so an XSS cannot read it
        samesite="lax",
        secure=_cookie_secure(request),
        max_age=60 * 60 * 24 * 30,
        path="/",
    )


def _presented(request: Request) -> str | None:
    q = request.query_params.get(QUERY)
    if q:
        return q.strip()
    h = request.headers.get(HEADER)
    if h:
        return h.strip()
    c = request.cookies.get(COOKIE)
    if c:
        return c.strip()
    return None


def _deny() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


async def middleware(request: Request, call_next):
    expected = _token()
    if not expected or request.url.path in _EXEMPT:
        return await call_next(request)

    # Wrong-guess budget burned → 404 for everything from this IP (including a
    # later correct key) until the window slides. Same status as a miss, so the
    # lockout itself is not a signal.
    if ratelimit.door_locked_out(request):
        return _deny()

    supplied = _presented(request)
    if not supplied or not _matches(supplied, expected):
        ratelimit.record_door_failure(request)
        return _deny()

    # Arrived via ?k= — drop the cookie and strip the token out of the URL, so it
    # stops appearing in the address bar, in browser history, and in the Referer
    # header of every outbound link the page contains. Prefer /access# for
    # waitlist links: query strings are logged by the proxy; fragments are not.
    if request.query_params.get(QUERY):
        clean = request.url.remove_query_params(QUERY)
        resp = RedirectResponse(url=str(clean), status_code=307)
        _set_door_cookie(resp, request, expected)
        return resp

    return await call_next(request)


# response_model=None: FastAPI otherwise tries to build a response model from
# the `HTMLResponse | RedirectResponse` annotation and raises at IMPORT time —
# the app does not start at all, so a deploy crashloops. The union is the honest
# signature (this really does return either), so the annotation stays and the
# model generation is switched off.
@router.get("/access", response_model=None)
def access_page() -> HTMLResponse | RedirectResponse:
    """Waitlist entry. Unset ACCESS_TOKEN → there is no door; go to the app."""
    if not _token():
        return RedirectResponse(url="/", status_code=307)
    return HTMLResponse(_ACCESS_HTML, status_code=200)


@router.post("/access")
async def access_claim(request: Request):
    """Accept the token from the fragment page. Never put it in a query string."""
    expected = _token()
    if not expected:
        return JSONResponse({"ok": True})  # no door; SPA does not need a cookie

    if ratelimit.door_locked_out(request):
        return _deny()

    try:
        body = await request.json()
    except Exception:
        ratelimit.record_door_failure(request)
        return _deny()

    supplied = str((body or {}).get("k") or "").strip()
    if not supplied or not _matches(supplied, expected):
        ratelimit.record_door_failure(request)
        return _deny()

    resp = JSONResponse({"ok": True})
    _set_door_cookie(resp, request, expected)
    return resp
