"""Session-overview RAG — the live state of the world for THIS session.

A dedicated, EPHEMERAL, SINGLE-SLOT vector index, separate from everything else:
  - NOT the SQLite `memories` table (the relevance gate / persistent memory)
  - NOT `doc_chunks` (uploaded-document RAG)
It lives in process RAM only — lost on restart by design ("the present matters
most during a session"). Each build OVERWRITES the prior slot (no stacking, no
accumulation across sessions).

Built from the frontend's live tile gather (the same paths /summarize and the
daily greeting use), embedded with the existing local nomic-embed stack and
chunked with the existing RAG line-aware chunker — no new infra. Chat silently
retrieves from it each turn for grounding, so Nova speaks from REAL ingested
data (real NVDA number, real headlines) instead of fake-fetch narration.
"""

from __future__ import annotations

import asyncio

import numpy as np

from reactor.memory import embed_text
from .chunking import chunk_text as _chunk   # rag.py is retired; the chunker moved

# Single ephemeral slot: list of {text, vec(unit-normalized np.float32)}.
_CHUNKS: list[dict] = []


async def set_overview(text: str) -> int:
    """Chunk + embed `text` and REPLACE the slot (overwrite — no stacking).
    Returns the chunk count. Fail-soft: a chunk that fails to embed is skipped;
    a total failure leaves the slot empty (chat just runs ungrounded)."""
    global _CHUNKS
    pieces = _chunk(text or "")
    built: list[dict] = []
    for p in pieces:
        try:
            vec = await embed_text(p)
        except Exception:
            continue
        built.append({"text": p, "vec": vec})
    _CHUNKS = built          # atomic overwrite of the single slot
    return len(_CHUNKS)


def search_overview(qvec: np.ndarray, k: int = 8, min_score: float = 0.2) -> list[str]:
    """Top-k session-overview chunks by cosine (vectors are unit-normalized, so
    dot == cosine). Generous k + low floor because the overview is small — we
    want the live context to surface, not be filtered away. Returns [] when the
    slot is empty (chat runs ungrounded, never crashes)."""
    if not _CHUNKS:
        return []
    scored = sorted(
        ((float(np.dot(qvec, c["vec"])), c["text"]) for c in _CHUNKS),
        key=lambda x: x[0],
        reverse=True,
    )
    return [t for s, t in scored[:k] if s >= min_score]


def has_overview() -> bool:
    return bool(_CHUNKS)


def clear_overview() -> None:
    global _CHUNKS
    _CHUNKS = []
