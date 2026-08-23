"""Chat-provider registry — OpenRouter + BYOK premium keys.

Each provider class implements:
    .list_models() -> [{ id, display_name }]
    .stream(messages, system, no_think, temperature)
        -> async iterator[str]   (text deltas, provider-native SSE
                                  normalized to plain text chunks)

The registry holds:
  * OpenRouter — the primary (and only) hosted language path.
  * any premium provider the user has connected (key stored locally,
    validated at connect time).

The chat-stream route in main.py looks up the provider for the selected
model_id via `registry.provider_for_model(...)`. No cross-talk with the
dispatch registry — sub-agents live in a different module entirely.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable, Protocol, runtime_checkable

import httpx

from . import keystore

# The router sentinel. Not a real upstream model — the chat route resolves it
# per-turn via resolve_auto_model(). The local-first lane it used to choose
# from is DELETED (AGENTS.md: OpenRouter only; local/edge comes back later as
# an on-prem tier), so the label no longer promises one. The seam stays: the
# cheap-vs-strong routing it exists for is still wanted, it just has no local
# option now.
AUTO_MODEL_ID = "infinity/auto"
AUTO_MODEL_LABEL = "Auto"

# What the sentinel resolves to. Env-overridable (AUTO_API_MODEL, the var Mark 3
# used) so a deploy can move the default without a code change. KEEP THIS VALUE
# IN SYNC with app/main.py :: _AUTO_API_MODEL — same env var, and two disagreeing
# code defaults would route the same sentinel to two different models.
AUTO_API_MODEL = os.environ.get("AUTO_API_MODEL", "anthropic/claude-sonnet-5")

log = logging.getLogger(__name__)


def resolve_auto_model() -> str:
    """Resolve AUTO_MODEL_ID to a concrete model id for this turn. The chat
    route calls this BEFORE provider lookup so the done line reports the model
    that actually answered. One lane today; a cheap/strong split lands here,
    not at the call site."""
    return AUTO_API_MODEL


# ---- key storage ------------------------------------------------------------
#
# .data/chat_keys.json — encrypted-at-rest via Windows DPAPI (see
# providers/keystore.py). Each value on disk is base64 DPAPI ciphertext
# bound to the current Windows user; opening the file in a text editor
# shows opaque base64, not the raw key. Decryption only succeeds on the
# same user account on the same machine. Decrypted values live in
# process memory only — never logged, never echoed in any API response.

_LAST_LOAD_REPORT: keystore.KeyStoreReport = keystore.KeyStoreReport(needs_reconnect=[])


def _load_keys() -> dict[str, str]:
    global _LAST_LOAD_REPORT
    keys, report = keystore.load_keys()
    _LAST_LOAD_REPORT = report
    if report.needs_reconnect:
        log.warning("chat keystore: %d entr%s failed to decrypt (likely "
                    "moved between users/machines): %s — user must reconnect.",
                    len(report.needs_reconnect),
                    "y" if len(report.needs_reconnect) == 1 else "ies",
                    ",".join(report.needs_reconnect))
    return keys


def _save_keys(d: dict[str, str]) -> None:
    keystore.save_keys(d)


def needs_reconnect() -> list[str]:
    """Providers whose stored ciphertext failed to decrypt under the
    current Windows user. The UI surfaces these as 'reconnect required'."""
    return list(_LAST_LOAD_REPORT.needs_reconnect)


def keystore_backend() -> str:
    return keystore.backend_name()


# ---- types ------------------------------------------------------------------

@dataclass
class ModelInfo:
    id: str
    display_name: str

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "display_name": self.display_name}


class ProviderError(RuntimeError):
    """UI-safe error message from a provider call."""


# Billing / quota rejections must surface as a FULLY-OPAQUE "Model unavailable."
# — never the provider's billing reason. Matched by HTTP status OR body keyword
# (some providers return 400/403 with "credit balance too low" etc.).
MODEL_UNAVAILABLE = "Model unavailable."
_BILLING_STATUSES = frozenset({402, 429})
_BILLING_KEYWORDS = (
    "insufficient", "balance", "credit", "quota", "billing",
    "payment", "exceeded", "too low",
)


def _http_provider_error(name: str, status: int, raw: bytes) -> "ProviderError":
    """Map a provider non-200 to a user-safe ProviderError. Billing/quota →
    exactly MODEL_UNAVAILABLE (no reason leaked). Other errors are abstracted to
    a short, body-free message (the response text is never surfaced)."""
    body = ""
    try:
        body = raw[:500].decode("utf-8", "replace").lower()
    except Exception:
        body = ""
    if status in _BILLING_STATUSES or any(k in body for k in _BILLING_KEYWORDS):
        return ProviderError(MODEL_UNAVAILABLE)
    return ProviderError(f"{name} HTTP {status}")


@runtime_checkable
class ChatProvider(Protocol):
    """All chat providers expose the same surface — OpenRouter (the primary
    language path) and every BYOK provider in this module conform."""

    name: str

    async def list_models(self) -> list[ModelInfo]: ...

    def supports_no_think(self, model: str) -> bool: ...

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        no_think: bool,
        temperature: float | None,
    ) -> AsyncIterator[str]: ...


# ---- helpers ----------------------------------------------------------------

# Marker main.py plants between the STABLE system-prompt prefix (persona, tool
# docs, manifest) and the per-turn tail (skills, session overview, memories).
# OpenRouter turns it into an Anthropic cache_control breakpoint; every other
# provider strips it (an ASCII record separator would otherwise leak upstream).
CACHE_BOUNDARY = "\x1e"


def _strip_cache_boundary(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        c = m.get("content")
        if isinstance(c, str) and CACHE_BOUNDARY in c:
            m = {**m, "content": c.replace(CACHE_BOUNDARY, "")}
        out.append(m)
    return out


def _split_system(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Many providers (Anthropic, Gemini) want the system prompt as a
    SEPARATE top-level field, not as a role:"system" message. Extract it."""
    messages = _strip_cache_boundary(messages)
    if messages and messages[0].get("role") == "system":
        return str(messages[0].get("content") or ""), messages[1:]
    return None, messages


