"""The chunker's line-awareness is the whole point, and it came from a real bug.

A table row or label-value pair split across two chunks means NEITHER half
carries the fact. "Annotated images: 838" becomes "Annotated images:" in one
chunk and "838" in another, both embed badly, and the answer is missing a number
that is plainly in the document. Nothing errors; the answer is just wrong.

That property is invisible in the code — it looks like ordinary accumulation —
so it is pinned here.
"""

from __future__ import annotations

from app.chunking import CHUNK_CHARS, MAX_CHUNKS, chunk_text


def test_empty_input_yields_nothing() -> None:
    assert chunk_text("") == []
    assert chunk_text("   \n  \n ") == []


def test_short_text_is_one_chunk() -> None:
    assert chunk_text("a single short line") == ["a single short line"]


def test_a_label_value_line_is_never_split_across_chunks() -> None:
    """The regression that motivated line-awareness."""
    needle = "Annotated images: 838"
    filler = "\n".join(f"filler line {i} with some words in it" for i in range(200))
    chunks = chunk_text(filler + "\n" + needle + "\n" + filler)

    assert any(needle in c for c in chunks), "the fact was split and is now unretrievable"
    # And it must not appear only as a broken fragment somewhere.
    assert not any(c.rstrip().endswith("Annotated images:") for c in chunks)


def test_no_chunk_ends_mid_line() -> None:
    text = "\n".join(f"line {i}: value-{i} which is reasonably long text here" for i in range(300))
    for c in chunk_text(text):
        # Every chunk boundary must fall on a line boundary, so every line in a
        # chunk is a complete line from the source.
        for line in c.split("\n"):
            assert line in text, f"chunk contains a partial line: {line[:60]!r}"


def test_overlap_keeps_boundary_context_retrievable() -> None:
    lines = [f"row {i}" for i in range(400)]
    chunks = chunk_text("\n".join(lines))
    assert len(chunks) > 1, "test needs multiple chunks to be meaningful"
    # Consecutive chunks should share something, or a fact spanning the boundary
    # is retrievable from neither side.
    a, b = set(chunks[0].split("\n")), set(chunks[1].split("\n"))
    assert a & b, "no overlap between consecutive chunks"


def test_a_single_over_long_line_is_hard_split_rather_than_dropped() -> None:
    monster = "x" * (CHUNK_CHARS * 5)
    chunks = chunk_text(monster)
    assert chunks, "an over-long line must not vanish"
    assert sum(len(c) for c in chunks) >= CHUNK_CHARS * 4


def test_chunk_count_is_capped() -> None:
    """One pathological upload must not be able to fill the index."""
    huge = "\n".join(f"line {i}" for i in range(MAX_CHUNKS * 40))
    assert len(chunk_text(huge)) <= MAX_CHUNKS


def test_no_empty_chunks_are_emitted() -> None:
    text = "a\n\n\n\nb\n\n\n" + ("\n" * 50) + "c"
    assert all(c.strip() for c in chunk_text(text))
