"""Embeddings — NO PROVIDER IS CONFIGURED IN THIS BUILD.

The `nomic-embed-text` lane this module calls was served by a local Ollama
container that no longer exists (AGENTS.md: Ollama is out of scope). Every
call therefore fails, loudly, with EmbedError.

Embeddings land at M5: Voyage `voyage-4` (1024-dim) against Postgres/pgvector,
with `embedding_model` recorded per chunk. EMBED_DIM stays 768 here until that
lands — do not repoint it ahead of the column.

Failure is never softened into a zero vector: a zero vector makes every
similarity search return plausible garbage instead of an error.

The function returns a numpy float32 array suitable for cosine similarity
against other embeddings in the store.
"""

from __future__ import annotations

import os

import httpx
import numpy as np

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768  # nomic-embed-text


class EmbedError(RuntimeError):
    pass


# Every failure path carries the SAME plain sentence: there is no embedding
# provider in this build. The low-level cause is appended in parentheses for
# diagnosis — it is never a hint to go install something local.
_NOT_CONFIGURED = (
    "No embedding provider is configured in this build. Embeddings land at M5: "
    "Voyage `voyage-4` (1024-dim) against Postgres/pgvector. Until then every "
    "embed call fails — never with a zero vector, which would make similarity "
    "search return plausible garbage instead of an error."
)


async def embed_text(text: str) -> np.ndarray:
    """Embed a single string. Returns a float32 numpy array."""
    text = (text or "").strip()
    if not text:
        return np.zeros(EMBED_DIM, dtype=np.float32)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{OLLAMA_HOST}/api/embed",
                json={"model": EMBED_MODEL, "input": text},
            )
    except httpx.HTTPError as e:
        raise EmbedError(f"{_NOT_CONFIGURED} (transport: {e})") from e
    if r.status_code == 404:
        raise EmbedError(f"{_NOT_CONFIGURED} (model '{EMBED_MODEL}' not served)")
    if r.status_code != 200:
        raise EmbedError(f"{_NOT_CONFIGURED} (HTTP {r.status_code})")
    body = r.json()
    # Newer /api/embed returns {"embeddings": [[...]]}
    arr = body.get("embeddings") or body.get("embedding")
    if not arr:
        raise EmbedError(f"{_NOT_CONFIGURED} (no embedding in response)")
    # Handle either [[...]] (plural) or [...] (legacy singular).
    flat = arr[0] if isinstance(arr[0], list) else arr
    vec = np.asarray(flat, dtype=np.float32)
    # Normalize so cosine sim is a dot product.
    n = np.linalg.norm(vec)
    if n > 0:
        vec = vec / n
    return vec
