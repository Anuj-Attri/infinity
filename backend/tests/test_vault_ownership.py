"""One account's vault is invisible to another.

The knowledge base is the product, so this is the migration that mattered most —
and the one with the most ways to be subtly wrong. Four of them:

1. **The slug namespace.** `slug` and `rel_path` were globally UNIQUE, so two
   accounts could never own a note with the same title, and the second one to
   try would collide with a row it cannot see. They are now
   `UNIQUE(owner_account_id, ...)`, which needed a table rebuild because SQLite
   cannot drop a constraint. Slugs are ACCOUNT-LOCAL rather than org-shared:
   the owner's instruction was that knowledge is personal for now, and it is the
   reversible direction — an org-shared alias layer can be added later, whereas
   narrowing a shared namespace would break every existing [[wikilink]].

2. **The filesystem.** Notes are FILES. A shared directory means two accounts
   writing `projects/plan.md` write the SAME file, and the database predicate
   would be perfectly correct while the data was still shared. No column can fix
   that, so each account gets its own directory.

3. **Keyword search.** FTS is a separate virtual table. Scoping the vector path
   and forgetting FTS would mean semantic search is isolated and typing a word
   is not.

4. **The predicate, not the parameter.** When these functions first took an
   `account_id`, 31 of their queries still had no WHERE clause. Every signature
   was right and nothing was enforced. `test_state_ownership.py` now checks the
   SQL itself, and these tests check the behaviour.
"""

from __future__ import annotations

import asyncio
import importlib

import numpy as np
import pytest

A = "acct-alice"
B = "acct-bob"


