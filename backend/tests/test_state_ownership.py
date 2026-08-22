"""Every function that touches stored state must name its owner.

The standing invariant, from DIRECTION.md and repeated at the top of AGENTS.md:

    Every piece of state belongs to exactly one account. No global singletons.
    Any function that reads or writes state takes an explicit `account`.
    If you cannot name the owner of a row, the schema is wrong.

MARK4.md's M4 says this is "enforced by a CI grep, not by discipline", and this
is that check. It is a RATCHET, not a wall: the 45 functions below genuinely do
not take an account today, and pretending otherwise would just mean a red build
nobody can fix. What it forbids is adding a 46th.

WHY BOTHER NOW, WITH ONE TENANT. Because the cost of this migration is entirely
in the retrofit. Right now there is exactly one tenant, so a missed WHERE clause
is invisible and harmless. The day a second account exists, that same missed
clause is a data leak between customers — and by then the list is longer and
nobody remembers which queries were meant to be scoped. Shrinking this list
while it is free is the whole strategy.

HOW TO USE IT. Give a function its `account` parameter, thread it through, add
the WHERE clause, and DELETE ITS LINE from the list below. The test fails if you
delete a line without doing the work, and fails if you do the work without
deleting the line — so the list cannot rot in either direction.
"""

from __future__ import annotations

import ast
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOTS = [BACKEND / "app", BACKEND / "reactor"]

# A string containing one of these, inside a function, means that function
# reads or writes persisted state.
_SQL = ("INSERT ", "UPDATE ", "DELETE ", "SELECT ", "REPLACE INTO")

# Either name counts as naming the owner. `account` is an Account object;
# `account_id` is the bare id, which is the right shape for a storage layer that
# should not import the account model.
_OWNER_PARAMS = {"account", "account_id"}


# ---------------------------------------------------------------------------
# THE BACKLOG. Every entry is a function that touches state without naming an
# owner. Shrink it; never grow it.
#
# Grouped by what each one would leak once a second account exists.
# ---------------------------------------------------------------------------
UNOWNED: set[str] = {
    # --- The vault. A second tenant's search would return the first's notes,
    # and their graph would show the first's structure. -------------------
    "app/vault.py:_index_file",
    "app/vault.py:scan",
    "app/vault.py:_domain_tree",
    "app/vault.py:_slug_taken",
    "app/vault.py:_vec_matrix",
    "app/vault.py:_node_meta",
    "app/vault.py:_vector_search",
    "app/vault.py:_keyword_search",
    "app/vault.py:retrieve",
    "app/vault.py:vault_tree",
    "app/vault.py:vault_graph",
    "app/vault.py:vault_node",
    "app/vault.py:vault_stats",
    "app/vault.py:backfill_embeddings",
    "app/vault.py:vault_embeddings_status",

    # --- Memory, documents and transcript: MIGRATED 2026-08-22. Every table
    # carries owner_account_id + org_id, every read filters on it, and every
    # write supplies it. doc_chunks and turns were empty and unwired, so those
    # were free — which is exactly why they were done now rather than later.

    # --- Missions: MIGRATED 2026-08-22. MissionState carries account_id,
    # store.load/recent/prune filter on it, and runtime.get applies the same
    # predicate to its in-process cache as the database does — otherwise the
    # hot path would answer a question the cold path refuses. Nothing left here.

    # --- Not a leak, listed so the scan stays exhaustive rather than
    # selective. -------------------------------------------------------
    "app/main.py:_announce_text",
}


# ---------------------------------------------------------------------------
# DELIBERATELY GLOBAL. Not backlog — these are correct as they are, and a future
# reader must be able to tell the difference between "not migrated yet" and
# "intentionally spans accounts". An operator view that took an account could
# not do its job.
# ---------------------------------------------------------------------------
DELIBERATELY_GLOBAL: dict[str, str] = {
    "app/agent/store.py:recover_interrupted":
        "A crash does not respect tenancy. Every mission the dead process was "
        "running is stranded whoever owns it, and leaving other accounts' work "
        "stuck in 'running' forever is worse than a sweep that spans them. It "
        "moves no data between accounts — it only closes out what was already "
        "each owner's.",
    "reactor/memory/store.py:__init__":
        "Schema creation and the migration that adds the owner columns. About "
        "the tables rather than any row in them, so it necessarily spans "
        "accounts — it is where existing rows are backfilled to the single "
        "tenant.",
    "app/agent/store.py:_db":
        "Schema creation and migration, which is by definition about the table "
        "rather than about any row in it. This is where the account_id column "
        "is added and existing rows are backfilled to the single tenant.",
    "app/entitlements.py:all_switched_off":
        "An operator view: 'which accounts are currently stopped'. Scoping it to "
        "one account would make it unable to answer the only question it exists "
        "to answer. Becomes owner-only at M3, which is an AUTHORISATION question, "
        "not an ownership one.",
}


