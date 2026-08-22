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
# MIGRATED 2026-08-22 — no longer id-only, so no longer listed:
#   /mission/{mission_id}         — store.load() filters on account_id, and
#                                   runtime.get() applies the same predicate to
#                                   its in-process cache as the SQL does.
#   /mission/{mission_id}/events  — same check before the stream opens.
#   /mission/{mission_id}/stop    — the write. Ownership checked against the
#                                   live state, which is where a RUNNING
#                                   mission always is.
# See backend/tests/test_mission_ownership.py.
UNSCOPED_BY_ID: dict[str, str] = {
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

    # ---- Found 2026-08-21, when the scanner stopped trusting CONSTANT_CASE ---
    # All of these were always here. They were invisible because the scan
    # exempted any name that was upper-case, and every one of them is.
    "main.py":
        "_WEATHER_CACHE/_STOCK_CACHE/_MOVERS_CACHE/_HIST_CACHE/_NEWS_CACHE are "
        "keyed by city or ticker and hold public data, so a second tenant "
        "sharing them is fine. _SEARCH_CACHE and _SUMMARY_CACHE are NOT: they "
        "are keyed by the user's own query, so at M3 one account's search text "
        "and results would be served to another account that happened to ask "
        "the same thing. Those two need an account in the key.",
    "providers/chat.py":
        "_PROVIDERS holds LIVE provider instances, and an instance carries the "
        "API key it was constructed with — so this is one set of credentials "
        "for the whole process. Per-account OpenRouter keys (the M3 plan) "
        "cannot work until this is keyed by account. _MODEL_CACHE is only the "
        "fetched model list and is harmless to share.",
    "ratelimit.py":
        "_BUCKETS — deliberate and correct at one replica: the limiter must be "
        "shared to mean anything. It is also exactly why numReplicas stays 1; "
        "a second replica silently doubles every limit. Moves to Redis before "
        "scaling out, not before.",
    "mcp_client.py":
        "_TOOLS_CACHE — per-server tool listings, not user data. Becomes a leak "
        "only once servers are registered per-account rather than from config, "
        "because then the cache key stops being globally meaningful.",
    "mcp_stdio.py":
        "_SESSIONS/_LOCKS/_FAILED_AT — subprocess bookkeeping for the stdio "
        "hands, which are UNMOUNTED in Mark 4 (no local machine to run them "
        "on). Dead in practice; kept because DIRECTION.md names this module the "
        "template for the M8 LSP bridge, which will need per-account sessions.",
}


# Methods that change a container in place. Calling one of these on a
# module-level name makes it shared state no matter what the name looks like.
_MUTATING_METHODS = frozenset({
    "add", "append", "extend", "update", "pop", "clear", "remove", "discard",
    "insert", "setdefault", "popitem", "sort", "appendleft", "extendleft",
})


def _mutated_names(tree: ast.Module) -> set[str]:
    """Module-level names that something in this module mutates in place."""
    hit: set[str] = set()

    def base(node) -> str:
        """The root Name of `X`, `X[...]`, `X.attr` — '' if not a plain name."""
        while isinstance(node, (ast.Subscript, ast.Attribute)):
            node = node.value
        return getattr(node, "id", "") if isinstance(node, ast.Name) else ""

    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            if n.func.attr in _MUTATING_METHODS:
                name = base(n.func.value)
                if name:
                    hit.add(name)
        elif isinstance(n, (ast.Assign, ast.AugAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Subscript):   # X[k] = v  /  X[k] += v
                    name = base(t)
                    if name:
                        hit.add(name)
        elif isinstance(n, ast.Delete):
            for t in n.targets:
                if isinstance(t, ast.Subscript):   # del X[k]
                    name = base(t)
                    if name:
                        hit.add(name)
    return hit


def _modules_with_shared_state() -> set[str]:
    """Module-level mutable bindings, excluding ones never mutated.

    CONSTANT_CASE used to be treated as proof of constness. It is a naming
    convention, not a guarantee, and the difference was load-bearing: `fs.py`
    held `_ROOTS: set[Path] = set()`, and `"_ROOTS".isupper()` is True, so the
    scanner that calls itself exhaustive skipped a mutable set that every
    request could add to — the same set that let `/fs/tree` hand out write
    access to any directory it was named.

    So case no longer exempts anything. What exempts a binding is that nothing
    in its module ever mutates it, which is a property of the code rather than
    of what someone called it.
    """
    out: set[str] = set()
    for f in sorted(APP.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        mutated = _mutated_names(tree)
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
                if not name:
                    continue
                # Lower-case module-level mutables are shared state by
                # convention; CONSTANT_CASE ones only if actually mutated.
                if not name.isupper() or name in mutated:
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
