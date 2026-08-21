"""Line-aware document chunking.

Lifted verbatim from Mark 3's `rag.py`, which is retired — the Vault is the one
knowledge base. The chunker itself was never the problem with that module and it
encodes a real lesson, so it moves here rather than dying with its old home.

`session_overview.py` imported it from `rag.py` even after that router was
unmounted, which is how a retired module stays alive by accident.

`retrieval.ingest_text` (M5) is the other caller: chunking is the write half of
retrieval, and both halves should agree on what a chunk is.
"""

from __future__ import annotations

# ~225 tokens. Sized for an embedding model's context and for retrieval quality:
# chunks much larger dilute the vector, much smaller lose the surrounding fact.
CHUNK_CHARS = 900
# Overlap so a fact spanning a boundary stays retrievable from either side.
CHUNK_OVERLAP = 150
# Safety cap per document, so one pathological upload cannot fill the index.
MAX_CHUNKS = 400


def chunk_text(text: str) -> list[str]:
    """Split into overlapping chunks WITHOUT ever breaking inside a line.

    The line-awareness is the whole point and it came from a real failure: a
    table row or label-value pair like "Annotated images: 838" split mid-row
    across two chunks embeds and retrieves badly — neither half carries the
    fact, so the answer is missing a number that is plainly in the document.
    Whole lines accumulate up to CHUNK_CHARS; a few trailing lines carry over as
    overlap; and a single pathologically long line is hard-split as a fallback.
    """
    text = (text or "").strip()
    if not text:
        return []
    lines = text.split("\n")
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0

    def flush() -> list[str]:
        nonlocal cur, cur_len
        block = "\n".join(cur).strip()
        # Carry the last few lines forward as overlap (up to CHUNK_OVERLAP).
        keep: list[str] = []
        kl = 0
        for ln in reversed(cur):
            if kl >= CHUNK_OVERLAP:
                break
            keep.insert(0, ln)
            kl += len(ln) + 1
        cur = keep[:]
        cur_len = sum(len(x) + 1 for x in cur)
        return [block] if block else []

    for raw in lines:
        # Hard-split a pathologically long single line (rare).
        seg_list = [raw]
        if len(raw) > CHUNK_CHARS:
            seg_list = [raw[i:i + CHUNK_CHARS] for i in range(0, len(raw), CHUNK_CHARS)]
        for seg in seg_list:
            seg_len = len(seg) + 1
            if cur and cur_len + seg_len > CHUNK_CHARS:
                chunks.extend(flush())
                if len(chunks) >= MAX_CHUNKS:
                    return chunks
            cur.append(seg)
            cur_len += seg_len

    if cur and len(chunks) < MAX_CHUNKS:
        block = "\n".join(cur).strip()
        if block:
            chunks.append(block)
    return [c for c in chunks if c]


# Mark 3's name, kept so lifted call sites do not all have to change at once.
_chunk = chunk_text
