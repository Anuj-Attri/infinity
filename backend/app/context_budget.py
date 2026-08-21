"""Deterministic context budgeting — the constant-context contract.

The prompt is assembled by PRIORITY, not by hope:

    P0  system head (persona + identity + skills)   — never dropped
    P1  memory blocks                               — dropped last
    P2  history, newest-first sliding window        — dropped first,
                                                      oldest turns go

When history overflows the window, the dropped turns are replaced by ONE
compaction marker (and, when a summarizer is provided, a compact summary
of what fell off). Same inputs → same prompt. Token counts come from
tiktoken when installed, else a stable chars//4 estimate — both
deterministic.

Pure module: stdlib only, summarizer injected. Unit-testable.
"""

from __future__ import annotations

from typing import Awaitable, Callable

_ENC = None
_ENC_TRIED = False


def count_tokens(text: str) -> int:
    """Exact-ish when tiktoken is available, stable estimate otherwise."""
    global _ENC, _ENC_TRIED
    if not _ENC_TRIED:
        _ENC_TRIED = True
        try:
            import tiktoken  # optional dep — requirements lists it

            _ENC = tiktoken.get_encoding("o200k_base")
        except Exception:
            _ENC = None
    if _ENC is not None:
        try:
            return len(_ENC.encode(text))
        except Exception:
            pass
    return max(1, len(text) // 4)


def count_messages(msgs: list[dict]) -> int:
    return sum(count_tokens(str(m.get("content") or "")) + 4 for m in msgs)


COMPACTION_MARKER = "[[compacted: {n} earlier turns"


def apply_budget(
    msgs: list[dict],
    *,
    limit_tokens: int,
    head_count: int = 1,
    summary: str | None = None,
) -> tuple[list[dict], int]:
    """Fit `msgs` into `limit_tokens`.

    msgs layout (the shape _build_chat_messages produces):
        [system*head_count, ...history..., final user message]

    Returns (budgeted_msgs, dropped_turn_count). The system head and the
    final user message are never dropped; history is trimmed oldest-first.
    A marker message (optionally carrying `summary`) replaces whatever
    fell off. Deterministic for identical inputs.
    """
    if not msgs:
        return msgs, 0
    head_count = max(0, min(head_count, len(msgs)))
    head = msgs[:head_count]
    if len(msgs) - head_count <= 1:
        return msgs, 0
    tail = msgs[-1:]
    history = msgs[head_count:-1]

    fixed = count_messages(head) + count_messages(tail)
    room = limit_tokens - fixed
    if room <= 0:
        # Head + current message alone bust the limit — nothing sane to trim
        # here; send head+tail and let the provider's own limit rule.
        return head + tail, len(history)

    kept_rev: list[dict] = []
    used = 0
    for m in reversed(history):
        t = count_tokens(str(m.get("content") or "")) + 4
        if used + t > room:
            break
        kept_rev.append(m)
        used += t
    kept = list(reversed(kept_rev))
    dropped = len(history) - len(kept)
    if dropped <= 0:
        return msgs, 0

    marker_text = COMPACTION_MARKER.format(n=dropped) + "]]"
    if summary:
        marker_text = (COMPACTION_MARKER.format(n=dropped)
                       + f" — summary:]] {summary.strip()}")
    marker = {"role": "system", "content": marker_text}
    return head + [marker] + kept + tail, dropped


async def summarize_dropped(
    msgs: list[dict],
    summarizer: Callable[..., Awaitable[str | None]],
    max_tokens: int = 240,
) -> str | None:
    """Optional compaction summary for the turns about to fall off.
    `summarizer` is the app's utility LLM. Never raises."""
    try:
        convo = "\n".join(
            f"{m.get('role')}: {str(m.get('content'))[:400]}" for m in msgs[-24:]
        )
        return await summarizer(
            "Summarize this conversation fragment in <=120 words. Keep names, "
            "numbers, decisions, and open questions. No preamble.\n\n" + convo,
            temperature=0.0,
            max_tokens=max_tokens,
        )
    except Exception:
        return None