# ---- Anthropic --------------------------------------------------------------

ANTHROPIC_DEFAULT_MODELS = [
    ("claude-fable-5",                "Claude Fable 5"),
    ("claude-opus-4-8",               "Claude Opus 4.8"),
    ("claude-sonnet-4-6",             "Claude Sonnet 4.6"),
    ("claude-haiku-4-5-20251001",     "Claude Haiku 4.5"),
]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, key: str):
        self._key = key

    @staticmethod
    async def validate(key: str) -> bool:
        # The /v1/models endpoint is the cheapest auth probe.
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(
                    "https://api.anthropic.com/v1/models?limit=5",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                )
            return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[ModelInfo]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(
                    "https://api.anthropic.com/v1/models?limit=50",
                    headers={"x-api-key": self._key, "anthropic-version": "2023-06-01"},
                )
            if r.status_code == 200:
                data = r.json().get("data") or []
                return [
                    ModelInfo(id=m["id"], display_name=m.get("display_name") or m["id"])
                    for m in data if m.get("id")
                ]
        except Exception as e:
            log.warning("anthropic list_models failed: %s", e)
        # Conservative fallback — known model IDs as of the cutoff.
        return [ModelInfo(id=i, display_name=n) for i, n in ANTHROPIC_DEFAULT_MODELS]

    def supports_no_think(self, model: str) -> bool:
        # Anthropic models don't use the qwen3 /no_think prefix; we just
        # pass user text straight through. "Fast" toggle is a no-op here.
        return False

    async def stream(
        self, *, model: str, messages: list[dict[str, Any]],
        no_think: bool, temperature: float | None,
    ) -> AsyncIterator[str]:
        # KILLSWITCH. This is the lane /chat and /chat/stream actually use,
        # so without it the main chat path kept spending while the switch
        # was on. At the provider layer, not a route dependency, because a
        # mission running in a background task is on no request path.
        from ..entitlements import assert_may_spend
        assert_may_spend()
        system, rest = _split_system(messages)
        # Anthropic only allows alternating user/assistant turns. The
        # backend's _build_chat_messages already produces that pattern.
        payload: dict[str, Any] = {
            "model":      model,
            "messages":   [{"role": m["role"], "content": m["content"]} for m in rest],
            # Anthropic REQUIRES max_tokens. 8192 (supported by every current
            # Claude model) so a long answer + trailing <nova_action> block
            # never hits finish=length mid-JSON. The OpenRouter lane omits the
            # field entirely (model default, verified >10k tokens uncut).
            "max_tokens": 8192,
            "stream":     True,
        }
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = float(temperature)

        headers = {
            "x-api-key":          self._key,
            "anthropic-version":  "2023-06-01",
            "content-type":       "application/json",
        }
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", "https://api.anthropic.com/v1/messages",
                                json=payload, headers=headers) as r:
                if r.status_code != 200:
                    raw = await r.aread()
                    raise _http_provider_error("Anthropic", r.status_code, raw)
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    body = line[len("data:"):].strip()
                    if body == "[DONE]":
                        break
                    try:
                        ev = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    t = ev.get("type")
                    if t == "content_block_delta":
                        delta = ev.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield delta.get("text") or ""
                    elif t == "message_stop":
                        break


