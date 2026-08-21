"""Vectors in the vault carry their provenance, and the corpus can be backfilled.

Two problems, both silent.

FIRST: chunks written while no embedding provider was configured have `vec IS
NULL`, and `scan()` never revisits them — it reconciles by mtime, and the files
have not changed. So configuring a provider used to fix nothing that already
existed: new notes got vectors, the entire existing corpus stayed invisible to
vector search, and nothing anywhere reported the gap.

SECOND: `vec` was a bare BLOB. Vectors from two different models are not
comparable even at the same dimension — but they still multiply, still produce a
confident ranking, and nothing looks wrong. At different dimensions it fails in
a nastier way: `_vec_matrix()` concatenates every row and reshapes, so a single
stale row of the wrong size takes out or mis-shapes the whole matrix.

Both are fixed by making a vector self-describing (model + dim) and having
readers skip what they cannot compare rather than comparing it anyway.
"""

from __future__ import annotations

import asyncio
import importlib

import numpy as np
import pytest


@pytest.fixture
def vault(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VAULT_DIR", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    import app.paths, app.providers.embeddings, app.vault
    importlib.reload(app.paths)
    importlib.reload(app.providers.embeddings)
    return importlib.reload(app.vault)


def note(v, name: str, body: str) -> None:
    p = v.VAULT_DIR / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"# {name}\n\n{body}\n", encoding="utf-8")


def fake_provider(monkeypatch, v, model="voyage-4", dim=1024):
    """Turn embeddings on without touching the network."""
    from app.providers import embeddings as E
    monkeypatch.setattr(E, "_key", lambda: "test-key")
    monkeypatch.setattr(E, "VOYAGE_MODEL", model)
    monkeypatch.setattr(E, "VOYAGE_DIM", dim)

    async def _embed_texts(texts, *, input_type="document"):
        out = []
        for i, t in enumerate(texts):
            a = np.zeros(dim, dtype=np.float32)
            a[hash(t) % dim] = 1.0          # deterministic, unit length
            out.append(a)
        return out
    monkeypatch.setattr(E, "embed_texts", _embed_texts)

    async def _embed_text(text, *, input_type="document"):
        return (await _embed_texts([text], input_type=input_type))[0]
    monkeypatch.setattr(E, "embed_text", _embed_text)
    import reactor.memory.embed as shim
    monkeypatch.setattr(shim, "embed_text", _embed_text)
    return E


def chunk_rows(v):
    return v._db().execute("SELECT id, vec, model, dim FROM chunks").fetchall()


# ── without a provider ───────────────────────────────────────────────────────

def test_chunks_are_stored_without_vectors_rather_than_with_fake_ones(vault):
    """Ingestion must keep working; the chunk is still full-text searchable."""
    note(vault, "a.md", "the sky is blue")
    asyncio.run(vault.scan(force=True))
    rows = chunk_rows(vault)
    assert rows, "the note was not indexed at all"
    assert all(r[1] is None for r in rows), "a vector was invented with no provider"
    assert all(r[2] is None and r[3] is None for r in rows)


def test_vector_search_returns_nothing_rather_than_guessing(vault):
    note(vault, "a.md", "the sky is blue")
    asyncio.run(vault.scan(force=True))
    ids, mat = vault._vec_matrix()
    assert ids == [] and mat is None


def test_status_reports_the_gap(vault):
    note(vault, "a.md", "one")
    note(vault, "b.md", "two")
    asyncio.run(vault.scan(force=True))
    st = asyncio.run(vault.vault_embeddings_status())
    assert st["chunks_total"] >= 2
    assert st["chunks_with_usable_vector"] == 0
    assert st["chunks_needing_backfill"] == st["chunks_total"]
    assert st["provider"]["configured"] is False


def test_backfill_refuses_without_a_provider(vault):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        asyncio.run(vault.backfill_embeddings())
    assert ei.value.status_code == 503


# ── with a provider ──────────────────────────────────────────────────────────

def test_new_chunks_record_which_model_made_them(vault, monkeypatch):
    fake_provider(monkeypatch, vault)
    note(vault, "a.md", "the sky is blue")
    asyncio.run(vault.scan(force=True))
    rows = chunk_rows(vault)
    assert rows and all(r[1] is not None for r in rows)
    assert all(r[2] == "voyage-4" and r[3] == 1024 for r in rows)


def test_backfill_embeds_the_corpus_that_scan_will_never_revisit(vault, monkeypatch):
    """The whole point: the files have not changed, so only this can fix them."""
    note(vault, "a.md", "first")
    note(vault, "b.md", "second")
    asyncio.run(vault.scan(force=True))
    assert all(r[1] is None for r in chunk_rows(vault))

    # A provider appears. scan() alone must NOT be what fixes this.
    fake_provider(monkeypatch, vault)
    asyncio.run(vault.scan(force=True))
    assert all(r[1] is None for r in chunk_rows(vault)), (
        "scan() re-embedded unchanged files; then this test is not testing backfill"
    )

    out = asyncio.run(vault.backfill_embeddings())
    assert out["embedded"] >= 2
    rows = chunk_rows(vault)
    assert all(r[1] is not None and r[2] == "voyage-4" for r in rows)

    st = asyncio.run(vault.vault_embeddings_status())
    assert st["chunks_needing_backfill"] == 0


