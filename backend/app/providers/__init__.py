"""Provider registries.

TWO STRICTLY-SEPARATED REGISTRIES:

  chat       — user-facing chat models. Local Qwen3 (always-on) + BYOK
               premium keys to Anthropic / OpenAI / Gemini. Direct-billed
               to the user. These models populate the chat selector.

  dispatch   — sub-agent / orchestration infra (NEXT PASS). OpenRouter
               only. Cost-isolated. NEVER appears in the chat selector,
               not even by accident: the import path is different, the
               registry class is different, and the API surfaces don't
               talk to each other.

The split is in CODE, not by convention. Future maintainers (and Nova
herself) should physically be unable to cross-pollute the two. If you
ever find yourself reaching for `chat` from a sub-agent path or for
`dispatch` from the chat path, stop — that's the design boundary.
"""

from . import chat as chat       # noqa: F401, PLW0127  (re-export for callers)
from . import dispatch as dispatch  # noqa: F401, PLW0127