# ---- OpenAI -----------------------------------------------------------------

OPENAI_DEFAULT_MODELS = [
    ("gpt-5",         "GPT-5"),
    ("gpt-5-mini",    "GPT-5 Mini"),
    ("gpt-4o",        "GPT-4o"),
    ("gpt-4o-mini",   "GPT-4o Mini"),
]


class OpenAIProvider:
    name = "openai"

    def __init__(self, key: str):
        self._key = key

    @staticmethod
    async def validate(key: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get("https://api.openai.com/v1/models",
                                headers={"Authorization": f"Bearer {key}"})
            return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[ModelInfo]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get("https://api.openai.com/v1/models",
                                headers={"Authorization": f"Bearer {self._key}"})
            if r.status_code == 200:
                data = r.json().get("data") or []
                # Keep only chat-capable model families; drop fine-tunes/embeddings/audio/etc.
                keep_prefixes = ("gpt-5", "gpt-4o", "gpt-4.1", "o1", "o3", "o4")
                out: list[ModelInfo] = []
                for m in data:
                    mid = m.get("id") or ""
                    if not any(mid.startswith(p) for p in keep_prefixes):
                        continue
                    if "audio" in mid or "transcribe" in mid or "tts" in mid or "embedding" in mid:
                        continue
                    out.append(ModelInfo(id=mid, display_name=mid))
                if out:
                    return sorted(out, key=lambda x: x.id)
        except Exception as e:
            log.warning("openai list_models failed: %s", e)
        return [ModelInfo(id=i, display_name=n) for i, n in OPENAI_DEFAULT_MODELS]

    def supports_no_think(self, model: str) -> bool:
        return False

    async def stream(
        self, *, model: str, messages: list[dict[str, Any]],
        no_think: bool, temperature: float | None,
    ) -> AsyncIterator[str]:
        # KILLSWITCH. This is the lane /chat and /chat/stream actually use,
        # so without it the main chat path kept spending while the switch
        # was on. At the provider layer, not a route dependency, because a
        # mission running in a background task is on no request path.
        from ..entitlements import assert_may_spend
        assert_may_spend()
        payload: dict[str, Any] = {
            "model":    model,
            "messages": _strip_cache_boundary(messages),
            "stream":   True,
        }
        if temperature is not None:
            payload["temperature"] = float(temperature)
        headers = {
            "Authorization": f"Bearer {self._key}",
            "content-type":  "application/json",
        }
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", "https://api.openai.com/v1/chat/completions",
                                json=payload, headers=headers) as r:
                if r.status_code != 200:
                    raw = await r.aread()
                    raise _http_provider_error("OpenAI", r.status_code, raw)
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    body = line[len("data:"):].strip()
                    if body == "[DONE]":
                        break
                    try:
                        ev = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    for ch in (ev.get("choices") or []):
                        delta = (ch.get("delta") or {}).get("content")
                        if delta:
                            yield delta


# ---- Gemini -----------------------------------------------------------------

