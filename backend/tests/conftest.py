"""Shared test setup.

Two kinds of shared state make a test lie about its subject, and both have
already done so in this project:

1. **Process-global state.** The rate limiter is one counter per process by
   design, which makes it one counter across tests too. Without a reset, a test
   that exhausts a bucket makes the NEXT test fail with a 429 it never asked
   for.

2. **The developer's own environment.** `app.main` calls `load_dotenv()` on the
   repo-root `.env`, so the moment a real `.env` existed, 19 tests went red:
   `ACCESS_TOKEN` was set, the gatekeeper locked the app, and every test
   expecting 200 got 404. Nothing was wrong with the code or the tests — the
   suite had simply started depending on a file that is gitignored and differs
   per machine, which is the worst kind of flake because it passes in CI and
   fails locally.

So the deployment variables are cleared for every test. A test that needs one
sets it explicitly with `monkeypatch.setenv` and reloads what it needs, which
also makes the dependency visible in the test itself.
"""

from __future__ import annotations

import pytest

# Everything that changes how the app behaves at runtime. Clearing these makes
# the default test environment the same on every machine and in CI.
_DEPLOYMENT_ENV = (
    "ACCESS_TOKEN",            # gatekeeper: locks the whole app
    "SETUP_TOKEN",             # first-run claim gate
    "INFINITY_DEV_ACCOUNT",       # resolves every request to one account
    "ALLOW_PLAINTEXT_KEYSTORE",
    "TRUST_PROXY_HEADERS",
    # Railway injects these into the container. A test must not inherit them
    # from a shell that happens to have them set.
    "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID", "RAILWAY_ENVIRONMENT_ID",
    "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PUBLIC_DOMAIN",
    "ENABLE_API_DOCS",
    "ALLOWED_ORIGINS",
    "DATA_DIR",
    "SENTRY_DSN",
    # Provider keys: a test must never make a real, billable API call because
    # the developer happened to have a key configured.
    "OPENROUTER_API_KEY",
    "NEWS_API_KEY",
    "VOYAGE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_AI_KEY",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    """No test inherits the developer's .env.

    Clearing is not enough on its own: reloading app.main re-runs load_dotenv
    and puts everything back. The skip flag is what actually holds.
    """
    monkeypatch.setenv("INFINITY_SKIP_DOTENV", "1")
    for name in _DEPLOYMENT_ENV:
        monkeypatch.delenv(name, raising=False)
    yield


class _LoopbackScope:
    """ASGI shim that makes a request look like it came from 127.0.0.1.

    Starlette hardcodes TestClient's peer as ("testclient", 50000) inside the
    transport — there is no constructor parameter — so the scope is rewritten
    here instead.

    This is honest rather than a cheat. The gatekeeper fails closed when
    ACCESS_TOKEN is unset UNLESS the request arrived on loopback, a deliberately
    narrow escape hatch for local development that CANNOT be true off the
    internet: any proxy replaces client.host with its own address. A test client
    genuinely is local, so this makes the scope match reality.

    The alternative — teaching the gatekeeper to trust a magic hostname — would
    weaken a security control to suit the tests, which is backwards. Tests that
    are ABOUT the door set ACCESS_TOKEN, and once it is set loopback is no
    longer a bypass, so they still exercise the real path.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            scope = dict(scope)
            scope["client"] = ("127.0.0.1", 50000)
        await self.app(scope, receive, send)


@pytest.fixture(autouse=True)
def _testclient_is_loopback(monkeypatch):
    import starlette.testclient as tc
    original = tc.TestClient.__init__

    def patched(self, app, *args, **kwargs):
        return original(self, _LoopbackScope(app), *args, **kwargs)

    monkeypatch.setattr(tc.TestClient, "__init__", patched)
    yield


@pytest.fixture
def remote_client():
    """Build a TestClient that presents as a PUBLIC address, not loopback.

    For tests about what a stranger on the internet can reach. Previously this
    came free because Starlette's synthetic host happens not to be loopback —
    an accident, not a statement of intent, and one that silently changed
    meaning the moment the gatekeeper learned about loopback. Ask for it
    explicitly instead.
    """
    import starlette.testclient as tc

    class _RemoteScope:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http":
                scope = dict(scope)
                scope["client"] = ("203.0.113.7", 44444)   # TEST-NET-3
            await self.app(scope, receive, send)

    def build(app, **kwargs):
        # Bypass the autouse loopback shim by constructing the transport around
        # the remote wrapper directly.
        return tc.TestClient(_RemoteScope(app), **kwargs)

    return build


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Every test starts with an empty limiter, before and after."""
    try:
        from app import ratelimit
    except Exception:
        yield
        return
    ratelimit.reset()
    yield
    ratelimit.reset()
