"""Shared test setup.

The rate limiter is process-global by design (one replica, one counter), which
means it is also global ACROSS TESTS. Without resetting it, a test that makes
six setup attempts exhausts the bucket and the next test gets a 429 where it
expected a 422 — a failure with nothing to do with what it was testing.

This is the same class of bug as the e2e harness reporting six app failures that
were all one harness bug: shared state making a test lie about its subject.
"""

from __future__ import annotations

import pytest


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