GEMINI_DEFAULT_MODELS = [
    ("gemini-2.0-flash-exp",     "Gemini 2.0 Flash (Exp)"),
    ("gemini-1.5-pro-latest",    "Gemini 1.5 Pro"),
    ("gemini-1.5-flash-latest",  "Gemini 1.5 Flash"),
]


def _to_gemini_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """OpenAI-style messages -> Gemini `contents`. Gemini uses 'model' as
    the assistant role name and parts: [{text: ...}]. System prompts go
    in a separate `systemInstruction` field."""
    system, rest = _split_system(messages)
    contents: list[dict[str, Any]] = []
    for m in rest:
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": str(m.get("content") or "")}]})
    return system, contents


class GeminiProvider:
    name = "gemini"

    def __init__(self, key: str):
        self._key = key

    @staticmethod
    async def validate(key: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
            return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[ModelInfo]:
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={self._key}")
            if r.status_code == 200:
                items = r.json().get("models") or []
                out: list[ModelInfo] = []
                for m in items:
                    name = (m.get("name") or "").removeprefix("models/")
                    methods = m.get("supportedGenerationMethods") or []
                    if "generateContent" not in methods:
                        continue
                    if not name or "embedding" in name or "aqa" in name:
                        continue
                    out.append(ModelInfo(id=name, display_name=m.get("displayName") or name))
                if out:
                    return out
        except Exception as e:
            log.warning("gemini list_models failed: %s", e)
        return [ModelInfo(id=i, display_name=n) for i, n in GEMINI_DEFAULT_MODELS]

    def supports_no_think(self, model: str) -> bool:
        return False

    async def stream(
        self, *, model: str, messages: list[dict[str, Any]],
        no_think: bool, temperature: float | None,
    ) -> AsyncIterator[str]:
        # KILLSWITCH. This is the lane /chat and /chat/stream actually use,
        # so without it the main chat path kept spending while the switch
        # was on. At the provider layer, not a route dependency, because a
        # mission running in a background task is on no request path.
        from ..entitlements import assert_may_spend
        assert_may_spend()
        system, contents = _to_gemini_messages(messages)
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if temperature is not None:
            payload["generationConfig"] = {"temperature": float(temperature)}
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model}:streamGenerateContent?alt=sse&key={self._key}"
        )
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", url, json=payload,
                                headers={"content-type": "application/json"}) as r:
                if r.status_code != 200:
                    raw = await r.aread()
                    raise _http_provider_error("Gemini", r.status_code, raw)
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    body = line[len("data:"):].strip()
                    try:
                        ev = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    for cand in (ev.get("candidates") or []):
                        for part in ((cand.get("content") or {}).get("parts") or []):
                            t = part.get("text")
                            if t:
                                yield t


# ---- OpenRouter (primary language path) -------------------------------------
#
# OpenRouter is now the PRIMARY chat language path: all language flows through
# it, and the selector exposes its full live catalog as a searchable picker.
#
# RULE 7 — this client is INDEPENDENT of providers/dispatch (the sub-agent /
# generative OpenRouter lane). They do NOT import each other. This one reads its
# key from the chat key store / env OPENROUTER_API_KEY; dispatch reads its own
# dispatch_keys.json. Two clients, same upstream — by design.
#
# PRIVACY — every request carries provider.data_collection="deny" so OpenRouter
# only routes to upstreams that do NOT log/train on the prompt (zero-retention
# posture). The account-level training/logging toggles are set in the OpenRouter
# dashboard (documented for the user).

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "http://127.0.0.1:1420"
OPENROUTER_TITLE = "Infinity (Reactor Mk3)"
# Zero-retention data policy applied to EVERY OpenRouter call.
OPENROUTER_DATA_POLICY = {"data_collection": "deny"}

