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
    m = store.add_memory("the user prefers dark mode", embedding=unit())
    row = store._conn.execute(
        "SELECT embedding_model FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row[0] == "voyage-4"


def test_a_memory_stored_without_a_vector_is_not_stamped(store, monkeypatch):
    use_model(monkeypatch)
    m = store.add_memory("no vector for this one", embedding=None)
    row = store._conn.execute(
        "SELECT embedding, embedding_model FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row[0] is None and row[1] is None


def test_recall_skips_vectors_from_a_different_model(store, monkeypatch):
    """Same dimension, so the shape check cannot catch this."""
    use_model(monkeypatch)
    store.add_memory("dark mode", embedding=unit(at=3))
    assert len(store.search_memories(unit(at=3), min_score=0.5)) == 1

    store._conn.execute("UPDATE memories SET embedding_model='some-other-model'")
    store._conn.commit()
    assert store.search_memories(unit(at=3), min_score=0.5) == [], (
        "a vector from another model was compared and returned"
    )


def test_recall_still_skips_a_wrong_dimension(store, monkeypatch):
    use_model(monkeypatch)
    store.add_memory("short vector", embedding=unit(dim=768, at=1))
    assert store.search_memories(unit(dim=1024, at=1), min_score=0.1) == []


def test_editing_an_unrelated_field_does_not_relabel_the_vector(store, monkeypatch):
    """COALESCE: only a NEW vector re-stamps the model."""
    use_model(monkeypatch, "model-a")
    m = store.add_memory("fact", embedding=unit())
    use_model(monkeypatch, "model-b")
    store.update_memory(m.id, confidence=0.5)
    row = store._conn.execute(
        "SELECT embedding_model FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row[0] == "model-a", "an untouched vector was relabelled as the new model"


def test_supplying_a_new_vector_does_re_stamp(store, monkeypatch):
    use_model(monkeypatch, "model-a")
    m = store.add_memory("fact", embedding=unit())
    use_model(monkeypatch, "model-b")
    store.update_memory(m.id, text="fact, revised", embedding=unit(at=9))
    row = store._conn.execute(
        "SELECT embedding_model FROM memories WHERE id=?", (m.id,)).fetchone()
    assert row[0] == "model-b"


# ── coverage and backfill ────────────────────────────────────────────────────

def test_coverage_reports_the_gap(store, monkeypatch):
    use_model(monkeypatch)
    store.add_memory("embedded", embedding=unit())
    store.add_memory("auto-fact whose embedding failed", embedding=None)
    cov = store.embedding_coverage("voyage-4")
    assert cov == {"total": 2, "usable": 1, "needing_backfill": 1}


def test_backfill_finds_exactly_the_unusable_ones(store, monkeypatch):
    use_model(monkeypatch)
    good = store.add_memory("already embedded", embedding=unit())
    missing = store.add_memory("never embedded", embedding=None)
    store._conn.execute("UPDATE memories SET embedding_model='old' WHERE id=?", (good.id,))
    store._conn.commit()

    ids = {mid for mid, _ in store.memories_needing_embedding("voyage-4")}
    assert missing.id in ids, "a memory with no vector was not offered for backfill"
    assert good.id in ids, "a memory from a previous model was not offered for backfill"


def test_backfill_makes_a_memory_recallable(store, monkeypatch):
    use_model(monkeypatch)
    m = store.add_memory("the user's dog is called Ada", embedding=None)
    assert store.search_memories(unit(at=5), min_score=0.5) == []

    store.set_embedding(m.id, unit(at=5), "voyage-4")
    hits = store.search_memories(unit(at=5), min_score=0.5)
    assert [h.id for h in hits] == [m.id]
    assert store.embedding_coverage("voyage-4")["needing_backfill"] == 0


def test_backfill_does_not_bump_updated_at(store, monkeypatch):
    """It is an internal repair, not something the user did."""
    use_model(monkeypatch)
    m = store.add_memory("fact", embedding=None)
    before = store._conn.execute(
        "SELECT updated_at FROM memories WHERE id=?", (m.id,)).fetchone()[0]
    store.set_embedding(m.id, unit(), "voyage-4")
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
