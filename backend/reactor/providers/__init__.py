"""Provider implementations behind a single interface.

The base `Provider` defines the contract. Concrete providers wrap:
    - Anthropic Claude API
    - OpenAI API
    - Perplexity API

Nothing is implemented yet — see each module's docstring for what it WILL do.

The local Ollama provider that used to head this list is DELETED and is not
coming back here: Mark 4 is a hosted web app and OpenRouter is the only model
path (AGENTS.md). Local/edge models return later as a separate on-prem tier.
The live chat lane lives in app/providers/chat.py, not in this package.
"""

from .base import Provider, ProviderResponse  # noqa: F401