def _touches_state(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if any(k in n.value.upper() for k in _SQL):
                return True
    return False


def _names_an_owner(fn) -> bool:
    args = ({a.arg for a in fn.args.args}
            | {a.arg for a in fn.args.kwonlyargs}
            | {a.arg for a in fn.args.posonlyargs})
    return bool(args & _OWNER_PARAMS)


def _scan() -> set[str]:
    """Every state-touching function that does NOT name an owner."""
    found: set[str] = set()
    for root in ROOTS:
        for f in sorted(root.rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            rel = f.relative_to(BACKEND).as_posix()
            for n in ast.walk(tree):
                if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if _touches_state(n) and not _names_an_owner(n):
                    found.add(f"{rel}:{n.name}")
    return found


def test_no_new_unowned_state_function_is_added():
    """The wall. Everything else here is bookkeeping around it."""
    new = _scan() - UNOWNED - set(DELIBERATELY_GLOBAL)
    assert not new, (
        "state-touching function(s) with no `account`/`account_id` parameter:\n  "
        + "\n  ".join(sorted(new))
        + "\n\nEvery function that reads or writes stored state must name whose "
          "state it is. With one tenant a missing scope is invisible; with two "
          "it is a leak between customers. Add the parameter and the WHERE "
          "clause — do not add a line to UNOWNED to make this pass."
    )


def test_the_backlog_does_not_rot():
    """A fixed function must be REMOVED from the list.

    Without this the list silently becomes fiction: entries stay long after the
    work is done, and the count stops meaning anything.
    """
    stale = UNOWNED - _scan()
    assert not stale, (
        "these now take an account and must be deleted from UNOWNED:\n  "
        + "\n  ".join(sorted(stale))
        + "\n\nLeaving them makes the backlog a lie about how much is left."
    )


def test_the_scanner_can_actually_see_something():
    """Guard against a silently-blind check.

    If the parse broke or the paths moved, the two ratchets above would report
    "nothing new" and "nothing stale" by finding nothing at all — the worst
    outcome, because it reports safety that was never verified.

    This used to assert a minimum COUNT, calibrated when the backlog was 45.
    That is a test that needs recalibrating every time the backlog shrinks,
    which means it fails on progress — so it would eventually be loosened until
    it meant nothing. Instead it feeds the detector two synthetic functions and
    checks it separates them, which stays true no matter how much is migrated.
    """
    sample = ast.parse(
        '''
def unowned(x):
    return db.execute("SELECT 1 FROM things")

def owned(x, *, account_id):
    return db.execute("SELECT 1 FROM things WHERE owner=?", (account_id,))

def not_state(x):
    return x + 1
'''
    )
    fns = {n.name: n for n in sample.body}
    assert _touches_state(fns["unowned"]) and not _names_an_owner(fns["unowned"])
    assert _touches_state(fns["owned"]) and _names_an_owner(fns["owned"])
    assert not _touches_state(fns["not_state"]), "a pure function was called stateful"

    # And it must still be pointed at real source, not an empty directory.
    assert _scan(), "the scan found nothing at all — it is looking in the wrong place"

def test_the_entitlement_store_already_names_its_owner():
    """The newest state module was written account-keyed from the start, which
    is the standard everything else is migrating toward."""
    found = _scan() - set(DELIBERATELY_GLOBAL)
    assert not any(p.startswith("app/entitlements.py:") for p in found), (
        "entitlements.py stopped taking account_id — it is the reference example"
    )


def test_every_deliberately_global_function_says_why():
    """An unexplained exemption is indistinguishable from an oversight, and it
    is the first thing someone will copy when they want their own exemption."""
    for name, why in DELIBERATELY_GLOBAL.items():
        assert len(why) > 60, f"{name}: explain why scoping it would be wrong"


def test_the_global_list_does_not_rot_either():
    """If one of these gains an account parameter, it stopped being global."""
    stale = set(DELIBERATELY_GLOBAL) - _scan()
    assert not stale, (
        f"{sorted(stale)} now take an account, so they are no longer global — "
        "remove them from DELIBERATELY_GLOBAL."
    )
