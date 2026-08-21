"""Perplexity provider — for turns that need fresh web grounding.

API key from `PERPLEXITY_API_KEY`. Not implemented.
"""

from __future__ import annotations

from .base import Provider, ProviderResponse


class PerplexityProvider(Provider):
    name = "perplexity"

    def __init__(self, model: str = "sonar-pro"):
        self.model = model

    async def complete(self, prompt: str, *, system: str | None = None) -> ProviderResponse:
        raise NotImplementedError("perplexity provider not wired yet")
