"""OpenAI provider. API key from `OPENAI_API_KEY`. Not implemented."""

from __future__ import annotations

from .base import Provider, ProviderResponse


class OpenAIProvider(Provider):
    name = "openai"

    def __init__(self, model: str = "gpt-5"):
        self.model = model

    async def complete(self, prompt: str, *, system: str | None = None) -> ProviderResponse:
        raise NotImplementedError("openai provider not wired yet")
