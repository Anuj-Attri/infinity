"""The migration gate. These run in CI and fail the build.

WHY THIS FILE EXISTS. On 2026-08-21 the SPA was mounted at "/" so the deployment
would show something. That change was correct in isolation and, in combination
with an endpoint nobody re-examined, turned `/auth/setup` into an
account-takeover endpoint: unauthenticated by nature, and only 409ing once a
password already existed. Nothing was wrong with either piece. The failure was
that making something public did not force anyone to look at what was already
public.

A checklist a human follows is a checklist a human skips. So the properties that
must hold are asserted here instead, and the important one is asserted EXACTLY:
the unauthenticated surface is pinned with a written reason per entry. Adding a
public route fails this suite until someone edits it and writes down why — which
is the review step that was missing.

These are cheap, static, and deliberately blunt. A false positive costs a minute;
the failures they prevent cost the business.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
PY_FILES = sorted(p for p in APP_DIR.rglob("*.py") if "__pycache__" not in p.parts)


# ===========================================================================
# 1. The unauthenticated surface, pinned.
# ===========================================================================

# path -> why it is reachable with no credential.
# Every entry is a decision someone made on purpose. If you add one, write the
# reason, and understand that you are widening the attack surface of a product
# whose entire pitch is access control.
PUBLIC_ROUTES: dict[str, str] = {
    "/health":
        "Railway's healthcheck calls it with no credential. Returns no user data "
        "and no secret values — only a count of configured CORS origins.",
    "/echo/stream":
        "CI's transport prober. Reads and writes nothing; it echoes back the "
        "words it was handed. Gating it would turn the streaming assertion "
        "into a 401.",
    "/auth/status":
        "The first-run UI must know whether to show setup or login BEFORE it "
        "can hold a credential. Returns booleans and the keystore backend name "
        "only.",
    "/auth/setup":
        "Unauthenticated BY NATURE — there is no credential yet to check. This "
        "is the dangerous one: it is gated on SETUP_TOKEN, and a request that "
        "is neither loopback nor token-bearing is refused. See test_setup_gate.",
    "/auth/login":
        "Cannot require a session to create one. Rate limited to 10 per 15 "
        "minutes per IP, because bcrypt slows a guesser but does not stop one.",
    # Registered as routes ONLY when ENABLE_API_DOCS=1. Listed so that turning
    # docs on does not silently widen the surface without this test noticing.
    "/openapi.json": "Opt-in only (ENABLE_API_DOCS). Publishes the full API map.",
    "/docs": "Opt-in only (ENABLE_API_DOCS).",
    "/redoc": "Opt-in only (ENABLE_API_DOCS).",
    "/docs/oauth2-redirect": "Opt-in only (ENABLE_API_DOCS).",
}


def test_the_unauthenticated_surface_is_exactly_what_was_reviewed():
    """The gate. Widening this requires editing this file and writing a reason.

    This is the check that would have caught /auth/setup: making the SPA public
    did not make anyone look at what was already public, and nothing failed.
    """
    from app.main import _AUTH_EXEMPT

    actual = set(_AUTH_EXEMPT)
    pinned = set(PUBLIC_ROUTES)

    added = actual - pinned
    assert not added, (
        f"NEW UNAUTHENTICATED ROUTE(S): {sorted(added)}\n"
        "Something is now reachable with no credential that was not reviewed. "
        "If that is intended, add it to PUBLIC_ROUTES with a written reason."
    )
    removed = pinned - actual
    assert not removed, (
        f"Route(s) no longer exempt: {sorted(removed)}. Good, probably — "
        "remove them from PUBLIC_ROUTES to keep this list honest."
    )


def test_every_public_route_has_a_real_justification():
    """A one-word reason is not a review.

    The ENABLE_API_DOCS routes are exempt from the length rule: they are not
    registered at all in a normal deployment, and their reason is the flag.
    """
    OPT_IN = {"/openapi.json", "/docs", "/redoc", "/docs/oauth2-redirect"}
    for path, why in PUBLIC_ROUTES.items():
        if path in OPT_IN:
            assert "ENABLE_API_DOCS" in why, f"{path}: say that it is opt-in"
            continue
        assert len(why) > 60, f"{path}: write an actual reason, not {why!r}"


def test_docs_are_off_unless_explicitly_enabled(monkeypatch):
    """The interactive docs publish a complete map of every endpoint and
    parameter, to anyone. Opt-in, so a deployment cannot publish its own schema
    because nobody remembered to turn it off."""
    import importlib
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    import app.main
    m = importlib.reload(app.main)
    paths = {r.path for r in m.app.routes if hasattr(r, "methods")}
    for leak in ("/docs", "/redoc", "/openapi.json"):
        assert leak not in paths, f"{leak} is public by default"


def test_no_route_is_reachable_that_is_neither_gated_nor_pinned(monkeypatch):
    """Every route is either behind require_auth or on the reviewed list.

    There is no third category, and this fails if one appears.
    """
    import importlib
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)
    import app.main
    m = importlib.reload(app.main)
    paths = {r.path for r in m.app.routes if hasattr(r, "methods")}
    # Everything not pinned must be gated; the gate is app-level, so the check
    # is that the app carries the dependency at all and nothing bypasses it.
    assert any("require_auth" in str(d.dependency) for d in m.app.router.dependencies), \
        "the app-level auth dependency is gone — EVERY route is now public"
    unreviewed_public = {p for p in paths if p in m._AUTH_EXEMPT} - set(PUBLIC_ROUTES)
    assert not unreviewed_public, f"unreviewed public routes: {sorted(unreviewed_public)}"


# ===========================================================================
# 2. Static invariants across the whole backend.
# ===========================================================================

def _iter_sources():
    for f in PY_FILES:
        yield f, f.read_text(encoding="utf-8", errors="replace")


def _strip_comments_and_strings(src: str) -> str:
    """Source with comments and string literals blanked out.

    A grep over raw text cannot tell code from a comment ABOUT code, and a check
    that cries wolf is a check people learn to skip.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    lines = src.splitlines()
    # Blank out string literals (covers docstrings too).
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            a, b = node.lineno - 1, getattr(node, "end_lineno", node.lineno) - 1
            for i in range(a, min(b + 1, len(lines))):
                lines[i] = ""
    return "\n".join(line.split("#", 1)[0] for line in lines)


