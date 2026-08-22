"""A mission belongs to one account, and nobody else can read it.

First store migrated under the standing invariant. It went first because it is
the smallest and most self-contained — five functions, four callers — so the
pattern could be established somewhere the blast radius is small.

THE BUG THIS CLOSES is one of the four IDOR routes recorded in
`test_tenancy_debt.py`: `/mission/{id}` selected on id alone, so anyone who
could guess or observe a mission id could read the whole run — goal, every
node's prompt, every model output, and the synthesis. Invisible with one tenant,
a breach with two.

The subtle half is the CACHE. `runtime.get()` checks an in-process dict before
touching the database, so the predicate has to be applied in BOTH places. If
only the SQL is scoped, a mission leaks for exactly as long as it stays hot in
memory — which is a bug that appears under load, disappears when you look for
it, and would have passed a test that only exercised the database path.

These tests use two account ids directly rather than two sessions, because real
identity is not wired yet. That is the point: the isolation is built and proven
NOW, while there is one tenant and therefore no risk, so that the day a second
account exists it is already enforced rather than retrofitted.
"""

from __future__ import annotations

import importlib

import pytest

A = "account-alice"
B = "account-bob"


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.paths, app.agent.store
    importlib.reload(app.paths)
    st = importlib.reload(app.agent.store)
    st._conn = None                     # fresh database per test
    st._DB_PATH = app.paths.data_path("missions.db")
    return st


def a_mission(mid: str, account_id: str):
    from app.agent.schemas import MissionSpec, MissionState, NodeSpec
    spec = MissionSpec(id=mid, title=f"t-{mid}", goal=f"goal of {mid}",
                       nodes=[NodeSpec(id="n1", task="do the thing")])
    return MissionState.from_spec(spec, account_id=account_id)


# ── the row knows its owner ──────────────────────────────────────────────────

def test_a_saved_mission_records_who_it_belongs_to(store):
    store.save(a_mission("m1", A))
    row = store._db().execute("SELECT account_id FROM missions WHERE id='m1'").fetchone()
    assert row[0] == A


def test_the_owner_can_load_their_own_mission(store):
    """The half that must not be forgotten: isolation that also blocks the owner
    is not isolation, it is an outage. `test_tenancy_leak.py` makes the same
    point and says never to delete that test."""
    store.save(a_mission("m1", A))
    loaded = store.load("m1", account_id=A)
    assert loaded is not None and loaded.id == "m1"


def test_another_account_cannot_load_it(store):
    """The IDOR. Knowing the id used to be enough."""
    store.save(a_mission("m1", A))
    assert store.load("m1", account_id=B) is None


def test_recent_only_lists_your_own(store):
    store.save(a_mission("m1", A))
    store.save(a_mission("m2", A))
    store.save(a_mission("m3", B))
    assert {m["id"] for m in store.recent(account_id=A)} == {"m1", "m2"}
    assert {m["id"] for m in store.recent(account_id=B)} == {"m3"}


def test_prune_cannot_delete_another_account_s_missions(store):
    """A maintenance sweep that spans tenants is a data-loss bug wearing a
    plausible name."""
    old = a_mission("old", B)
    old.status = "done"
    old.updated_at = 0.0                # ancient
    store.save(old)

    store.prune(older_than_days=1.0, account_id=A)
    assert store.load("old", account_id=B) is not None, "pruned another account's data"

    store.prune(older_than_days=1.0, account_id=B)
    assert store.load("old", account_id=B) is None, "prune did not work at all"


# ── the cache must agree with the database ───────────────────────────────────

def test_the_in_process_cache_applies_the_same_predicate(monkeypatch, tmp_path):
    """A mission leaks for exactly as long as it is hot, otherwise.

    runtime.get() checks _states before the database. If only the SQL were
    scoped, this would pass against disk and leak from memory — a bug that shows
    up under load and vanishes when you go looking for it.
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.paths, app.agent.store, app.agent.runtime
    importlib.reload(app.paths)
    st = importlib.reload(app.agent.store)
    st._conn = None
    st._DB_PATH = app.paths.data_path("missions.db")
    rt = importlib.reload(app.agent.runtime)

    m = a_mission("hot", A)
    rt._states[m.id] = m                # exactly what _emit does
    st.save(m)

    assert rt.get("hot", account_id=A) is not None, "the owner cannot see it"
    assert rt.get("hot", account_id=B) is None, (
        "the in-process cache served another account's mission — the predicate "
        "is only on the SQL path"
    )


def test_a_missing_mission_is_indistinguishable_from_someone_else_s(store):
    """Both return None. A different answer for "exists but not yours" would
    confirm the id is real, which is how an enumeration attack starts."""
    store.save(a_mission("m1", A))
    assert store.load("m1", account_id=B) is None
    assert store.load("does-not-exist", account_id=B) is None


# ── the migration ────────────────────────────────────────────────────────────

def test_rows_written_before_ownership_existed_are_backfilled(monkeypatch, tmp_path):
    """Backfilling to the single tenant is only honest while there IS one.

    The same backfill after a second account exists would be a guess about which
    of them owns each row — which is precisely why this migration is worth doing
    before that day rather than after it.
    """
    import sqlite3
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.paths
    importlib.reload(app.paths)
    db_path = app.paths.data_path("missions.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # The pre-ownership schema, with a row in it.
    con = sqlite3.connect(str(db_path))
    con.execute("""CREATE TABLE missions (
        id TEXT PRIMARY KEY, status TEXT NOT NULL, title TEXT NOT NULL,
        created_at REAL NOT NULL, updated_at REAL NOT NULL, state_json TEXT NOT NULL)""")
    con.execute("INSERT INTO missions VALUES ('legacy','done','t',0,0,'{}')")
    con.commit(); con.close()

    import app.agent.store
    st = importlib.reload(app.agent.store)
    st._conn = None
    st._DB_PATH = db_path

    from app.accounts import SINGLE_TENANT
    owner = st._db().execute(
        "SELECT account_id FROM missions WHERE id='legacy'").fetchone()[0]
    assert owner == SINGLE_TENANT.id, "an existing row was left with no owner"
