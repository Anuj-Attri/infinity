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
# NOT "k". It was, and it collided with any route that legitimately takes a
# query parameter called k — /vault/search?q=...&k=3 passes a RESULT COUNT, the
# door read it as a wrong token, returned 404, and burned the lockout budget.
# A door parameter shares a namespace with every route's parameters, so it has
# to be named like it knows that.
QUERY = "__infinity_access"

# Railway's healthcheck has no way to present a token, and a deployment whose
# healthcheck 404s is a deployment that never goes live. It returns no user data.
# /access is the waitlist door: reachable without a cookie so the fragment can
# be posted; it never serves app state.
_EXEMPT = frozenset({"/health", "/access"})

router = APIRouter(tags=["access"])

# THE DOOR HAS TO HAVE A HANDLE.
#
# This page used to render the literal text "Not Found" whenever it was opened
# without a `#token` fragment. Combined with `/` returning a bare JSON 404, that
# meant the ONLY way into a deployed instance was to hand-assemble a URL from a
# secret in a gitignored file. The owner opened their own app and got
# `{"detail":"Not Found"}` — a locked door with no handle, which is not a gate,
# it is an outage that happens to be deliberate.
#
# So: `/` still 404s for anyone with no key (the URL gives nothing away), and
# `/access` is now a real entry screen. Someone who guesses this path learns
# that a gated app exists here and nothing else — no product name, no version,
# no hint about what is inside. For a private beta that is the right trade: the
# alternative was an instance its own owner could not open.
#
# The fragment path still works and is still preferred for anything emailed: a
# `#...` is never sent to the server, so the key stays out of Railway's and
# Cloudflare's access logs, which a `?query=` does not.
_ACCESS_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Infinity</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background:
      radial-gradient(1100px 700px at 18% -10%, #efeaff 0%, rgba(239,234,255,0) 60%),
      radial-gradient(900px 620px at 88% 108%, #e9e4fb 0%, rgba(233,228,251,0) 62%),
      #ffffff;
    color: #100e14;
    font-family: ui-sans-serif, -apple-system, "Segoe UI", Inter, system-ui, sans-serif;
    padding: 24px;
  }
  .card { width: 100%; max-width: 380px; text-align: center; }
  .mark { width: 46px; height: 46px; margin: 0 0 22px; }
  h1 {
    font-family: ui-serif, Georgia, "Times New Roman", serif;
    font-style: italic; font-weight: 400;
    font-size: clamp(28px, 6vw, 38px); margin: 0 0 8px; letter-spacing: -0.01em;
  }
  p.sub { margin: 0 0 26px; color: rgba(16,14,20,0.55); font-size: 14px; }
  form { display: flex; gap: 8px; }
  input {
    flex: 1 1 auto; min-width: 0;
    padding: 13px 15px; font-size: 15px;
    border: 1px solid rgba(20,18,24,0.14); border-radius: 12px;
    background: rgba(255,255,255,0.85); color: inherit;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  input:focus { outline: none; border-color: #5a4ca8; box-shadow: 0 0 0 3px rgba(90,76,168,0.14); }
  button {
    flex: 0 0 auto; padding: 13px 20px; font-size: 15px; font-weight: 500;
    border: 0; border-radius: 12px; cursor: pointer;
    background: #221a45; color: #fff;
  }
  button:disabled { opacity: 0.55; cursor: default; }
  .msg { min-height: 20px; margin-top: 16px; font-size: 13.5px; color: #b02020; }
  .hint { margin-top: 26px; font-size: 12.5px; color: rgba(16,14,20,0.42); line-height: 1.6; }
</style>
</head><body>
  <main class="card">
    <svg class="mark" viewBox="0 0 64 64" role="img" aria-label="Infinity">
      <defs><linearGradient id="g" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
        <stop offset="0" stop-color="#221a45"/><stop offset="1" stop-color="#8b7cf6"/>
      </linearGradient></defs>
      <rect width="64" height="64" rx="14" fill="url(#g)"/>
      <path d="M32 32c-4.2-6.4-8-9.6-12.2-9.6-5.3 0-9.3 4.3-9.3 9.6s4 9.6 9.3 9.6C24 41.2 27.8 38 32 31.6
               m0 .4c4.2-6.4 8-9.6 12.2-9.6 5.3 0 9.3 4.3 9.3 9.6s-4 9.6-9.3 9.6C40 41.6 36.2 38.4 32 32z"
            fill="none" stroke="#fff" stroke-width="4.4" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>
    <h1>Welcome back.</h1>
    <p class="sub">This instance is private. Enter your access key to continue.</p>
    <form id="f" autocomplete="off">
      <input id="k" type="password" placeholder="Access key" aria-label="Access key"
             autocomplete="off" spellcheck="false" autofocus>
      <button id="b" type="submit">Enter</button>
    </form>
    <div class="msg" id="m" role="status" aria-live="polite"></div>
    <p class="hint">Opened from an invite link? It signs you in automatically.</p>
  </main>
<script>
(function () {
  var form = document.getElementById("f"), key = document.getElementById("k"),
      btn = document.getElementById("b"), msg = document.getElementById("m");

  function submit(token) {
    if (!token) { msg.textContent = "Enter your access key."; return; }
    btn.disabled = true; msg.style.color = "rgba(16,14,20,0.55)"; msg.textContent = "Checking…";
    fetch("/access", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({k: token}),
      credentials: "same-origin",
    }).then(function (r) {
      if (r.ok) { location.replace("/"); return; }
      btn.disabled = false;
      msg.style.color = "#b02020";
      // Deliberately not "wrong key": after too many tries the door stops
      // answering for a while, and a message that only ever says "wrong"
      // would leave someone retyping a key that was already correct.
      msg.textContent = "That key was not accepted. Check it and try again.";
      key.select();
    }).catch(function () {
      btn.disabled = false;
      msg.style.color = "#b02020";
      msg.textContent = "Could not reach the server. Try again in a moment.";
    });
  }

  // An invite link carries the key in the FRAGMENT, which never reaches the
  // server or its logs. Strip it from the address bar before doing anything,
  // so it does not linger in history or leak through a Referer header.
  var fromLink = location.hash.replace(/^#/, "");
  if (fromLink) {
    history.replaceState(null, "", location.pathname);
    submit(fromLink);
  }

  form.addEventListener("submit", function (e) { e.preventDefault(); submit(key.value.trim()); });
})();
</script>
</body></html>
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
    request.url.scheme is often "http". X-Forwarded-Proto is only believed when
    a proxy is known to be in front — the same rule as the rate limiter, because
    an untrusted client can spoof that header too. hosting.behind_proxy() infers
    that from Railway's own injected variables, which a request cannot forge.
    """
    if request.url.scheme == "https":
        return True
    from .hosting import behind_proxy
    if behind_proxy():
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


def _presented(request: Request) -> list[str]:
    """Every credential the request carries, in preference order.

    It used to return the FIRST one present and stop. That made one malformed
    channel mask a perfectly good one: a browser with a valid door cookie that
    also happened to send a query parameter got refused on the parameter and
    never had its cookie looked at. A caller offering several keys should get in
    if ANY of them is right — that is not a weakening, because each is still
    compared in full against the same secret.
    """
    out: list[str] = []
    for v in (request.query_params.get(QUERY),
              request.headers.get(HEADER),
              request.cookies.get(COOKIE)):
        if v and v.strip():
            out.append(v.strip())
    return out


def _deny() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


def _is_loopback(request: Request) -> bool:
    """True only for a request that arrived on the local machine.

    Behind ANY proxy — Railway, Cloudflare, nginx — request.client.host is the
    proxy's address, never loopback. That asymmetry is exactly what makes this a
    safe test for "is this a developer on their own box": it cannot accidentally
    be true for a request off the internet.
    """
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost")


async def middleware(request: Request, call_next):
    expected = _token()
    if request.url.path in _EXEMPT:
        return await call_next(request)

    # FAIL CLOSED WHEN UNCONFIGURED.
    #
    # This used to pass everything through when ACCESS_TOKEN was unset, so a
    # deployment was OPEN until someone remembered to set a variable. That is
    # the same shape as the /auth/setup hole: a default that is safe on a laptop
    # and wrong the moment the thing is reachable. Forgetting is the common
    # case, and a security control whose safe state depends on remembering is
    # not a control.
    #
    # So: no token and not loopback means nothing is served but /health. Local
    # development still needs zero configuration; a hosted deploy that has not
    # been configured is inert rather than exposed.
    if not expected:
        if _is_loopback(request):
            return await call_next(request)
        return _deny()

    # Wrong-guess budget burned → 404 for everything from this IP (including a
    # later correct key) until the window slides. Same status as a miss, so the
    # lockout itself is not a signal.
    if ratelimit.door_locked_out(request):
        return _deny()

    supplied = _presented(request)
    if not supplied or not any(_matches(v, expected) for v in supplied):
        ratelimit.record_door_failure(request)
        # A PERSON LANDING ON "/" GETS THE DOOR. EVERYTHING ELSE STAYS SILENT.
        #
        # 404-for-everything was the original rule and it is still right for API
        # paths: a stranger probing /vault/stats or /admin/diagnostics learns
        # nothing, so the surface cannot be enumerated. But applying it to "/"
        # meant the owner opened their own deployment and got a raw JSON 404
        # with no way forward — the failure that made this change necessary.
        #
        # The redirect discloses exactly one thing: a gated app lives here. That
        # is already implied for anyone holding the URL, and the key is still
        # required for every byte behind it.
        if request.method == "GET" and request.url.path == "/":
            return RedirectResponse(url="/access", status_code=307)
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
