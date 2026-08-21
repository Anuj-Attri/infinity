"""The one place that turns text into a vector.

Every embedding in the product goes through `embed_texts()`. There are three
separate vector stores (the vault's `chunks.vec`, the memory store's
`memories.embedding`, and `doc_chunks`), and before this they each reached a
module that posted to a local Ollama host. In a container that host does not
exist, so every call raised and four features — vault search, note auto-linking,
vault graph edges, and session-overview grounding — were quietly dead.

THE FAILURE MODE THIS FILE EXISTS TO PREVENT is not "no embeddings". It is
*wrong* embeddings that look fine:

  * A ZERO VECTOR on failure. Cosine similarity against zeros is 0 or NaN for
    everything, so search returns plausible garbage — ranked, confident, and
    meaningless — instead of an error. Never do this. Raise.
  * MIXING MODELS OR DIMENSIONS in one index. Vectors from two different models
    are not comparable even at equal dimension; the numbers still multiply and
    still produce a ranking, so nothing looks broken. Every vector written now
    records the model and dimension that produced it, and readers must skip rows
    that do not match the active provider rather than compare across them.

Both are silent-corruption bugs, which is the class this codebase treats as
worse than a crash: a crash gets fixed, a confident wrong answer gets believed.
DIRECTION.md's first law — every claim carries a source — is worth nothing if
the retrieval that found the source was noise.

CONFIGURATION. `VOYAGE_API_KEY` enables the Voyage provider. Nothing else is
required; the model (`voyage-4`) and dimension (1024) are the decisions on
record and are overridable only for a deliberate migration. With no key the
provider is absent and every call raises EmbedError with a sentence saying so —
the same behaviour as before, which is the correct behaviour when unconfigured.
"""

from __future__ import annotations

import os
from typing import Literal

import httpx
import numpy as np

# The decision on record (AGENTS.md, docs/MARK4.md): Voyage voyage-4 at 1024
# dimensions, frozen. Overridable by env ONLY so a migration can be staged
# without a code change — changing either invalidates every stored vector, which
# is why `embedding_model` and `dim` are written alongside them.
VOYAGE_MODEL = (os.environ.get("VOYAGE_MODEL") or "voyage-4").strip()
VOYAGE_DIM = int(os.environ.get("VOYAGE_DIM") or "1024")
VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"

# Voyage distinguishes the two sides of a retrieval pair and it measurably
# matters: the same sentence is embedded differently as a stored document than
# as a question about one.
InputType = Literal["document", "query"]

_NO_PROVIDER = (
    "No embedding provider is configured. Set VOYAGE_API_KEY to enable "
    "embeddings (voyage-4, 1024-dim). Until then every embed call fails — "
    "never with a zero vector, which would make similarity search return "
    "plausible garbage instead of an error."
)


class EmbedError(RuntimeError):
    """Raised for every failure. There is no degraded success path."""


def _key() -> str:
    return (os.environ.get("VOYAGE_API_KEY") or "").strip()


def is_configured() -> bool:
    return bool(_key())


def active_model() -> str | None:
    """Model id to stamp on rows written now, or None if unconfigured."""
    return VOYAGE_MODEL if is_configured() else None


def active_dim() -> int | None:
    return VOYAGE_DIM if is_configured() else None


def status() -> dict[str, object]:
    """Matches the shape providers/chat.py reports, for /admin/diagnostics."""
    return {
        "provider": "voyage" if is_configured() else None,
        "configured": is_configured(),
        "model": VOYAGE_MODEL,
        "dim": VOYAGE_DIM,
        "detail": (
            f"Voyage {VOYAGE_MODEL} at {VOYAGE_DIM} dimensions."
            if is_configured() else _NO_PROVIDER
        ),
    }