def test_no_silently_swallowed_exceptions_are_added():
    """`except: pass` is how a failure becomes invisible.

    The lift brought 33 of these across; 31 remain. They are grandfathered BY
    COUNT so the number can only go DOWN — a new one fails this test, and paying
    one off is followed by lowering this number so it cannot come back.

    Not a nice-to-have: nearly every entry in the landmine ledger is a failure
    that was invisible because something swallowed it.
    """
    GRANDFATHERED = 31
    found: list[str] = []
    for f, src in _iter_sources():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                body = [n for n in node.body if not isinstance(n, ast.Expr)
                        or not isinstance(getattr(n, "value", None), ast.Constant)]
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    found.append(f"{f.name}:{node.lineno}")
    assert len(found) <= GRANDFATHERED, (
        f"{len(found)} bare `except: pass` (was {GRANDFATHERED}). New ones:\n  "
        + "\n  ".join(found[GRANDFATHERED:])
        + "\nA swallowed exception is a failure nobody will ever see."
    )


def test_no_outbound_request_bypasses_the_ssrf_guard():
    """Any module that fetches a URL the CALLER chose must resolve it first.

    browse.py owns that check. A second, weaker copy elsewhere drifts; a missing
    one turns the backend into a proxy into its own private network.
    """
    # Modules allowed to make outbound HTTP at all, and why.
    ALLOWED = {
        "browse.py":      "owns _assert_safe_url; this IS the guard",
        "mcp_client.py":  "calls _assert_safe_url before every _rpc",
        "chat.py":        "fixed provider endpoints (OpenRouter/Anthropic/OpenAI/Gemini)",
        "dispatch.py":    "fixed OpenRouter endpoint",
        "main.py":        "fixed third-party APIs (weather, news, stocks)",
        "embed.py":       "fixed embedding host",
        "ocr.py":         "no outbound of its own; uses the dispatch provider",
    }
    offenders: list[str] = []
    for f, src in _iter_sources():
        # Comments and docstrings do not make outbound requests. vault.py has a
        # comment saying it REPLACED a raw httpx.get with the guarded fetcher —
        # scanning raw text flagged it, which is a false positive that trains
        # people to ignore this check.
        code = _strip_comments_and_strings(src)
        if not re.search(r"httpx\.(AsyncClient|Client|get|post)", code):
            continue
        if f.name not in ALLOWED:
            offenders.append(f.name)
    assert not offenders, (
        f"module(s) making outbound HTTP without review: {offenders}\n"
        "If the URL can come from a caller, route it through "
        "browse._assert_safe_url and add the module here with a reason."
    )


