"""Common provider interface.

Every concrete provider (Claude, OpenAI, Perplexity) implements
`Provider.complete()`. The caller picks one per turn; the rest of the app
should never branch on which provider is in use.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderResponse:
    text: str
    provider: str
    model: str
    # Token counts, latency, cost, etc. land here when we start measuring.


class Provider:
    """Base class — concrete providers override `name`, `model`, and `complete`."""

    name: str = "base"
    model: str = ""

    async def complete(self, prompt: str, *, system: str | None = None) -> ProviderResponse:
        raise NotImplementedError