def test_backfill_is_idempotent(vault, monkeypatch):
    fake_provider(monkeypatch, vault)
    note(vault, "a.md", "first")
    asyncio.run(vault.scan(force=True))
    assert asyncio.run(vault.backfill_embeddings())["embedded"] == 0, (
        "already-embedded chunks were re-embedded — that is money and rate limit"
    )


# ── the mixing hazard ────────────────────────────────────────────────────────

def test_vectors_from_another_model_are_skipped_not_compared(vault, monkeypatch):
    """Same dimension, different model: the numbers still multiply."""
    fake_provider(monkeypatch, vault)
    note(vault, "a.md", "first")
    asyncio.run(vault.scan(force=True))
    assert len(vault._vec_matrix()[0]) >= 1

    with vault._LOCK:
        vault._db().execute("UPDATE chunks SET model='some-other-model'")
        vault._db().commit()
    vault._mark_dirty()

    ids, mat = vault._vec_matrix()
    assert ids == [] and mat is None, (
        "vectors from a different model were loaded into the search matrix"
    )


def test_a_stale_wrong_size_vector_cannot_break_the_whole_matrix(vault, monkeypatch):
    """One bad row used to mis-shape or blow up the reshape for every row."""
    fake_provider(monkeypatch, vault)
    note(vault, "a.md", "first")
    note(vault, "b.md", "second")
    asyncio.run(vault.scan(force=True))
    good = len(vault._vec_matrix()[0])
    assert good >= 2

    # A row that claims the right model/dim but holds a short blob.
    with vault._LOCK:
        cid = vault._db().execute("SELECT id FROM chunks LIMIT 1").fetchone()[0]
        vault._db().execute(
            "UPDATE chunks SET vec=? WHERE id=?",
            (np.zeros(768, dtype=np.float32).tobytes(), cid))
        vault._db().commit()
    vault._mark_dirty()

    ids, mat = vault._vec_matrix()
    assert mat is not None, "one corrupt row took out the entire index"
    assert len(ids) == good - 1
    assert mat.shape == (good - 1, 1024)


def test_backfill_re_embeds_rows_from_a_previous_model(vault, monkeypatch):
    """Changing provider must be a supported operation, not a silent corruption."""
    fake_provider(monkeypatch, vault)
    note(vault, "a.md", "first")
    asyncio.run(vault.scan(force=True))
    with vault._LOCK:
        vault._db().execute("UPDATE chunks SET model='old-model', dim=1024")
        vault._db().commit()
    vault._mark_dirty()
    assert vault._vec_matrix()[0] == []

    out = asyncio.run(vault.backfill_embeddings())
    assert out["embedded"] >= 1
    assert all(r[2] == "voyage-4" for r in chunk_rows(vault))
    assert len(vault._vec_matrix()[0]) >= 1


# ── batching ─────────────────────────────────────────────────────────────────

def test_indexing_a_note_makes_ONE_embedding_request(vault, monkeypatch):
    """Not one per chunk.

    Indexing used to call the provider once per chunk, serially, each with its
    own TLS handshake — and paid it again for every changed file on every scan.
    Nothing about the output differs, so this can regress silently; only a call
    count catches it.
    """
    from app.providers import embeddings as E
    calls = {"n": 0, "sizes": []}

    async def _batch(texts, *, input_type="document"):
        calls["n"] += 1
        calls["sizes"].append(len(texts))
        return [np.eye(1024, dtype=np.float32)[i % 1024] for i in range(len(texts))]

    monkeypatch.setattr(E, "_key", lambda: "k")
    monkeypatch.setattr(E, "embed_texts", _batch)

    # A body long enough to chunk into several pieces.
    note(vault, "long.md", ("paragraph about vectors. " * 120 + "\n\n") * 4)
    asyncio.run(vault.scan(force=True))

    assert calls["n"] == 1, f"expected 1 batched request, made {calls['n']}"
    assert calls["sizes"][0] > 1, "the note did not actually chunk; test is vacuous"


def test_a_blank_chunk_does_not_cost_the_note_its_index(vault, monkeypatch):
    """The provider refuses empty strings; one blank must not fail the batch."""
    from app.providers import embeddings as E
    monkeypatch.setattr(E, "_key", lambda: "k")

    async def _batch(texts, *, input_type="document"):
        assert all(t.strip() for t in texts), "an empty string reached the provider"
        return [np.eye(1024, dtype=np.float32)[0] for _ in texts]
    monkeypatch.setattr(E, "embed_texts", _batch)

    out = asyncio.run(vault._embed_many(["real text", "   ", "more text"]))
    assert out[0] is not None and out[2] is not None
    assert out[1] is None, "a blank chunk was given a vector"