# CURATED chat shortlist — the dropdown shows THIS, not the raw ~339-model
# catalog. The raw catalog mixes in models that 4xx/5xx on use: `:free` tiers
# (404 "No endpoints matching your data policy" — the zero-retention policy
# excludes free models, which require training), models with no live endpoint
# (404 "No endpoints found"), rate-limited/niche providers (429/400/502), and
# audio/embedding models. Every id below was verified live to return 200 under
# OPENROUTER_DATA_POLICY (2026-06-26). list_models() intersects this with the
# live catalog so a slug that later disappears silently drops out. Search in the
# selector still works — it just searches this curated set.
CURATED_CHAT_MODELS: list[tuple[str, str]] = [
    ("anthropic/claude-opus-4.7",          "Claude Opus 4.7"),
    ("anthropic/claude-sonnet-4.5",        "Claude Sonnet 4.5"),
    ("anthropic/claude-haiku-4.5",         "Claude Haiku 4.5"),
    ("openai/gpt-5",                       "GPT-5"),
    ("openai/gpt-5-mini",                  "GPT-5 Mini"),
    ("openai/gpt-4o-mini",                 "GPT-4o-mini"),
    ("openai/gpt-4.1-nano",                "GPT-4.1 Nano"),
    ("openai/o3-mini",                     "o3-mini"),
    ("google/gemini-3.1-flash-lite",       "Gemini 3.1 Flash Lite"),
    ("google/gemini-2.5-flash",            "Gemini 2.5 Flash"),
    ("google/gemini-2.5-pro",              "Gemini 2.5 Pro"),
    ("deepseek/deepseek-r1",               "DeepSeek R1"),
    ("deepseek/deepseek-chat",             "DeepSeek V3"),
    ("qwen/qwen3-coder",                   "Qwen3 Coder"),
    ("moonshotai/kimi-k2-0905",            "Kimi K2"),
    ("meta-llama/llama-3.3-70b-instruct",  "Llama 3.3 70B"),
]


