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
    # --- The vault: MIGRATED 2026-08-23. nodes/chunks/edges/fts all carry
    # owner_account_id, notes live in a per-account directory (a shared one
    # would mean two accounts writing the same relative path write the same
    # FILE, which no column can prevent), and slugs are UNIQUE(owner, slug)
    # rather than globally unique. [[wikilinks]] resolve inside the owner's
    # namespace only.

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
    "app/vault.py:_db":
        "Schema creation, and the connection every other function borrows. "
        "About the tables rather than any row in them.",
    "app/vault.py:_migrate_ownership":
        "The migration that ADDS the owner columns and rebuilds nodes with "
        "composite uniques. It necessarily spans accounts because it is what "
        "creates the concept — and it is where existing rows are backfilled to "
        "the single tenant.",
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


# ---------------------------------------------------------------------------
# Tables whose every row belongs to exactly one account. A query against one of
# these without an owner predicate reads or writes across tenants.
# ---------------------------------------------------------------------------
OWNED_TABLES = ("nodes", "chunks", "edges", "fts", "memories", "doc_chunks",
                "turns", "missions", "entitlements")

# Functions whose whole job is the schema, so they legitimately span accounts.
_SCHEMA_FNS = {"_db", "_migrate_ownership", "__init__", "recover_interrupted",
               "all_switched_off", "_write"}


def _queries_without_an_owner_predicate() -> list[str]:
    """SQL against an owned table that never mentions owner_account_id.

    THE GAP THIS CLOSES. The ratchet above checks that a function NAMES an
    owner. It does not check that the owner reaches the WHERE clause — and when
    the vault was migrated, 31 queries had a correct signature and no predicate.
    Every ownership test passed. A signature is a promise; only the predicate
    keeps it, and a test that checks the promise instead of the behaviour is the
    "test that cannot fail" this codebase keeps warning about.
    """
    out: list[str] = []
    for root in ROOTS:
        for f in sorted(root.rglob("*.py")):
            if "__pycache__" in f.parts:
                continue
            try:
                tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            rel = f.relative_to(BACKEND).as_posix()
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if fn.name in _SCHEMA_FNS:
                    continue
                for node in ast.walk(fn):
                    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                        continue
                    sql = node.value.upper()
                    if not any(v in sql for v in ("SELECT ", "DELETE ", "UPDATE ")):
                        continue
                    if not any(f"FROM {t.upper()}" in sql or f"UPDATE {t.upper()}" in sql
                               for t in OWNED_TABLES):
                        continue
                    if "OWNER_ACCOUNT_ID" in sql or "ACCOUNT_ID" in sql:
                        continue
                    out.append(f"{rel}:{fn.name}: {node.value.strip()[:70]}")
    return out


# Queries that read an owned table with no predicate, and are still correct.
PREDICATE_EXEMPT: dict[str, str] = {}


def test_every_query_on_an_owned_table_filters_by_owner():
    """The predicate, not just the parameter.

    This is the check that would have caught the vault migration shipping with
    correct signatures and no WHERE clauses.
    """
    offenders = [q for q in _queries_without_an_owner_predicate()
                 if q.split(":", 2)[0] + ":" + q.split(":", 2)[1] not in PREDICATE_EXEMPT]
    assert not offenders, (
        "SQL against an owned table with no owner predicate:\n  "
        + "\n  ".join(offenders)
        + "\n\nNaming an account in the signature is a promise; the WHERE "
          "clause is what keeps it."
    )


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


# ---------------------------------------------------------------------------
# FILE-BACKED STATE. The SQL checks above cannot see any of this, and for a
# while that made the backlog look finished when it was not: "one entry left"
# was true of tables and false of the app. A JSON file on the volume is exactly
# as shared between accounts as a table with no WHERE clause.
#
# Each entry names what a second account would get. Same rules as UNOWNED:
# shrink it, and delete the line when you do.
# ---------------------------------------------------------------------------
FILE_STATE_UNOWNED: dict[str, str] = {
    "app/notify.py:_DATA":
        "notify_queue.json / notify_settings.json — one queue for everyone, so "
        "accounts receive each other's notifications, including job titles and "
        "failure messages.",
    "app/spend.py:_SPEND_PATH":
        "spend.json — one ledger. Another account's usage is attributed to you, "
        "and their spend silences your daily warning.",
    "app/mcp_client.py:_STORE_PATH":
        "mcp_servers.json — connector configuration, including bearer tokens "
        "for remote servers. Shared config means shared credentials.",
    "app/mcp_client.py:_AUDIT_PATH":
        "mcp_audit.jsonl — one audit log. An audit trail that mixes tenants "
        "cannot answer 'what did THIS account's model do', which is the only "
        "question an audit log exists for.",
    "app/skills_engine.py:_STATE_PATH":
        "skills.json — which skills are enabled. Lowest value here: the skill "
        "CONTENT is identical read-only built-in material, so only the on/off "
        "preference leaks.",
}