@pytest.fixture
def vault(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VAULT_DIR", raising=False)
    monkeypatch.setenv("VOYAGE_API_KEY", "k")
    import app.paths, app.providers.embeddings, app.vault
    importlib.reload(app.paths)
    E = importlib.reload(app.providers.embeddings)

    async def _fake(texts, *, input_type="document"):
        # Deterministic unit vectors so similarity is meaningful without network.
        out = []
        for t in texts:
            v = np.zeros(1024, dtype=np.float32)
            v[abs(hash(t)) % 1024] = 1.0
            out.append(v)
        return out
    monkeypatch.setattr(E, "embed_texts", _fake)
    return importlib.reload(app.vault)


def note(v, account_id: str, name: str, body: str) -> None:
    p = v.vault_dir(account_id) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {name}\n\n{body}\n", encoding="utf-8")


def scan(v, account_id: str):
    return asyncio.run(v.scan(force=True, account_id=account_id))


# ── the filesystem ───────────────────────────────────────────────────────────

def test_each_account_gets_its_own_directory(vault, tmp_path):
    a, b = vault.vault_dir(A), vault.vault_dir(B)
    assert a != b
    assert a.parent == b.parent == tmp_path / "vault"


def test_the_same_relative_path_is_two_different_files(vault):
    """The failure a database column cannot prevent."""
    note(vault, A, "plan.md", "alice's plan")
    note(vault, B, "plan.md", "bob's plan")
    assert (vault.vault_dir(A) / "plan.md").read_text(encoding="utf-8") != \
           (vault.vault_dir(B) / "plan.md").read_text(encoding="utf-8")


# ── the index ────────────────────────────────────────────────────────────────

def test_a_scan_only_indexes_your_own_notes(vault):
    note(vault, A, "alice.md", "alice's secret plan")
    note(vault, B, "bob.md", "bob's secret plan")
    assert scan(vault, A)["indexed"] == 1
    assert scan(vault, B)["indexed"] == 1

    rows = vault._db().execute(
        "SELECT owner_account_id, COUNT(*) FROM nodes GROUP BY 1").fetchall()
    assert dict(rows) == {A: 1, B: 1}


def test_two_accounts_can_own_a_note_with_the_same_title(vault):
    """Globally-unique slugs made this a constraint error against invisible data."""
    note(vault, A, "plan.md", "alice's plan")
    note(vault, B, "plan.md", "bob's plan")
    scan(vault, A)
    scan(vault, B)
    slugs = vault._db().execute(
        "SELECT owner_account_id, slug FROM nodes ORDER BY 1").fetchall()
    assert len(slugs) == 2, "the second account's note was rejected or overwritten"
    assert slugs[0][1] == slugs[1][1], "the test is not exercising a slug collision"


# ── retrieval ────────────────────────────────────────────────────────────────

def test_vector_search_never_crosses_accounts(vault):
    note(vault, A, "alice.md", "the bowline makes a fixed loop")
    scan(vault, A)

    mine = asyncio.run(vault._vector_search("the bowline makes a fixed loop", 5, account_id=A))
    assert mine, "the owner cannot find their own note"

    theirs = asyncio.run(vault._vector_search("the bowline makes a fixed loop", 5, account_id=B))
    assert theirs == [], "vector search returned another account's note"


def test_keyword_search_never_crosses_accounts(vault):
    """FTS is a SEPARATE table. Scoping vectors and forgetting it would mean
    semantic search is isolated and typing a word is not."""
    note(vault, A, "alice.md", "pneumonoultramicroscopic")
    scan(vault, A)

    assert vault._keyword_search("pneumonoultramicroscopic", 5, account_id=A), \
        "the owner cannot keyword-search their own note"
    assert vault._keyword_search("pneumonoultramicroscopic", 5, account_id=B) == [], \
        "keyword search returned another account's note"


def test_hybrid_retrieve_never_crosses_accounts(vault):
    note(vault, A, "alice.md", "the bowline makes a fixed loop")
    scan(vault, A)
    assert asyncio.run(vault.retrieve("bowline", 5, account_id=A))
    assert asyncio.run(vault.retrieve("bowline", 5, account_id=B)) == []


def test_the_vector_matrix_only_loads_your_own(vault):
    note(vault, A, "a.md", "alice one")
    note(vault, B, "b.md", "bob one")
    scan(vault, A)
    scan(vault, B)
    ids_a, mat_a = vault._vec_matrix(account_id=A)
    ids_b, mat_b = vault._vec_matrix(account_id=B)
    assert len(ids_a) == 1 and len(ids_b) == 1
    assert mat_a.shape == (1, 1024)


# ── the routes ───────────────────────────────────────────────────────────────

def test_node_by_id_is_the_last_idor_route(vault):
    """/vault/node/{id} read a note by id and never asked whose it was.

    The knowledge base is the product, so this was the most valuable of the four
    id-addressed routes recorded in test_tenancy_debt.py.
    """
    from fastapi import HTTPException
    note(vault, A, "alice.md", "alice's private note")
    scan(vault, A)
    nid = vault._db().execute(
        "SELECT id FROM nodes WHERE owner_account_id=?", (A,)).fetchone()[0]

    got = asyncio.run(vault.vault_node(nid, account_id=A))
    assert got["id"] == nid, "the owner cannot read their own note"

    with pytest.raises(HTTPException) as ei:
        asyncio.run(vault.vault_node(nid, account_id=B))
    assert ei.value.status_code == 404, "another account read the note"


def test_a_missing_note_and_someone_else_s_look_identical(vault):
    """Both 404. "Exists but not yours" would confirm the id is real."""
    from fastapi import HTTPException
    note(vault, A, "alice.md", "private")
    scan(vault, A)
    nid = vault._db().execute(
        "SELECT id FROM nodes WHERE owner_account_id=?", (A,)).fetchone()[0]

    codes = []
    for probe in (nid, "definitely-not-a-real-id"):
        try:
            asyncio.run(vault.vault_node(probe, account_id=B))
            codes.append(200)
        except HTTPException as e:
            codes.append(e.status_code)
    assert codes == [404, 404]


def test_tree_graph_and_stats_are_scoped(vault):
    note(vault, A, "alice.md", "alice one")
    note(vault, B, "bob1.md", "bob one")
    note(vault, B, "bob2.md", "bob two")
    scan(vault, A)
    scan(vault, B)

    assert asyncio.run(vault.vault_stats(account_id=A))["nodes"] == 1
    assert asyncio.run(vault.vault_stats(account_id=B))["nodes"] == 2

    g = asyncio.run(vault.vault_graph(limit=100, account_id=A))
    assert len(g["nodes"]) == 1

    t = asyncio.run(vault.vault_tree(account_id=A))
    total = sum(len(v) for v in t.get("tree", {}).values()) if isinstance(t.get("tree"), dict) else None
    assert total in (1, None)


def test_embedding_coverage_and_backfill_are_scoped(vault):
    note(vault, A, "alice.md", "alice one")
    note(vault, B, "bob.md", "bob one")
    scan(vault, A)
    scan(vault, B)
    assert asyncio.run(vault.vault_embeddings_status(account_id=A))["chunks_total"] == 1
    assert asyncio.run(vault.backfill_embeddings(account_id=A))["embedded"] == 0


# ── the migration ────────────────────────────────────────────────────────────

def test_pre_ownership_notes_move_into_the_single_tenant_directory(monkeypatch, tmp_path):
    """Flat layout -> per-account, done once, on first use."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VAULT_DIR", raising=False)
    import app.paths
    importlib.reload(app.paths)

    flat = tmp_path / "vault"
    flat.mkdir(parents=True, exist_ok=True)
    (flat / "legacy.md").write_text("# Legacy\n\nfrom before ownership\n", encoding="utf-8")

    import app.vault
    v = importlib.reload(app.vault)
    from app.accounts import SINGLE_TENANT

    d = v.vault_dir(SINGLE_TENANT.id)
    assert (d / "legacy.md").exists(), "a pre-ownership note was left behind"
    assert not (flat / "legacy.md").exists(), "it was copied rather than moved"
