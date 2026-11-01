"""The ONE entry point for a turn.

Routers are thin adapters: parse the request, resolve the account, call `run_turn`.
Nothing else owns a turn. Orchestration spread across many modules leaves no single
place that can be given an account, and that is what makes tenancy hard to add later.

Keeping it here has a second payoff: `run_turn` is callable from a test with no FastAPI
in the picture, so the interesting logic is testable without spinning up a server.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .accounts import Account
from .retrieval import Chunk


@dataclass(frozen=True)
class Citation:
    """A source the answer actually leaned on.

    Product law #1: every claim carries a source link, and an answer without one is a
    bug. Citations are built from the chunks that were retrieved — never asked of the
    model, which is how the desktop version's reference product ended up fabricating 4 of 5 URLs.
    """

    document_id: str
    title: str
    source_url: str | None
    quote: str | None = None


@dataclass(frozen=True)
class TurnResult:
    """What one turn produced.

    `truncated` and `finish_reason` are NOT optional extras. Silent truncation
    has two known causes: reasoning tokens consuming the entire visible answer, and a
    length cut mid-sentence that nothing reported. A turn that was cut short must say
    so, out loud, every time.
    """

    text: str
    citations: tuple[Citation, ...] = ()
    model: str | None = None
    finish_reason: str | None = None
    truncated: bool = False
    # Populated when retrieval ran. EMPTY IS A LEGITIMATE ANSWER — it is what triggers
    # the refusal product law #3 requires, and it must never be "fixed" by widening scope.
    chunks: tuple[Chunk, ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)


async def run_turn(
    *,
    account: Account,
    message: str,
    ctx: dict[str, Any] | None = None,
) -> TurnResult:
    """Run one turn on behalf of exactly one account.

    `account` is keyword-only and required. Every downstream call that touches state
    receives it explicitly; nothing reads a module-level "current user", because the
    failure mode of forgetting is silent — the wrong person gets a correct-looking,
    correctly-cited answer.

    Order, deny by default, is established before this
    function is reached: authenticate -> resolve account -> check entitlement -> run.
    By the time `run_turn` executes, the caller is known and entitled.
    """
    raise NotImplementedError(
        "Planned: assemble persona + skills + memory under the context budget, retrieve "
        "with `account`, dispatch to the model, parse tool calls, record spend, and "
        "persist the turn. Streaming callers consume the NDJSON adapter in routers/."
    )
