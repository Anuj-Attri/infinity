"""Memories are recalled only by vectors that can actually be compared.

The memory store already refused to compare vectors of a different SHAPE, so a
dimension change was safe. Two models at the SAME dimension were not: their
vectors dot cleanly, produce a confident ranking, and nothing looks wrong. The
store now records which model made each vector and skips the rest.

The other half is coverage. Auto-captured facts are stored even when embedding
fails — dropping a fact the user just stated is worse than storing it
unsearchable — but nothing ever went back for them. Every auto-fact captured
before a provider existed was listed in the UI and permanently invisible to
recall, which is a particularly bad shape: the user can see it, so they assume
the assistant can use it.
"""

from __future__ import annotations

import numpy as np
import pytest

from reactor.memory.store import MemoryStore


ACCT = "acct-under-test"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    return MemoryStore(tmp_path / "m.db")


def unit(dim=1024, at=0):
    v = np.zeros(dim, dtype=np.float32)
    v[at] = 1.0
    return v


def use_model(monkeypatch, name="voyage-4"):
    import reactor.memory.store as st
    monkeypatch.setattr(st, "_active_model", lambda: name)


# ── provenance ───────────────────────────────────────────────────────────────

def test_a_vector_records_the_model_that_made_it(store, monkeypatch):
    use_model(monkeypatch)
    m = store.add_memory("the user prefers dark mode", embedding=unit(), account_id=ACCT)
    row = store._conn.execute(
        "SELECT embedding_model FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row[0] == "voyage-4"


def test_a_memory_stored_without_a_vector_is_not_stamped(store, monkeypatch):
    use_model(monkeypatch)
    m = store.add_memory("no vector for this one", embedding=None, account_id=ACCT)
    row = store._conn.execute(
        "SELECT embedding, embedding_model FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row[0] is None and row[1] is None


def test_recall_skips_vectors_from_a_different_model(store, monkeypatch):
    """Same dimension, so the shape check cannot catch this."""
    use_model(monkeypatch)
    store.add_memory("dark mode", embedding=unit(at=3), account_id=ACCT)
    assert len(store.search_memories(unit(at=3), min_score=0.5, account_id=ACCT)) == 1

    store._conn.execute("UPDATE memories SET embedding_model='some-other-model'")
    store._conn.commit()
    assert store.search_memories(unit(at=3), min_score=0.5, account_id=ACCT) == [], (
        "a vector from another model was compared and returned"
    )


def test_recall_still_skips_a_wrong_dimension(store, monkeypatch):
    use_model(monkeypatch)
    store.add_memory("short vector", embedding=unit(dim=768, at=1), account_id=ACCT)
    assert store.search_memories(unit(dim=1024, at=1), min_score=0.1, account_id=ACCT) == []


def test_editing_an_unrelated_field_does_not_relabel_the_vector(store, monkeypatch):
    """COALESCE: only a NEW vector re-stamps the model."""
    use_model(monkeypatch, "model-a")
    m = store.add_memory("fact", embedding=unit(), account_id=ACCT)
    use_model(monkeypatch, "model-b")
    store.update_memory(m.id, confidence=0.5, account_id=ACCT)
    row = store._conn.execute(
        "SELECT embedding_model FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row[0] == "model-a", "an untouched vector was relabelled as the new model"


def test_supplying_a_new_vector_does_re_stamp(store, monkeypatch):
    use_model(monkeypatch, "model-a")
    m = store.add_memory("fact", embedding=unit(), account_id=ACCT)
    use_model(monkeypatch, "model-b")
    store.update_memory(m.id, text="fact, revised", embedding=unit(at=9), account_id=ACCT)
    row = store._conn.execute(
        "SELECT embedding_model FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row[0] == "model-b"


# ── coverage and backfill ────────────────────────────────────────────────────

def test_coverage_reports_the_gap(store, monkeypatch):
    use_model(monkeypatch)
    store.add_memory("embedded", embedding=unit(), account_id=ACCT)
    store.add_memory("auto-fact whose embedding failed", embedding=None, account_id=ACCT)
    cov = store.embedding_coverage("voyage-4", account_id=ACCT)
    assert cov == {"total": 2, "usable": 1, "needing_backfill": 1}


def test_backfill_finds_exactly_the_unusable_ones(store, monkeypatch):
    use_model(monkeypatch)
    good = store.add_memory("already embedded", embedding=unit(), account_id=ACCT)
    missing = store.add_memory("never embedded", embedding=None, account_id=ACCT)
    store._conn.execute("UPDATE memories SET embedding_model='old' WHERE id=?", (good.id,))
    store._conn.commit()

    ids = {mid for mid, _ in store.memories_needing_embedding("voyage-4", account_id=ACCT)}
    assert missing.id in ids, "a memory with no vector was not offered for backfill"
    assert good.id in ids, "a memory from a previous model was not offered for backfill"


def test_backfill_makes_a_memory_recallable(store, monkeypatch):
    use_model(monkeypatch)
    m = store.add_memory("the user's dog is called Ada", embedding=None, account_id=ACCT)
    assert store.search_memories(unit(at=5), min_score=0.5, account_id=ACCT) == []

    store.set_embedding(m.id, unit(at=5), "voyage-4", account_id=ACCT)
    hits = store.search_memories(unit(at=5), min_score=0.5, account_id=ACCT)
    assert [h.id for h in hits] == [m.id]
    assert store.embedding_coverage("voyage-4", account_id=ACCT)["needing_backfill"] == 0


def test_backfill_does_not_bump_updated_at(store, monkeypatch):
    """It is an internal repair, not something the user did."""
    use_model(monkeypatch)
    m = store.add_memory("fact", embedding=None, account_id=ACCT)
    before = store._conn.execute(
        "SELECT updated_at FROM memories WHERE id=?", (m.id,)).fetchone()[0]
    store.set_embedding(m.id, unit(), "voyage-4", account_id=ACCT)
    after = store._conn.execute(
        "SELECT updated_at FROM memories WHERE id=?", (m.id,)).fetchone()[0]
    assert after == before, "backfilling reshuffled every recent-memories view"


def test_an_existing_database_gains_the_column(tmp_path, monkeypatch):
    """The migration must work on a store that predates provenance."""
    import sqlite3
    from reactor.memory.store import SCHEMA
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)          # the pre-provenance shape
    con.commit()
    con.close()

    s = MemoryStore(db)                 # must migrate, not raise
    cols = {r[1] for r in s._conn.execute("PRAGMA table_info(memories)").fetchall()}
    assert "embedding_model" in cols


# ── ownership: the leak this migration closes ────────────────────────────────

B = "acct-someone-else"


def test_recall_never_returns_another_account_s_memory(store, monkeypatch):
    """The worst of the unowned stores, and the least visible.

    A leaked vault note is at least something the user can look at and say "that
    is not mine". A leaked MEMORY goes into the model's context silently and
    comes back out as the assistant apparently knowing a fact it was never told
    — about someone else's life, in someone else's session.
    """
    use_model(monkeypatch)
    store.add_memory("alice's dog is called Ada", embedding=unit(at=7), account_id=ACCT)

    mine = store.search_memories(unit(at=7), min_score=0.5, account_id=ACCT)
    assert [m.text for m in mine] == ["alice's dog is called Ada"], "the owner lost their own memory"

    theirs = store.search_memories(unit(at=7), min_score=0.5, account_id=B)
    assert theirs == [], "recall returned another account's memory"


def test_listing_is_scoped_too(store, monkeypatch):
    use_model(monkeypatch)
    store.add_memory("mine", embedding=None, account_id=ACCT)
    store.add_memory("theirs", embedding=None, account_id=B)
    assert [m.text for m in store.list_memories(account_id=ACCT)] == ["mine"]
    assert [m.text for m in store.list_memories(account_id=B)] == ["theirs"]


def test_the_profile_baseline_is_scoped(store, monkeypatch):
    """list_by_type feeds the always-on profile injection — every single turn.
    An unscoped version would put another account's profile in the prompt."""
    use_model(monkeypatch)
    store.add_memory("mine", type="profile", embedding=None, account_id=ACCT)
    store.add_memory("theirs", type="profile", embedding=None, account_id=B)
    assert [m.text for m in store.list_by_type("profile", account_id=ACCT)] == ["mine"]


def test_you_cannot_delete_or_edit_someone_else_s_memory(store, monkeypatch):
    use_model(monkeypatch)
    m = store.add_memory("alice's", embedding=None, account_id=ACCT)

    assert store.delete_memory(m.id, account_id=B) is False, "deleted another account's memory"
    assert store.update_memory(m.id, text="tampered", account_id=B) is None, "edited another account's memory"

    still = store.list_memories(account_id=ACCT)
    assert [x.text for x in still] == ["alice's"]
    assert store.delete_memory(m.id, account_id=ACCT) is True, "the owner cannot delete their own"


def test_backfill_only_touches_your_own(store, monkeypatch):
    """Backfill embeds text and costs money; doing it for another account is
    both a leak and a bill."""
    use_model(monkeypatch)
    store.add_memory("mine, unembedded", embedding=None, account_id=ACCT)
    store.add_memory("theirs, unembedded", embedding=None, account_id=B)
    texts = {t for _, t in store.memories_needing_embedding("voyage-4", account_id=ACCT)}
    assert texts == {"mine, unembedded"}


def test_coverage_counts_only_your_own(store, monkeypatch):
    use_model(monkeypatch)
    store.add_memory("mine", embedding=unit(), account_id=ACCT)
    store.add_memory("theirs", embedding=None, account_id=B)
    assert store.embedding_coverage("voyage-4", account_id=ACCT)["total"] == 1


def test_a_transcript_is_scoped_by_owner_not_just_session_id(store):
    """A session id alone is not an owner. If it were, a guessed or leaked
    session id would replay someone else's conversation."""
    store.add_turn("shared-session", "user", "alice's message", account_id=ACCT)
    store.add_turn("shared-session", "user", "bob's message", account_id=B)

    a = [t["text"] for t in store.recent_turns("shared-session", account_id=ACCT)]
    b = [t["text"] for t in store.recent_turns("shared-session", account_id=B)]
    assert a == ["alice's message"]
    assert b == ["bob's message"]


def test_document_chunks_are_scoped(store):
    store.add_doc_chunk(doc_id="d1", source="alice.pdf", chunk_index=0,
                        text="alice's document", embedding=unit(at=2), account_id=ACCT)
    store.add_doc_chunk(doc_id="d2", source="bob.pdf", chunk_index=0,
                        text="bob's document", embedding=unit(at=2), account_id=B)

    hits = store.search_doc_chunks(unit(at=2), min_score=0.5, account_id=ACCT)
    assert [h["text"] for h in hits] == ["alice's document"]
    assert [d["source"] for d in store.list_docs(account_id=ACCT)] == ["alice.pdf"]


def test_rows_written_before_ownership_are_backfilled_to_the_single_tenant(tmp_path, monkeypatch):
    """Honest only while there IS one tenant — which is why it is done now."""
    import sqlite3
    from reactor.memory.store import SCHEMA
    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO memories (id, type, text, source, confidence, entities,"
                " created_at, updated_at) VALUES ('m1','profile','old fact','user',1.0,'[]',0,0)")
    con.commit(); con.close()

    s = MemoryStore(db)
    from app.accounts import SINGLE_TENANT
    owner = s._conn.execute("SELECT owner_account_id FROM memories WHERE id='m1'").fetchone()[0]
    assert owner == SINGLE_TENANT.id, "an existing memory was left with no owner"
    assert [m.text for m in s.list_memories(account_id=SINGLE_TENANT.id)] == ["old fact"]
