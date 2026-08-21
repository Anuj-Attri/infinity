"""Rate limiting, with the auth endpoints treated as the special case they are.

The one that matters: `/auth/login` had no limit at all. bcrypt makes each guess
cost ~100 ms, which slows an attacker down but does not stop one — a few
thousand guesses an hour against a human-chosen master password is a real
attack, and the endpoint is public by necessity.

Deliberately dependency-free and in-process, which is correct for EXACTLY ONE
replica: one process sees every request, and a shared counter would be ceremony.

⚠ THIS IS NOW ENFORCED BY A DASHBOARD SETTING, NOT BY CODE. `railway.json` used
to pin `numReplicas: 1`, but that field lives in Railway's "Regions & Replicas"
block, and config-as-code overrides the dashboard — so pinning it also froze the
REGION control. Replicas are set in the dashboard now (see docs/RUNBOOK.md).

The tripwire is therefore weaker than it was and worth stating plainly: scaling
to two replicas DOUBLES the effective rate limit, silently, with nothing in this
repo objecting. At that point this moves to Postgres or Redis. The same is true
of the mission runtime, which keeps live missions in process memory.

Cloudflare or Railway's own edge should sit in front of this eventually. Their
limits protect the platform from floods; this one protects specific endpoints
from targeted abuse, and the two are not substitutes.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import JSONResponse

# X-Forwarded-For is set by whoever is upstream — including the client, if
# nothing is upstream. Trusting it unconditionally lets an attacker rotate a
# header and get an unlimited number of buckets, which is worse than no limit
# because it looks like protection.
#
# So it is never on by default, but it no longer has to be REMEMBERED either:
# hosting.behind_proxy() infers it from Railway's own injected variables, which
# a request cannot forge. An explicit TRUST_PROXY_HEADERS still wins either way.
# Read per-call rather than at import so a test can change it without a reload.
def _trust_proxy() -> bool:
    from .hosting import behind_proxy
    return behind_proxy()


@dataclass
class Rule:
    limit: int
    window_s: float
    # Why this endpoint is special, for whoever reads the 429.
    reason: str


# Longest prefix wins, so a specific rule beats the catch-all.
RULES: dict[str, Rule] = {
    # Credential guessing. Strict, and per-IP.
    "/auth/login": Rule(10, 900, "too many sign-in attempts"),
    "/auth/setup": Rule(5, 900, "too many setup attempts"),
    # Waitlist door claim (POST body). Path limiter is a second belt; the
    # gatekeeper's door_* counters are the one that covers every presentation.
    "/access": Rule(40, 900, "too many requests"),
    # Costs money per call.
    "/chat": Rule(60, 60, "too many requests"),
    "/mission": Rule(20, 60, "too many requests"),
    "/dispatch": Rule(20, 60, "too many requests"),
    "/generate": Rule(10, 60, "too many requests"),
    "/image": Rule(10, 60, "too many requests"),
    # Fetches an arbitrary URL on our behalf.
    "/browse": Rule(30, 60, "too many requests"),
}

DEFAULT_RULE = Rule(240, 60, "too many requests")

# Wrong ACCESS_TOKEN guesses. Counted separately from path rules because the
# gatekeeper sits OUTSIDE this middleware and returns 404 without call_next —
# so failed door checks would otherwise be unlimited. Response stays 404 (see
# gatekeeper); this only caps how many misses an IP gets before lockout.
DOOR_RULE = Rule(40, 900, "not found")
DOOR_SCOPE = "door"

# Never limited: the health check (a limiter that can 429 the healthcheck can
# take the service down by itself) and CI's transport prober.
EXEMPT: frozenset[str] = frozenset({"/health", "/echo/stream"})


@dataclass
class _Bucket:
    hits: list[float] = field(default_factory=list)


_LOCK = threading.Lock()
_BUCKETS: dict[tuple[str, str], _Bucket] = {}
_last_sweep = 0.0


def client_key(request: Request) -> str:
    """Who to count against. The socket peer unless a proxy is trusted."""
    if _trust_proxy():
        xff = request.headers.get("x-forwarded-for") or ""
        first = xff.split(",")[0].strip()
        if first:
            return first
    return (request.client.host if request.client else "") or "unknown"


def door_locked_out(request: Request) -> bool:
    """True when this IP has burned its wrong-guess budget for the door.

    Gatekeeper returns 404 either way — never 429 — so lockout does not confirm
    that a door exists. Correct keys are also refused until the window slides;
    that is deliberate: otherwise the budget does not actually cap guessing.
    """
    key = (DOOR_SCOPE, client_key(request))
    now = time.monotonic()
    with _LOCK:
        b = _BUCKETS.get(key)
        if not b:
            return False
        cutoff = now - DOOR_RULE.window_s
        live = [t for t in b.hits if t > cutoff]
        return len(live) >= DOOR_RULE.limit


def record_door_failure(request: Request) -> None:
    """One wrong ACCESS_TOKEN presentation (header, query, cookie, or /access)."""
    key = (DOOR_SCOPE, client_key(request))
    now = time.monotonic()
    with _LOCK:
        _sweep(now)
        b = _BUCKETS.setdefault(key, _Bucket())
        cutoff = now - DOOR_RULE.window_s
        b.hits = [t for t in b.hits if t > cutoff]
        b.hits.append(now)


def _rule_for(path: str) -> tuple[str, Rule]:
    best = ""
    for prefix in RULES:
        if path.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return (best, RULES[best]) if best else ("*", DEFAULT_RULE)


def _sweep(now: float) -> None:
    """Drop buckets nothing has touched recently, so memory cannot grow without
    bound from one-off IPs."""
    global _last_sweep
    if now - _last_sweep < 60:
        return
    _last_sweep = now
    dead = [k for k, b in _BUCKETS.items() if not b.hits or now - b.hits[-1] > 1800]
    for k in dead:
        _BUCKETS.pop(k, None)


def check(request: Request) -> tuple[bool, int, Rule]:
    """(allowed, retry_after_seconds, rule)."""
    path = request.url.path
    if path in EXEMPT:
        return True, 0, DEFAULT_RULE

    scope, rule = _rule_for(path)
    key = (scope, client_key(request))
    now = time.monotonic()

    with _LOCK:
        _sweep(now)
        b = _BUCKETS.setdefault(key, _Bucket())
        cutoff = now - rule.window_s
        # Sliding window: keep only hits still inside it.
        b.hits = [t for t in b.hits if t > cutoff]
        if len(b.hits) >= rule.limit:
            retry = int(b.hits[0] + rule.window_s - now) + 1
            return False, max(retry, 1), rule
        b.hits.append(now)
        return True, 0, rule


async def middleware(request: Request, call_next):
    allowed, retry_after, rule = check(request)
    if not allowed:
        # Deliberately vague. "wrong password, 3 attempts left" tells an attacker
        # they found a valid surface and how much budget remains.
        return JSONResponse(
            status_code=429,
            content={"detail": rule.reason},
            headers={"Retry-After": str(retry_after)},
        )
    return await call_next(request)


def reset() -> None:
    """Test hook — the counters are process-global."""
    with _LOCK:
        _BUCKETS.clear()
