"""Embeddings — the compatibility shim in front of the real provider.

This module used to POST to a local Ollama host serving `nomic-embed-text`. That
host does not exist in a container, so every call raised and four features died
quietly: vault search, note auto-linking, vault graph edges, and session-overview
grounding. The provider now lives at `app.providers.embeddings` (Voyage
`voyage-4`, 1024-dim) and this file only forwards to it.

It is kept, rather than deleted and every import rewritten, because it is the
name six call sites already reach for. Redirecting one module is a smaller and
much more reviewable change than editing six — and it leaves exactly one place
to look when the next question is "where do embeddings actually come from".

THE CONTRACT IS UNCHANGED, AND THE IMPORTANT HALF OF IT IS THE FAILURE:
a failure raises EmbedError. It never returns a zero vector, because a zero
vector makes every similarity search return plausible garbage — ranked and
confident — instead of an error.

`EMBED_DIM` is now the ACTIVE provider's dimension rather than a constant. It
used to be 768 (nomic-embed-text) with a note not to repoint it ahead of the
storage column; the storage is a BLOB either way, and what actually keeps
vectors comparable is that each row now records the model and dimension that
produced it. See `app/providers/embeddings.py`.
"""

from __future__ import annotations

import numpy as np

from app.providers.embeddings import (  # noqa: F401  (re-exported on purpose)
    EmbedError,
    active_dim,
    active_model,
    embed_texts,
    is_configured,
)
from app.providers.embeddings import VOYAGE_DIM as EMBED_DIM
from app.providers.embeddings import embed_text as _embed_text


async def embed_text(text: str, *, input_type: str = "document") -> np.ndarray:
    """Embed a single string. Returns a float32, L2-normalised numpy array.

    `input_type` reaches Voyage, which embeds the same sentence differently as a
    stored document than as a question about one. Callers that are SEARCHING
    should pass "query"; callers that are INDEXING should leave the default.
    """
    return await _embed_text(text, input_type=input_type)  # type: ignore[arg-type]
