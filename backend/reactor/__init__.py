"""Long-term memory and model providers.

This package is the home for the tiered-brain logic that will eventually
sit behind the FastAPI backend:

    reactor.providers   — uniform interface over API LLMs
    reactor.memory      — LanceDB (vectors) + SQLite (structured) wrappers

`reactor.router` (local-first tiered routing) is DELETED: it routed to the
Ollama provider, and the app is OpenRouter-only. Cheap-vs-strong
routing now lives on the chat lane — app/providers/chat.py :: resolve_auto_model.

Nothing here is wired into the running app yet. The modules exist so the
shape of the system is honest and the next step has a place to land.
"""