def test_mcp_rpc_still_calls_the_ssrf_guard():
    """Pinned directly: one line, easy to lose in a refactor, invisible until
    exploited.

    Checks the guard is CALLED, not merely mentioned. The first version of this
    test searched the function text for the name — and passed when the call was
    replaced with `pass`, because the import line inside the function still
    contained it. A test that cannot fail is worse than no test: it reports
    safety that was never checked.
    """
    src = (APP_DIR / "mcp_client.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == "_rpc"), None)
    assert fn is not None, "mcp_client._rpc is gone — did the transport change?"

    called = {
        (n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", ""))
        for n in ast.walk(fn) if isinstance(n, ast.Call)
    }
    assert "_assert_safe_url" in called, (
        "mcp_client._rpc no longer CALLS the SSRF guard. Remote HTTP is the only "
        "MCP transport, so every MCP call is a request to a caller-chosen URL — "
        "this is the live SSRF surface into our own private network."
    )


def test_no_html_is_built_from_an_unescaped_fstring():
    """The client renders some server strings with dangerouslySetInnerHTML.

    Any `html=f"...{x}..."` that does not escape is the exact bug that shipped:
    a page served as text/plain executed in the app's origin.
    """
    bad: list[str] = []
    for f, src in _iter_sources():
        for m in re.finditer(r"""html\s*=\s*f["'](.*?)["']""", src, re.S):
            frag = m.group(1)
            if "{" in frag and "escape(" not in frag:
                bad.append(f"{f.name}: html=f{frag[:70]!r}")
    assert not bad, "unescaped interpolation into an html field:\n  " + "\n  ".join(bad)


def test_no_personal_paths_or_usernames_are_hardcoded():
    """A hardcoded home directory is wrong in a container and, in a
    multi-tenant product, offers every user someone else's filesystem."""
    pat = re.compile(r"(C:\\\\Users\\\\[A-Za-z]|/home/[a-z]+/|/Users/[a-z]+/)")
    bad = [f"{f.name}:{i+1}" for f, src in _iter_sources()
           for i, line in enumerate(src.splitlines())
           if pat.search(line) and "example" not in line.lower()]
    assert not bad, f"hardcoded personal paths: {bad}"


def test_cors_is_never_a_wildcard():
    src = (APP_DIR / "main.py").read_text(encoding="utf-8")
    for m in re.finditer(r"allow_origins\s*=\s*(.+)", src):
        val = m.group(1)
        assert '"*"' not in val and "'*'" not in val, \
            f"allow_origins wildcard with credentials enabled: {val[:60]}"


# ===========================================================================
# 3. Responses must not echo configuration.
# ===========================================================================

@pytest.mark.parametrize("path", ["/health", "/auth/status"])
def test_public_responses_never_echo_a_secret(monkeypatch, tmp_path, path):
    """These two are readable by anyone on the internet."""
    import importlib, json
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SETUP_TOKEN", "CANARY-setup-token")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-CANARY-key")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example")
    import app.paths, app.auth, app.main
    importlib.reload(app.paths); importlib.reload(app.auth)
    m = importlib.reload(app.main)
    from fastapi.testclient import TestClient
    body = json.dumps(TestClient(m.app).get(path).json())
    assert "CANARY" not in body, f"{path} echoed a secret: {body[:160]}"
    # The origin allowlist is configuration too — a count is enough.
    assert "app.example" not in body, f"{path} published the CORS allowlist"