def _to_unit(vec: list[float], where: str) -> np.ndarray:
    """Validate, then L2-normalise so cosine similarity is a plain dot product.

    The length check is not paranoia. A provider that silently returns a
    different dimension — a renamed model, a changed default — would otherwise
    write vectors that are incomparable with everything already stored, and
    nothing would look wrong until search results quietly became noise.
    """
    arr = np.asarray(vec, dtype=np.float32)
    if arr.ndim != 1 or arr.shape[0] != VOYAGE_DIM:
        raise EmbedError(
            f"{where}: expected a {VOYAGE_DIM}-dimension vector, got shape "
            f"{arr.shape}. Refusing to store a vector that cannot be compared "
            f"with the rest of the index."
        )
    norm = float(np.linalg.norm(arr))
    if not np.isfinite(norm) or norm <= 0:
        raise EmbedError(f"{where}: provider returned a zero or non-finite vector")
    return (arr / norm).astype(np.float32)


# Voyage caps a single request at 1,000 inputs. A vault re-index can exceed
# that in one call, and the failure would be a 400 at an unpredictable size.
_MAX_BATCH = 1000


async def embed_texts(texts: list[str], *, input_type: InputType = "document") -> list[np.ndarray]:
    """Embed a batch. Raises EmbedError unless every input embedded cleanly.

    Batching is the point: the vault re-indexes a note as many chunks at once,
    and one request per chunk is both slow and needlessly expensive.
    """
    if not texts:
        return []
    key = _key()
    if not key:
        raise EmbedError(_NO_PROVIDER)

    # Voyage rejects empty strings. Keep positions stable so callers can zip
    # results back onto their inputs.
    cleaned = [(t or "").strip() for t in texts]
    if any(not t for t in cleaned):
        raise EmbedError("refusing to embed an empty string — the caller should skip it")

    if len(cleaned) > _MAX_BATCH:
        out: list[np.ndarray] = []
        for i in range(0, len(cleaned), _MAX_BATCH):
            out.extend(await embed_texts(cleaned[i:i + _MAX_BATCH], input_type=input_type))
        return out

    payload = {
        "input": cleaned,
        "model": VOYAGE_MODEL,
        "input_type": input_type,
        "output_dimension": VOYAGE_DIM,
        # Default is true, but state it: an over-long chunk should be clipped,
        # not turn a whole re-index into a 400.
        "truncation": True,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                VOYAGE_URL,
                json=payload,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
            )
    except httpx.HTTPError as e:
        raise EmbedError(f"embedding request failed (transport: {e})") from e

    if r.status_code == 401:
        raise EmbedError("VOYAGE_API_KEY was rejected (HTTP 401)")
    if r.status_code == 429:
        raise EmbedError("Voyage rate limit (HTTP 429)")
    if r.status_code != 200:
        # The body carries Voyage's own reason (bad model id, bad dimension).
        # Include it: "HTTP 400" alone sends the reader looking in the wrong place.
        raise EmbedError(f"embedding request failed (HTTP {r.status_code}: {r.text[:200]})")

    try:
        data = r.json()["data"]
    except Exception as e:
        raise EmbedError(f"unreadable embedding response: {e}") from e

    if len(data) != len(cleaned):
        raise EmbedError(
            f"asked for {len(cleaned)} embeddings, got {len(data)} — refusing to "
            f"guess which text each vector belongs to"
        )

    # Voyage documents that results carry an index; do not assume input order.
    ordered = sorted(data, key=lambda d: d.get("index", 0))
    return [_to_unit(d["embedding"], f"embedding[{i}]") for i, d in enumerate(ordered)]


async def embed_text(text: str, *, input_type: InputType = "document") -> np.ndarray:
    """Single-string convenience. Same contract: raises rather than degrading."""
    text = (text or "").strip()
    if not text:
        # An empty input is the caller's bug, not a provider failure, and a
        # zero vector here cannot poison an index because there is nothing to
        # store. Callers already skip empty chunks.
        raise EmbedError("cannot embed an empty string")
    out = await embed_texts([text], input_type=input_type)
    return out[0]
