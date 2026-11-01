"""Anthropic Claude provider — for hard turns the local model shouldn't take.

API key sourced from `ANTHROPIC_API_KEY`.
Not implemented.
"""

from __future__ import annotations

from .base import Provider, ProviderResponse


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, model: str = "claude-opus-4-7"):
        self.model = model

    async def complete(self, prompt: str, *, system: str | None = None) -> ProviderResponse:
        raise NotImplementedError("claude provider not wired yet")
