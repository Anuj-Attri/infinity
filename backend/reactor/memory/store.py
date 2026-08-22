"""SQLite-backed memory store.

Schema (two tables, single DB file):
    memories   — typed durable items (profile facts, episodic context)
                 plus their embedding as a BLOB for semantic search.
    turns      — persisted conversation transcript.

The store is sync-only (sqlite3 stdlib). Call sites wrap blocking ops in
asyncio.to_thread when used from FastAPI handlers.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np


SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    text        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'user',
    confidence  REAL NOT NULL DEFAULT 1.0,
    entities    TEXT NOT NULL DEFAULT '[]',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    embedding   BLOB
);
CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type);
CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at DESC);

CREATE TABLE IF NOT EXISTS turns (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    model       TEXT,
    ts          INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, ts);

-- RAG document chunks. SEPARATE namespace from `memories`: doc vectors are
-- scanned only by search_doc_chunks(), never by search_memories/list_by_type,
-- so ingested documents can NOT pollute the profile/episodic store or the
-- relevance gate.
CREATE TABLE IF NOT EXISTS doc_chunks (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL,       -- stable per source file (hash of name+content)
    source      TEXT NOT NULL,       -- original filename
    chunk_index INTEGER NOT NULL,
    text        TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    embedding   BLOB
);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_doc ON doc_chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_chunks_source ON doc_chunks(source);
"""


@dataclass
class Memory:
    id: str
    type: str                          # 'profile' | 'episodic'
    text: str
    source: str                        # 'user' | 'auto' | 'system'
    confidence: float
    entities: list[str] = field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0
    score: float | None = None         # set during search; None otherwise

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "text": self.text,
            "source": self.source,
            "confidence": self.confidence,
            "entities": self.entities,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            **({"score": self.score} if self.score is not None else {}),
        }


def _now_ms() -> int:
    return int(time.time() * 1000)


def _active_model() -> str | None:
    """Model id to stamp on vectors written now. None when unconfigured."""
    try:
        from app.providers.embeddings import active_model
        return active_model()
    except Exception:
        return None


def _decode_emb(blob: bytes | None) -> np.ndarray | None:
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32)


def _encode_emb(vec: np.ndarray | None) -> bytes | None:
    if vec is None:
        return None
    return np.asarray(vec, dtype=np.float32).tobytes()


