"""railway.json is code, and it silently decides where the service runs.

This file exists because of a specific, expensive confusion. The deployment came
up in Amsterdam and nothing in the repo said why, because nothing in the repo
said anything about region at all — Railway had simply picked a default. Two
further facts made it hard to correct:

1. `serviceInstanceUpdate(input: {region: ...})` is ACCEPTED by Railway's API and
   then reads back as null. The `Deployment` type exposes no region either. So
   the region cannot be confirmed through the API at all; the only honest source
   is the container's own RAILWAY_REPLICA_REGION, surfaced by hosting.where().
2. Config-as-code overrides the dashboard. A region chosen by clicking is
   outranked by this file on the next deploy, so the click appears to work and
   then quietly stops working.

The conclusion is that region belongs in this file and nowhere else, and that a
JSON file cannot explain itself. These assertions are that explanation, and they
fail the build if the pin is dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "railway.json"


@pytest.fixture(scope="module")
def cfg() -> dict:
    assert CONFIG.exists(), f"{CONFIG} is missing — Railway would fall back to its own defaults"
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def test_the_image_is_built_from_our_dockerfile(cfg):
    """Railpack guessing a build is not the artifact CI tested."""
    build = cfg["build"]
    assert build["builder"] == "DOCKERFILE"
    dockerfile = REPO / build["dockerfilePath"]
    assert dockerfile.exists(), f"dockerfilePath points at {dockerfile}, which does not exist"


def test_healthcheck_matches_a_route_that_is_actually_public(cfg):
    """A healthcheck path the gatekeeper blocks makes every deploy fail."""
    from app.gatekeeper import _EXEMPT

    path = cfg["deploy"]["healthcheckPath"]
    assert path == "/health"
    assert path in _EXEMPT, (
        "the healthcheck path must be exempt from the access-token door, or "
        "Railway's prober gets a 404 and the deploy never goes live"
    )


def test_region_is_pinned_and_is_in_the_us(cfg):
    """Unpinned means Amsterdam. The owner asked for the US, twice."""
    deploy = cfg["deploy"]
    regions = deploy.get("multiRegionConfig")
    assert regions, (
        "no multiRegionConfig — Railway picks a region for us, and it picked "
        "europe-west4. Pin it here; the dashboard cannot win against this file."
    )
    assert len(regions) == 1, f"expected exactly one region, got {sorted(regions)}"
    (name, spec), = regions.items()
    assert name.startswith("us-"), f"region {name!r} is not in the US"
    assert spec.get("numReplicas") == 1


def test_replicas_are_declared_in_exactly_one_place(cfg):
    """Both forms set replicas; together they are ambiguous.

    `numReplicas` beside `multiRegionConfig` is the shape that made the region
    control unsettable in the first place. Keep it to the region-scoped one.
    """
    deploy = cfg["deploy"]
    assert not ("numReplicas" in deploy and "multiRegionConfig" in deploy), (
        "deploy.numReplicas and deploy.multiRegionConfig both set replicas. "
        "Declare replicas inside multiRegionConfig only."
    )


def test_restart_policy_does_not_hide_a_crash_loop(cfg):
    """Unlimited retries turn a boot failure into a silent, billed loop."""
    deploy = cfg["deploy"]
    assert deploy["restartPolicyType"] == "ON_FAILURE"
    assert 0 < deploy["restartPolicyMaxRetries"] <= 10
