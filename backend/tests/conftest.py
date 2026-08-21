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
    "MARK4_DEV_ACCOUNT",       # resolves every request to one account
    "ALLOW_PLAINTEXT_KEYSTORE",
    "TRUST_PROXY_HEADERS",
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