class MemoryStore:
    """Thin SQLite wrapper. One DB per app instance. All operations sync."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        # WHOSE ROW THIS IS.
        #
        # Added by migration because every table predates ownership. Existing
        # rows are backfilled to the single tenant, which is the true owner —
        # there has only ever been one. The same backfill after a second account
        # exists would be a guess about which of them owns each row, which is
        # the entire reason to do this while it is still free.
        #
        # No column DEFAULT on purpose: a default is exactly what lets a future
        # unowned INSERT pass silently. The app supplies it or the write fails.
        from app.accounts import SINGLE_TENANT
        for table in ("memories", "doc_chunks", "turns"):
            have = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for col in ("owner_account_id", "org_id"):
                if col not in have:
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
            self._conn.execute(
                f"UPDATE {table} SET owner_account_id=?, org_id=?"
                f" WHERE owner_account_id IS NULL",
                (SINGLE_TENANT.id, SINGLE_TENANT.org_id))
            self._conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_owner"
                f" ON {table}(owner_account_id)")
        # WHICH MODEL MADE THIS VECTOR.
        #
        # recall() already refuses to compare vectors of a different SHAPE, so a
        # dimension change is safe. Two models at the SAME dimension are not:
        # their vectors still dot cleanly and still produce a confident ranking,
        # so an index holding both looks healthy and returns noise. Recording the
        # model makes that case detectable instead of silent.
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "embedding_model" not in have:
            self._conn.execute("ALTER TABLE memories ADD COLUMN embedding_model TEXT")
        self._conn.commit()

    # ── Memories ─────────────────────────────────────────────────────

    def add_memory(
        self,
        text: str,
        *,
        type: str = "profile",
        source: str = "user",
        confidence: float = 1.0,
        entities: Iterable[str] | None = None,
        embedding: np.ndarray | None = None,
        account_id: str,
        org_id: str = "",
    ) -> Memory:
        mid = uuid.uuid4().hex[:12]
        now = _now_ms()
        ents = list(entities or [])
        self._conn.execute(
            "INSERT INTO memories (id, type, text, source, confidence, entities,"
            " created_at, updated_at, embedding, embedding_model,"
            " owner_account_id, org_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, type, text, source, confidence, json.dumps(ents), now, now,
             _encode_emb(embedding), _active_model() if embedding is not None else None,
             account_id, org_id),
        )
        self._conn.commit()
        return Memory(
            id=mid, type=type, text=text, source=source, confidence=confidence,
            entities=ents, created_at=now, updated_at=now,
        )

    def memories_needing_embedding(self, model: str | None, limit: int = 256,
                                   *, account_id: str) -> list[tuple[str, str]]:
        """(id, text) for memories that have no usable vector for `model`.

        Auto-captured facts are stored even when embedding fails — dropping a
        fact the user stated is worse than storing it unsearchable. But nothing
        ever went back for them, so every auto-fact captured before a provider
        existed was permanently invisible to recall. This is what goes back.
        """
        rows = self._conn.execute(
            "SELECT id, text FROM memories"
            " WHERE owner_account_id=? AND (embedding IS NULL OR embedding_model IS NOT ?)"
            " LIMIT ?",
            (account_id, model, limit),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def set_embedding(self, mid: str, vec: np.ndarray, model: str | None,
                      *, account_id: str) -> None:
        """Attach a vector to an existing memory without touching updated_at.

        Backfilling is not an edit the user made; bumping updated_at would
        reshuffle every "recent memories" view for a purely internal operation.
        """
        self._conn.execute(
            "UPDATE memories SET embedding=?, embedding_model=? WHERE id=? AND owner_account_id=?",
            (_encode_emb(vec), model, mid, account_id),
        )
        self._conn.commit()

    def embedding_coverage(self, model: str | None, *, account_id: str) -> dict:
        """How much of the memory store is actually recallable by meaning."""
        total = self._conn.execute(
            "SELECT COUNT(*) FROM memories WHERE owner_account_id=?",
            (account_id,)).fetchone()[0]
        usable = self._conn.execute(
            "SELECT COUNT(*) FROM memories WHERE owner_account_id=?"
            " AND embedding IS NOT NULL AND embedding_model IS ?",
            (account_id, model),
        ).fetchone()[0]
        return {"total": total, "usable": usable, "needing_backfill": total - usable}

    def update_memory(
        self,
        mid: str,
        *,
        text: str | None = None,
        type: str | None = None,
        confidence: float | None = None,
        entities: Iterable[str] | None = None,
        embedding: np.ndarray | None = None,
        account_id: str,
    ) -> Memory | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ? AND owner_account_id = ?",
            (mid, account_id)).fetchone()
        if not row:
            return None
        cols = [c[0] for c in self._conn.execute(
            "SELECT * FROM memories WHERE id = ? AND owner_account_id = ? LIMIT 1",
            (mid, account_id)).description]
        cur = dict(zip(cols, row))
        new_text = text if text is not None else cur["text"]
        new_type = type if type is not None else cur["type"]
        new_conf = confidence if confidence is not None else cur["confidence"]
        new_ents = list(entities) if entities is not None else json.loads(cur["entities"])
        new_emb_blob = _encode_emb(embedding) if embedding is not None else cur["embedding"]
        now = _now_ms()
        self._conn.execute(
            "UPDATE memories SET text=?, type=?, confidence=?, entities=?, updated_at=?, embedding=?,"
            " embedding_model=COALESCE(?, embedding_model)"
            " WHERE id=? AND owner_account_id=?",
            (new_text, new_type, new_conf, json.dumps(new_ents), now, new_emb_blob,
             # Only re-stamp when a NEW vector was supplied; COALESCE keeps the
             # existing stamp otherwise, so editing an unrelated field does not
             # relabel a vector that was made by a different model.
             _active_model() if embedding is not None else None,
             mid, account_id),
        )
        self._conn.commit()
        return Memory(
            id=mid, type=new_type, text=new_text, source=cur["source"],
            confidence=new_conf, entities=new_ents,
            created_at=cur["created_at"], updated_at=now,
        )

    def delete_memory(self, mid: str, *, account_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM memories WHERE id = ? AND owner_account_id = ?", (mid, account_id))
        self._conn.commit()
        return cur.rowcount > 0

    def list_memories(self, *, account_id: str) -> list[Memory]:
        rows = self._conn.execute(
            "SELECT id, type, text, source, confidence, entities, created_at, updated_at"
            " FROM memories WHERE owner_account_id = ? ORDER BY updated_at DESC",
            (account_id,)
        ).fetchall()
        return [
            Memory(
                id=r[0], type=r[1], text=r[2], source=r[3], confidence=r[4],
                entities=json.loads(r[5] or "[]"),
                created_at=r[6], updated_at=r[7],
            )
            for r in rows
        ]

    def search_memories(
        self,
        query_vec: np.ndarray,
        limit: int = 5,
        min_score: float = 0.4,
        type_filter: str | None = None,
        *,
        account_id: str,
    ) -> list[Memory]:
        """Linear cosine scan. query_vec must already be unit-normalized.
        Memory embeddings are normalized at write-time so cosine = dot.
        Optional `type_filter` restricts to a single memory type."""
        if type_filter is None:
            rows = self._conn.execute(
                "SELECT id, type, text, source, confidence, entities, created_at, updated_at, embedding,"
                " embedding_model"
                " FROM memories WHERE owner_account_id = ?",
                (account_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, type, text, source, confidence, entities, created_at, updated_at, embedding,"
                " embedding_model"
                " FROM memories WHERE owner_account_id = ? AND type = ?",
                (account_id, type_filter),
            ).fetchall()
        # Only vectors made by the ACTIVE model are comparable. The shape check
        # below catches a dimension change; it cannot catch two models at the
        # same dimension, whose vectors dot cleanly and rank confidently while
        # meaning nothing. Rows written before this column existed have a NULL
        # stamp and are skipped rather than trusted.
        active = _active_model()
        out: list[Memory] = []
        for r in rows:
            if active is not None and r[9] != active:
                continue
            emb = _decode_emb(r[8])
            if emb is None or emb.shape != query_vec.shape:
                continue
            score = float(np.dot(query_vec, emb))
            if score < min_score:
                continue
            out.append(Memory(
                id=r[0], type=r[1], text=r[2], source=r[3], confidence=r[4],
                entities=json.loads(r[5] or "[]"),
                created_at=r[6], updated_at=r[7], score=score,
            ))
        out.sort(key=lambda m: (m.score or 0), reverse=True)
        return out[:limit]

    def list_by_type(self, type_filter: str, limit: int | None = None,
                     *, account_id: str) -> list[Memory]:
        """All memories of a given type, ordered newest-first. Used for the
        always-on profile baseline injection (no semantic gate)."""
        sql = (
            "SELECT id, type, text, source, confidence, entities, created_at, updated_at"
            " FROM memories WHERE owner_account_id = ? AND type = ?"
            " ORDER BY confidence DESC, updated_at DESC"
        )
        params: tuple = (account_id, type_filter)
        if limit:
            sql += " LIMIT ?"
            params = (account_id, type_filter, limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            Memory(
                id=r[0], type=r[1], text=r[2], source=r[3], confidence=r[4],
                entities=json.loads(r[5] or "[]"),
                created_at=r[6], updated_at=r[7],
            )
            for r in rows
        ]

    # ── RAG document chunks (separate namespace) ─────────────────────

    def doc_exists(self, doc_id: str, *, account_id: str) -> int:
        """Number of stored chunks for this doc_id (0 => not ingested)."""
        row = self._conn.execute(
            "SELECT COUNT(*) FROM doc_chunks WHERE doc_id = ? AND owner_account_id = ?",
            (doc_id, account_id)
        ).fetchone()
        return int(row[0]) if row else 0

    def delete_doc_by_source(self, source: str, *, account_id: str) -> int:
        """Remove all chunks for a filename (used to replace an old version)."""
        cur = self._conn.execute(
            "DELETE FROM doc_chunks WHERE source = ? AND owner_account_id = ?",
            (source, account_id))
        self._conn.commit()
        return cur.rowcount

    def clear_all_doc_chunks(self, *, account_id: str) -> int:
        """Wipe EVERY document chunk (clear-all). Touches doc_chunks ONLY — the
        memories table (Nova's profile/episodic memory) is a separate table and
        is intentionally left untouched. Returns the number of chunks removed."""
        cur = self._conn.execute(
            "DELETE FROM doc_chunks WHERE owner_account_id = ?", (account_id,))
        self._conn.commit()
        return cur.rowcount

    def add_doc_chunk(
        self, *, doc_id: str, source: str, chunk_index: int, text: str,
        embedding: np.ndarray | None, account_id: str, org_id: str = "",
    ) -> None:
        self._conn.execute(
            "INSERT INTO doc_chunks (id, doc_id, source, chunk_index, text, created_at, embedding,"
            " owner_account_id, org_id)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex[:12], doc_id, source, chunk_index, text, _now_ms(),
             _encode_emb(embedding), account_id, org_id),
        )
        self._conn.commit()

    def search_doc_chunks(
        self, query_vec: np.ndarray, limit: int = 5, min_score: float = 0.35,
        *, account_id: str,
    ) -> list[dict]:
        """Linear cosine scan over DOC chunks only. query_vec unit-normalized.
        Returns [{source, chunk_index, text, score}] sorted best-first."""
        rows = self._conn.execute(
            "SELECT source, chunk_index, text, embedding FROM doc_chunks WHERE owner_account_id = ?",
            (account_id,)
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            emb = _decode_emb(r[3])
            if emb is None or emb.shape != query_vec.shape:
                continue
            score = float(np.dot(query_vec, emb))
            if score < min_score:
                continue
            out.append({"source": r[0], "chunk_index": r[1], "text": r[2], "score": score})
        out.sort(key=lambda d: d["score"], reverse=True)
        return out[:limit]

    def ranked_chunks(
        self, query_vec: np.ndarray, source: str | None = None, max_chars: int = 5000,
        *, account_id: str,
    ) -> list[dict]:
        """Source-verify escalation: rank a document's OWN chunks (or the whole
        corpus if source is None) by cosine with NO min-score floor, and return
        as many as fit `max_chars`. Used when normal top-k retrieval is weak —
        gives the model a fuller view of the actual source before any
        'not in the document' conclusion. Local-only, reuses stored embeddings."""
        if source is None:
            rows = self._conn.execute(
                "SELECT source, chunk_index, text, embedding FROM doc_chunks"
                " WHERE owner_account_id = ?",
                (account_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT source, chunk_index, text, embedding FROM doc_chunks"
                " WHERE owner_account_id = ? AND source = ?",
                (account_id, source),
            ).fetchall()
        scored: list[tuple[float, str, int, str]] = []
        for r in rows:
            emb = _decode_emb(r[3])
            if emb is None or emb.shape != query_vec.shape:
                continue
            scored.append((float(np.dot(query_vec, emb)), r[0], r[1], r[2]))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[dict] = []
        total = 0
        for score, src, idx, text in scored:
            if out and total + len(text) > max_chars:
                break
            out.append({"source": src, "chunk_index": idx, "text": text, "score": score})
            total += len(text)
        return out

    def list_docs(self, *, account_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT source, doc_id, COUNT(*), MAX(created_at) FROM doc_chunks"
            " WHERE owner_account_id = ? GROUP BY doc_id ORDER BY 4 DESC", (account_id,)
        ).fetchall()
        return [{"source": r[0], "doc_id": r[1], "chunks": int(r[2])} for r in rows]

    def first_doc_chunks(self, source: str, limit: int = 2, *, account_id: str) -> list[dict]:
        """Return a source's leading chunks VERBATIM (by chunk order) — a grounded
        preview of what was actually indexed, with no embedding/query needed.
        Used by the Docs tile's populate-on-index resting state."""
        rows = self._conn.execute(
            "SELECT source, chunk_index, text FROM doc_chunks"
            " WHERE owner_account_id = ? AND source = ? "
            "ORDER BY chunk_index ASC LIMIT ?",
            (account_id, source, max(1, limit)),
        ).fetchall()
        return [{"source": r[0], "chunk_index": int(r[1]), "text": r[2]} for r in rows]

    # ── RAG-graph view (read-only; never alters ingest/retrieval) ─────
    def graph_doc_chunks(self, *, account_id: str) -> list[dict]:
        """Every doc chunk WITH its decoded embedding — for the /rag/graph view."""
        rows = self._conn.execute(
            "SELECT id, source, chunk_index, text, embedding FROM doc_chunks"
            " WHERE owner_account_id = ? ORDER BY source, chunk_index",
            (account_id,)
        ).fetchall()
        return [
            {"id": r[0], "source": r[1], "chunk_index": int(r[2]), "text": r[3],
             "embedding": _decode_emb(r[4])}
            for r in rows
        ]

    def graph_memories(self, *, account_id: str) -> list[dict]:
        """Every memory WITH its decoded embedding — for the /rag/graph view."""
        rows = self._conn.execute(
            "SELECT id, type, text, source, embedding FROM memories"
            " WHERE owner_account_id = ? ORDER BY created_at",
            (account_id,)
        ).fetchall()
        return [
            {"id": r[0], "type": r[1], "text": r[2], "source": r[3],
             "embedding": _decode_emb(r[4])}
            for r in rows
        ]

    # ── Turns (persistent transcript) ────────────────────────────────

    def add_turn(self, session_id: str, role: str, text: str, model: str | None = None,
                 *, account_id: str, org_id: str = "") -> str:
        tid = uuid.uuid4().hex[:12]
        self._conn.execute(
            "INSERT INTO turns (id, session_id, role, text, model, ts,"
            " owner_account_id, org_id) VALUES (?,?,?,?,?,?,?,?)",
            (tid, session_id, role, text, model, _now_ms(), account_id, org_id),
        )
        self._conn.commit()
        return tid

    def recent_turns(self, session_id: str, limit: int = 20, *, account_id: str) -> list[dict]:
        # A session id alone is not an owner. Scoping on both means a guessed or
        # leaked session id still returns nothing to the wrong account.
        rows = self._conn.execute(
            "SELECT role, text, model, ts FROM turns"
            " WHERE owner_account_id = ? AND session_id = ?"
            " ORDER BY ts DESC LIMIT ?",
            (account_id, session_id, limit),
        ).fetchall()
        return [
            {"role": r[0], "text": r[1], "model": r[2], "ts": r[3]}
            for r in reversed(rows)   # chronological order
        ]
