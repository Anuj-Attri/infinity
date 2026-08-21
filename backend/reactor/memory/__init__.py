"""Local memory layer.

Two surfaces:
    store   — SQLite-backed structured store for memories + conversation turns
    embed   — semantic embedding. NO PROVIDER IN THIS BUILD: every call
              raises EmbedError until M5 lands Voyage `voyage-4` (1024-dim)

We keep this deliberately simple: vectors live as BLOBs in the same SQLite
row as the structured memory, queried via a numpy linear cosine scan. For
a personal memory store with <10K items this is microseconds per query —
no separate vector index needed. When the store grows past that, swap in
sqlite-vec or a dedicated index without touching the API.
"""

from .store import MemoryStore  # noqa: F401
from .embed import embed_text   # noqa: F401