# File-backed state that is correctly shared.
FILE_STATE_GLOBAL: dict[str, str] = {
    "app/main.py:_PERMISSIONS_PATH":
        "nova_permissions.json — MIGRATED 2026-08-23 to {account_id: {tool: "
        "level}}. The FILE is shared; every grant inside it names the account "
        "that gave it, and an unknown account starts with nothing.",
    "app/profile.py:_PATH":
        "account_profile.json — {account_id: {display_name}}, owned from the "
        "first commit. The FILE is shared; every name inside it is keyed by the "
        "account that set it, and an account with no entry gets no name rather "
        "than someone else's. This one is load-bearing: the name goes into the "
        "system prompt, so a shared value would put one user's identity in "
        "another user's model context on every turn.",
    "app/paths.py:DATA_DIR":
        "The root every other path is derived from. Per-account by definition "
        "would be circular — it is the thing accounts are stored inside.",
    "app/auth.py:_DATA_DIR":
        "tokens.json is the single master password and its device tokens. It IS "
        "the pre-account authentication scheme; Supabase Auth retires it at M3.",
    "app/mcp_client.py:_STATE_PATH":
        "mcp_state.json holds one boolean: the operator's global MCP kill "
        "switch. Deliberately service-wide, like the spend killswitch.",
    "app/agent/store.py:_DB_PATH": "A database file; its ROWS carry the owner.",
    "app/vault.py:_DB_PATH": "A database file; its ROWS carry the owner.",
    "app/entitlements.py:_DB_PATH": "A database file; its ROWS carry the owner.",
    "app/main.py:_DB_PATH": "A database file; its ROWS carry the owner.",
    "app/vault.py:_VAULT_ROOT":
        "The ROOT of the per-account note directories. vault_dir(account_id) "
        "returns the per-account path underneath it.",
    "app/fs.py:WORKSPACE_ROOT":
        "The ROOT of the per-account code workspaces. workspace_dir(account_id) "
        "returns the per-account path underneath it.",
}


def _file_state_locations() -> set[str]:
    """Module-level names bound to a path under DATA_DIR."""
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
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                val = getattr(node, "value", None)
                if val is None:
                    continue
                calls = {n.func.id for n in ast.walk(val)
                         if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
                if not ({"data_path", "data_dir"} & calls):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    name = getattr(t, "id", "")
                    if name:
                        found.add(f"{rel}:{name}")
    return found


def test_no_new_file_backed_state_appears_unrecorded():
    """A JSON file on the volume is as shared as a table with no WHERE clause.

    The SQL ratchet is blind to all of this, which is how "backlog: one entry"
    came to be said out loud while six unowned files sat on the disk.
    """
    found = _file_state_locations()
    recorded = set(FILE_STATE_UNOWNED) | set(FILE_STATE_GLOBAL)
    new = found - recorded
    assert not new, (
        "file-backed state with no recorded owner:\n  " + "\n  ".join(sorted(new))
        + "\n\nEither make it per-account, or record it with what a second "
          "account would get."
    )


def test_the_file_state_ledger_does_not_rot():
    found = _file_state_locations()
    stale = (set(FILE_STATE_UNOWNED) | set(FILE_STATE_GLOBAL)) - found
    assert not stale, (
        f"recorded but no longer present: {sorted(stale)} — delete them, or the "
        f"ledger becomes fiction."
    )


def test_every_file_state_entry_says_what_leaks():
    for name, why in FILE_STATE_UNOWNED.items():
        assert len(why) > 60, f"{name}: say what a second account would actually get"
    for name, why in FILE_STATE_GLOBAL.items():
        assert len(why) > 40, f"{name}: say why sharing it is correct"
