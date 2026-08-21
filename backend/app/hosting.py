"""What the app can work out about where it is running, without being told.

Every setting a human has to remember is a setting that will one day be
forgotten, and the forgotten state is usually the unsafe one. `ACCESS_TOKEN`
cannot be derived — it is a secret. `TRUST_PROXY_HEADERS` can be.

WHY THIS IS SAFE. Trusting `X-Forwarded-For` when nothing is upstream is worse
than having no rate limit at all: a client sets the header itself, rotates it,
and gets an unlimited number of buckets while the logs show a limiter working.
So it must never be on by default.

But Railway injects its own variables into the container — `RAILWAY_PROJECT_ID`,
`RAILWAY_SERVICE_ID`, `RAILWAY_ENVIRONMENT_NAME` and friends — and a request
cannot forge an environment variable. Their presence is proof that we are inside
Railway, which is proof that a proxy IS in front. That is a different kind of
evidence from a header, and it is the kind worth acting on.

An explicit `TRUST_PROXY_HEADERS` still wins in both directions, so a different
host (or a test) can force it either way.
"""

from __future__ import annotations

import os

# Injected by Railway into every container. Not settable by a client, and not
# present anywhere else.
_RAILWAY_MARKERS = (
    "RAILWAY_PROJECT_ID",
    "RAILWAY_SERVICE_ID",
    "RAILWAY_ENVIRONMENT_ID",
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_PUBLIC_DOMAIN",
)


def on_railway() -> bool:
    return any((os.environ.get(k) or "").strip() for k in _RAILWAY_MARKERS)


def behind_proxy() -> bool:
    """Whether X-Forwarded-* headers may be believed.

    Explicit setting wins either way; otherwise inferred from the platform.
    """
    raw = (os.environ.get("TRUST_PROXY_HEADERS") or "").strip()
    if raw:
        return raw == "1"
    return on_railway()


def where() -> dict[str, object]:
    """Where this container actually woke up.

    Railway's API will not answer this. `serviceInstance.region` reads back null
    even immediately after a mutation setting it is accepted, and the deployment
    type exposes no region at all — so "did the region change take?" cannot be
    checked from the outside. RAILWAY_REPLICA_REGION is injected into the running
    container, which makes this readout the verification instrument for a region
    change rather than a decoration.
    """

    def _v(name: str) -> str | None:
        return (os.environ.get(name) or "").strip() or None

    return {
        "platform": "railway" if on_railway() else "unknown",
        "region": _v("RAILWAY_REPLICA_REGION"),
        "replica": _v("RAILWAY_REPLICA_ID"),
        "deployment": _v("RAILWAY_DEPLOYMENT_ID"),
        "environment": _v("RAILWAY_ENVIRONMENT_NAME"),
        "public_domain": _v("RAILWAY_PUBLIC_DOMAIN"),
        "behind_proxy": behind_proxy(),
    }


def describe() -> str:
    """One line for the boot log and the operator console."""
    if (os.environ.get("TRUST_PROXY_HEADERS") or "").strip():
        return f"TRUST_PROXY_HEADERS={os.environ['TRUST_PROXY_HEADERS']} (explicit)"
    if on_railway():
        return "detected Railway — forwarded headers trusted"
    return "no proxy detected — forwarded headers ignored"
