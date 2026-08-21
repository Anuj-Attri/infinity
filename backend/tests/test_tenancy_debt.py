"""Every place that will leak across accounts the day accounts exist.

`test_tenancy_leak.py` is the acceptance gate for retrieval. This file is the
inventory of everything ELSE — the surfaces that are safe today only because
there is exactly one tenant, and that become live cross-account leaks the moment
M3 lands.

The point is that this is a RATCHET, not a note. The list is asserted exactly:

  * add a new unscoped surface  -> this fails, and you must justify it here
  * fix one and forget to remove it -> this fails, so the list cannot rot into
    fiction that everyone stops reading

That second half matters more than it looks. A stale "known issues" document is
worse than none: it is read once, trusted, and then quietly diverges from the
code until nobody believes it.

None of these is exploitable right now. Recording them at the moment they were
found — rather than rediscovering them under a real tenant — is the whole idea.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"


# ---------------------------------------------------------------------------
# 1. Routes that identify a resource by id and never ask who is asking.
# ---------------------------------------------------------------------------

# route -> what an attacker gets once there is more than one tenant.
UNSCOPED_BY_ID: dict[str, str] = {
    "/mission/{mission_id}":
        "Full mission state including the synthesised output. Checks only that "
        "the mission EXISTS.",
    "/mission/{mission_id}/events":
        "Live NDJSON stream of another account's mission as it runs.",
    "/mission/{mission_id}/stop":
        "Kills another account's running mission. A write, not just a read.",
    "/vault/node/{node_id}":
        "A single vault node by id — the knowledge base is the product, and "
        "this reads from it without asking whose it is.",
}


def _route_paths() -> set[str]:
    import app.main as m
    return {r.path for r in m.app.routes if hasattr(r, "methods")}


def test_the_unscoped_by_id_surface_is_exactly_what_was_recorded():
    """A ratchet in both directions.

    Mission ids are `uuid4().hex[:10]` — 40 bits, so not brute-forceable over
    HTTP. That is obscurity, not authorisation: an id that leaks through a log,
    a shared screenshot or a URL is enough, and product law #2 says permissions
    are enforced at retrieval rather than by how hard something is to guess.
    """
    present = _route_paths()
    recorded = set(UNSCOPED_BY_ID)

    stale = recorded - present
    assert not stale, (
        f"recorded as unscoped but no longer routed: {sorted(stale)}\n"
        "If these were fixed or removed, delete them here — a known-issues list "
        "that has drifted from the code is worse than none."
    )
    for path, why in UNSCOPED_BY_ID.items():
        assert len(why) > 40, f"{path}: say what an attacker actually gets"


@pytest.mark.xfail(
    strict=True,
    reason="M3/M4: these take an account and filter by owner. When that lands "
           "this passes unexpectedly and the suite fails, which is the signal "
           "to delete this test and the list above.",
)
def test_id_addressed_routes_take_an_account():
    """Written to fail, like the leak test.

    Because the marker is strict, the day someone adds account scoping this
    becomes an UNEXPECTED PASS and the build goes red — telling us to retire the
    debt entry instead of letting it sit here looking permanent.
    """
    import app.integration as integ
    for name in ("mission_state", "mission_events", "mission_stop"):
        fn = getattr(integ, name, None)
        if fn is None:
            continue
        params = inspect.signature(fn).parameters
        assert "account" in params, f"{name} does not know who is asking"


# ---------------------------------------------------------------------------
# 2. Module-level state shared by every user of the process.
# ---------------------------------------------------------------------------

# module -> the names, and why each is or is not a leak today.
SHARED_MUTABLE_STATE: dict[str, str] = {
    "agent/runtime.py":
        "_running/_stops/_subscribers/_states — one mission registry for the "
        "whole process. Also why a deploy kills every in-flight mission.",
    "notify.py":
        "_settings/_queue/_earned_history — ONE notification queue for everyone. "
        "With more than one tenant, users receive each other's notifications.",
    "spend.py":
        "_day_state — the daily spend nudge is process-wide, so one account's "
        "usage silences another's warning.",
    "vault.py":
        "_vec_cache — a single embedding matrix over the whole corpus. At M5 "
        "this would mix every tenant's vectors into one index.",
    "auth.py":
        "_tokens — correct while auth is a single master password. Retired at "
        "M3 by Supabase Auth.",
    "skills_engine.py":
        "_skills — NOT a leak: read-only built-in content, identical for every "
        "user. Listed so the scan stays exhaustive rather than selective.",
}


def _modules_with_shared_state() -> set[str]:
    """Module-level mutable bindings that are not CONSTANT-cased."""
    out: set[str] = set()
    for f in sorted(APP.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in tree.body:
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(node, ast.AnnAssign) else [])
            val = getattr(node, "value", None)
            if val is None:
                continue
            mutable = isinstance(val, (ast.Dict, ast.List, ast.Set)) or (
                isinstance(val, ast.Call)
                and getattr(val.func, "id", "") in
                {"dict", "list", "set", "defaultdict", "OrderedDict", "deque"}
            )
            if not mutable:
                continue
            for t in targets:
                name = getattr(t, "id", "")
                if name and not name.isupper():
                    out.add(f.relative_to(APP).as_posix())
    return out


def test_no_new_module_level_shared_state_appears():
    """Every one of these becomes a cross-account leak at M3.

    Adding another is not forbidden — it is forbidden to add one SILENTLY.
    """
    found = _modules_with_shared_state()
    recorded = set(SHARED_MUTABLE_STATE)

    new = found - recorded
    assert not new, (
        f"NEW module-level shared state in: {sorted(new)}\n"
        "In a multi-tenant process this is shared by every user. If it is "
        "genuinely per-process and not per-user (like a read-only index), record "
        "it here with that reasoning."
    )
    gone = recorded - found
    assert not gone, (
        f"recorded but no longer present: {sorted(gone)} — remove them so this "
        "list keeps meaning something."
    )


def test_every_shared_state_entry_says_whether_it_leaks():
    for mod, why in SHARED_MUTABLE_STATE.items():
        assert len(why) > 50, f"{mod}: explain what it means for a second tenant"