class OpenRouterChatProvider:
    name = "openrouter"

    # finish_reason of the LAST stream — main.py flags "length" on the done
    # line so silent truncation is visible on every lane (the durable guard).
    last_finish: str | None = None

    def __init__(self, key: str):
        self._key = key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "HTTP-Referer":  OPENROUTER_REFERER,
            "X-Title":       OPENROUTER_TITLE,
            "content-type":  "application/json",
        }

    @staticmethod
    async def validate(key: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(f"{OPENROUTER_BASE}/models",
                                headers={"Authorization": f"Bearer {key}"})
            return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[ModelInfo]:
        """The CURATED chat shortlist, intersected with the live catalog so a
        slug that disappears upstream silently drops out. NOT the raw ~339-model
        catalog: that mixes in models that 4xx/5xx on use (see CURATED_CHAT_MODELS).
        Order follows the curated list. If the catalog can't be fetched, return
        the curated list as-is so the selector still works."""
        live_ids: set[str] | None = None
        try:
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.get(f"{OPENROUTER_BASE}/models", headers=self._headers())
            if r.status_code == 200:
                live_ids = {m.get("id") for m in (r.json().get("data") or []) if m.get("id")}
        except Exception as e:
            log.warning("openrouter list_models catalog fetch failed: %s", e)
        out: list[ModelInfo] = []
        for mid, label in CURATED_CHAT_MODELS:
            if live_ids is None or mid in live_ids:
                out.append(ModelInfo(id=mid, display_name=label))
        return out

    def supports_no_think(self, model: str) -> bool:
        # OpenRouter models reason natively; the local /no_think prefill toggle
        # doesn't apply. The Fast/Thinking control is a no-op for these.
        return False

    def _cacheable_messages(self, model: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Anthropic models: split the system prompt at CACHE_BOUNDARY and mark
        the stable fat prefix with cache_control (persona+manifest+tools first,
        variables last) so consecutive turns reuse it. Other models just get
        the marker stripped."""
        if (not model.startswith("anthropic/")
                or not messages or messages[0].get("role") != "system"):
            return _strip_cache_boundary(messages)
        sys_text = str(messages[0].get("content") or "")
        if CACHE_BOUNDARY not in sys_text:
            return _strip_cache_boundary(messages)
        stable, _, variable = sys_text.partition(CACHE_BOUNDARY)
        content: list[dict[str, Any]] = [
            {"type": "text", "text": stable,
             "cache_control": {"type": "ephemeral"}},
        ]
        if variable.strip():
            content.append({"type": "text", "text": variable})
        return [{"role": "system", "content": content},
                *_strip_cache_boundary(messages[1:])]

    async def stream(
        self, *, model: str, messages: list[dict[str, Any]],
        no_think: bool, temperature: float | None,
    ) -> AsyncIterator[str]:
        # KILLSWITCH. This is the lane /chat and /chat/stream actually use,
        # so without it the main chat path kept spending while the switch
        # was on. At the provider layer, not a route dependency, because a
        # mission running in a background task is on no request path.
        from ..entitlements import assert_may_spend
        assert_may_spend()
        payload: dict[str, Any] = {
            "model":    model,
            "messages": self._cacheable_messages(model, messages),
            "stream":   True,
            "provider": OPENROUTER_DATA_POLICY,
            # Final SSE chunk carries exact token usage (incl. reasoning tokens)
            # so truncation is diagnosable from the log instead of invisible.
            "usage":    {"include": True},
        }
        if temperature is not None:
            payload["temperature"] = float(temperature)
        finish: str | None = None
        usage: dict[str, Any] | None = None
        reasoning_chars = 0
        content_chars = 0
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST", f"{OPENROUTER_BASE}/chat/completions",
                                json=payload, headers=self._headers()) as r:
                if r.status_code != 200:
                    raw = await r.aread()
                    raise _http_provider_error("OpenRouter", r.status_code, raw)
                async for line in r.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    body = line[len("data:"):].strip()
                    if body == "[DONE]":
                        break
                    try:
                        ev = json.loads(body)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("usage"):
                        usage = ev["usage"]
                    for ch in (ev.get("choices") or []):
                        if ch.get("finish_reason"):
                            finish = ch["finish_reason"]
                        d = ch.get("delta") or {}
                        if d.get("reasoning"):
                            reasoning_chars += len(d["reasoning"])
                        delta = d.get("content")
                        if delta:
                            content_chars += len(delta)
                            yield delta
        comp = (usage or {}).get("completion_tokens")
        rtok = ((usage or {}).get("completion_tokens_details") or {}).get("reasoning_tokens")
        cached = ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens")
        print(
            f"[CHAT-USAGE] model={model} finish={finish}"
            f" completion_tokens={comp} reasoning_tokens={rtok}"
            f" cached_prompt_tokens={cached}"
            f" reasoning_chars={reasoning_chars} content_chars={content_chars}",
            flush=True,
        )
        self.last_finish = finish
        if finish == "length":
            log.warning("openrouter chat stream TRUNCATED (finish=length) model=%s "
                        "usage=%s", model, usage)

    async def complete_text(
        self, *, model: str, messages: list[dict[str, Any]],
        temperature: float | None = 0.1, max_tokens: int = 512,
    ) -> str:
        """Non-streaming single-shot — used by the utility lane (relevance gate
        / auto-memory extraction). Carries the same zero-retention policy."""
        # KILLSWITCH. This is the lane /chat and /chat/stream actually use,
        # so without it the main chat path kept spending while the switch
        # was on. At the provider layer, not a route dependency, because a
        # mission running in a background task is on no request path.
        from ..entitlements import assert_may_spend
        assert_may_spend()
        payload: dict[str, Any] = {
            "model":      model,
            "messages":   messages,
            "max_tokens": max_tokens,
            "provider":   OPENROUTER_DATA_POLICY,
        }
        if temperature is not None:
            payload["temperature"] = float(temperature)
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{OPENROUTER_BASE}/chat/completions",
                             json=payload, headers=self._headers())
        if r.status_code != 200:
            raise _http_provider_error("OpenRouter", r.status_code, await r.aread())
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except Exception as e:
            raise ProviderError(f"OpenRouter unexpected response: {e}") from e


# ---- Registry ---------------------------------------------------------------

_PROVIDERS: dict[str, ChatProvider] = {}   # name -> live instance
_MODEL_CACHE: dict[str, list[ModelInfo]] = {}   # name -> last fetched list


PROVIDER_FACTORY = {
    "openrouter": OpenRouterChatProvider,
    "anthropic":  AnthropicProvider,
    "openai":     OpenAIProvider,
    "gemini":     GeminiProvider,
}


# Env-var fallbacks, checked at startup for providers with no stored key.
# Keys found here are used in-memory only — NEVER persisted to the DPAPI
# store (the .env file is their source of truth) and never echoed anywhere.
ENV_KEY_VARS: dict[str, tuple[str, ...]] = {
    "openrouter": ("OPENROUTER_API_KEY",),
    "anthropic":  ("ANTHROPIC_API_KEY",),
    "openai":     ("OPENAI_API_KEY",),
    "gemini":     ("GEMINI_API_KEY", "GOOGLE_AI_KEY"),
}


def init_from_disk() -> None:
    """Recreate provider instances from previously-stored keys at app
    startup. Failures are tolerated — a revoked key just stays disconnected
    until the user re-saves it. Providers with no stored key fall back to
    well-known env vars (repo-root .env is loaded by app.main)."""
    keys = _load_keys()
    for name, factory in PROVIDER_FACTORY.items():
        if name in keys and keys[name]:
            try:
                _PROVIDERS[name] = factory(keys[name])
            except Exception as e:
                log.warning("chat-provider %s init failed: %s", name, e)
    for name, factory in PROVIDER_FACTORY.items():
        if name in _PROVIDERS:
            continue
        for var in ENV_KEY_VARS.get(name, ()):
            key = (os.environ.get(var) or "").strip()
            if not key:
                continue
            try:
                _PROVIDERS[name] = factory(key)
                log.info("chat-provider %s connected from env var %s", name, var)
            except Exception as e:
                log.warning("chat-provider %s env init failed: %s", name, e)
            break


async def connect(name: str, key: str) -> tuple[bool, str | None, list[ModelInfo]]:
    """Validate + persist a key. Returns (ok, error_msg, models_on_success)."""
    factory = PROVIDER_FACTORY.get(name)
    if not factory:
        return (False, f"unknown provider '{name}'", [])
    if not key or len(key) < 8:
        return (False, "key looks empty / too short", [])
    ok = await factory.validate(key)
    if not ok:
        return (False, "key validation failed (server returned non-200)", [])

    # Persist.
    keys = _load_keys()
    keys[name] = key
    _save_keys(keys)

    # Replace any prior instance + refresh model list.
    inst = factory(key)
    _PROVIDERS[name] = inst
    try:
        models = await inst.list_models()
    except Exception as e:
        log.warning("post-connect list_models for %s failed: %s", name, e)
        models = []
    _MODEL_CACHE[name] = models
    return (True, None, models)


def disconnect(name: str) -> bool:
    keys = _load_keys()
    if name in keys:
        keys.pop(name)
        _save_keys(keys)
    _PROVIDERS.pop(name, None)
    _MODEL_CACHE.pop(name, None)
    return True


def status() -> dict[str, dict[str, Any]]:
    """{name: {connected: bool, needs_reconnect?: true}} for OpenRouter + the
    three BYOK premium providers. `needs_reconnect` appears when the stored
    ciphertext for that provider failed DPAPI decryption (file moved
    between users/machines, or the user profile rotated). UI shows a
    distinct 'Reconnect' affordance in that case."""
    nr = set(needs_reconnect())
    out: dict[str, dict[str, Any]] = {}
    for n in PROVIDER_FACTORY:
        entry: dict[str, Any] = {"connected": n in _PROVIDERS}
        if n in nr:
            entry["needs_reconnect"] = True
        out[n] = entry
    return out


def store_info() -> dict[str, Any]:
    """Metadata about the on-disk key store. Exposed at GET
    /chat-providers/status so the UI can show a one-line provenance hint
    ('Stored encrypted (Windows DPAPI)') and refuse to live-use BYOK
    keys if we ever fell back to the identity store."""
    be = keystore_backend()
    return {
        "backend":   be,
        "encrypted": be == "dpapi",
    }


async def list_all_models() -> list[dict[str, Any]]:
    """Flat list of {provider, model_id, display_name}. The chat selector
    consumes this directly."""
    out: list[dict[str, Any]] = []

    # Auto router — listed only when OpenRouter is connected (fail-closed: the
    # sentinel resolves to an OpenRouter slug, so without that key it would be
    # a dead picker entry).
    if "openrouter" in _PROVIDERS:
        out.append({
            "provider":     "auto",
            "model_id":     AUTO_MODEL_ID,
            "display_name": AUTO_MODEL_LABEL,
        })

    # Connected providers (OpenRouter primary + any BYOK) — refresh if missing
    # (one-shot per call;
    # caller can re-hit /chat-providers/models to force a refresh after
    # the user expects the catalog to have changed).
    for name, inst in _PROVIDERS.items():
        cached = _MODEL_CACHE.get(name)
        if cached is None:
            try:
                cached = await inst.list_models()
                _MODEL_CACHE[name] = cached
            except Exception as e:
                log.warning("chat-provider %s list_models failed: %s — it "
                            "contributes NO models to this selector list", name, e)
                cached = []
        for m in cached:
            out.append({
                "provider":     name,
                "model_id":     m.id,
                "display_name": m.display_name,
            })
    if not out:
        # The local lane used to guarantee at least one entry here. It is gone,
        # so an empty list is now possible — say why instead of letting the
        # selector render a silent blank.
        log.warning("chat model list is EMPTY — no chat provider is connected. "
                    "Set OPENROUTER_API_KEY (or connect a BYOK key); until then "
                    "every chat turn fails closed.")
    return out


def _no_chat_provider(model_id: str | None) -> tuple[str, None]:
    """Nothing connected can serve this id. Loud by design: the local lane that
    used to absorb this case is deleted (AGENTS.md — OpenRouter only), so a
    quiet ('local', None) here would strand the turn with no explanation.
    Callers already branch on `instance is None` and degrade explicitly."""
    log.warning("no chat provider can serve model_id=%r — connect OpenRouter "
                "(OPENROUTER_API_KEY) or a BYOK key", model_id)
    return ("none", None)


def provider_for_model(model_id: str | None) -> tuple[str, ChatProvider | None]:
    """(provider_name, instance). Returns ('none', None) when no connected
    provider can serve the id — never a silent stand-in. OpenRouter is the
    primary language path and the catch-all: any namespaced ('vendor/model')
    id, and any unrecognized id, routes there.

    AUTO_MODEL_ID must never reach here — callers resolve it first. That was a
    comment and nothing more, so /mission/plan violated it and the sentinel went
    to OpenRouter as though it were a model name. OpenRouter rejected the id and
    the user was told "planner failed", which points at the planner rather than
    at the caller that forgot to resolve. It is checked now."""
    if model_id == AUTO_MODEL_ID:
        raise ValueError(
            f"{AUTO_MODEL_ID!r} reached provider_for_model. It is a sentinel, "
            f"not a model: the calling route must resolve it to a concrete id "
            f"first. Passing it upstream produces an unknown-model error that "
            f"blames the provider."
        )
    openrouter = _PROVIDERS.get("openrouter")

    # No selection → default to OpenRouter (the primary path) when available.
    if not model_id:
        return ("openrouter", openrouter) if openrouter else _no_chat_provider(model_id)

    # Namespaced ids ('anthropic/…', 'openai/…', 'meta-llama/…') are OpenRouter.
    if "/" in model_id and openrouter:
        return ("openrouter", openrouter)

    # Exact match against any connected provider's cached catalog.
    for name, models in _MODEL_CACHE.items():
        for m in models:
            if m.id == model_id:
                return (name, _PROVIDERS.get(name))

    # Heuristic for direct BYOK ids the cache hasn't seen yet.
    if model_id.startswith("claude") and "anthropic" in _PROVIDERS:
        return ("anthropic", _PROVIDERS["anthropic"])
    if (model_id.startswith(("gpt", "o1", "o3", "o4"))) and "openai" in _PROVIDERS:
        return ("openai", _PROVIDERS["openai"])
    if model_id.startswith("gemini") and "gemini" in _PROVIDERS:
        return ("gemini", _PROVIDERS["gemini"])

    # Unknown id → OpenRouter (the catch-all language path) if connected.
    if openrouter:
        return ("openrouter", openrouter)
    return _no_chat_provider(model_id)
