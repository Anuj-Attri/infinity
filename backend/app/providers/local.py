"""The local model lane: an OpenAI-compatible server you run yourself.

Used only as a fallback, when the hosted model cannot answer: the spend limit
is reached, the provider reports the key is out of credit or rate limited, or
the provider is unreachable before anything was generated.

Off unless configured:
    LOCAL_MODEL_URL   base URL ending in /v1, e.g. http://localhost:11434/v1
                      (Ollama), http://localhost:1234/v1 (LM Studio),
                      http://localhost:8080/v1 (llama.cpp server)
    LOCAL_MODEL       the model name that server knows, e.g. llama3.2

No killswitch check, on purpose. The killswitch stops SPEND, and a model on
your own hardware spends nothing. That is also why the function is not named
like the billing methods the killswitch ratchet looks for.

Never silent: the caller labels every reply that came from here.
"""

from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)


def configured() -> bool:
    return bool(base_url() and model_name())


def base_url() -> str:
    return (os.environ.get("LOCAL_MODEL_URL") or "").strip().rstrip("/")


def model_name() -> str:
    return (os.environ.get("LOCAL_MODEL") or "").strip()


class LocalModelError(Exception):
    """The local server refused or could not be reached."""


async def stream_chat(messages: list[dict], *, temperature: float | None = None
                      ) -> AsyncIterator[str]:
    """Yield reply text as it streams. Raises LocalModelError on failure."""
    if not configured():
        raise LocalModelError("no local model configured")
    payload: dict = {"model": model_name(), "messages": messages, "stream": True}
    if temperature is not None:
        payload["temperature"] = temperature
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            async with client.stream("POST", f"{base_url()}/chat/completions",
                                     json=payload) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode("utf-8", "replace")[:200]
                    raise LocalModelError(f"local model HTTP {r.status_code}: {body}")
                async for line in r.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        delta = json.loads(data)["choices"][0].get("delta") or {}
                    except (ValueError, KeyError, IndexError):
                        continue
                    piece = delta.get("content")
                    if piece:
                        yield piece
    except httpx.HTTPError as e:
        raise LocalModelError(f"local model unreachable: {type(e).__name__}: {e}") from e
