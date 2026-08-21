"""Dispatch (sub-agent) provider registry.

STRICT SEPARATION:
  This module handles sub-agent infra costs via OpenRouter ONLY.
  It MUST NEVER import from providers.chat, and providers.chat
  MUST NEVER import from here. The registries are entirely independent:
  different key file, different endpoints, different storage class.

Endpoints (mounted by app/dispatch_providers.py):
  GET    /dispatch-providers/status
  GET    /dispatch-providers/models
  POST   /dispatch-providers/openrouter/connect  {key}
  DELETE /dispatch-providers/openrouter

Dispatch streaming (mounted in main.py):
  POST   /dispatch/subagent   {role, task, model_tier?}  -> NDJSON stream
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

from . import keystore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- tiers ---

# Curated tier -> OpenRouter model mapping.  Nova picks by capability need;
# the user never sees the raw model names here unless they look in Settings.
TIER_MODELS: dict[str, str] = {
    "fast":  "openai/gpt-4o-mini",
    "smart": "anthropic/claude-sonnet-4.5",
}

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OPENROUTER_REFERER = "http://127.0.0.1:1420"
OPENROUTER_TITLE = "Infinity (Reactor Mk3)"
# Zero-retention data policy applied to EVERY dispatch OpenRouter call — routes
# only to upstreams that do NOT log/train on the payload. Mirrors the chat lane.
OPENROUTER_DATA_POLICY = {"data_collection": "deny"}

# ---------------------------------------------------------------- types ---

@dataclass
class DispatchModelInfo:
    id: str
    display_name: str
    tier: str          # "fast" | "smart" | "other"

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "display_name": self.display_name, "tier": self.tier}


class DispatchProviderError(RuntimeError):
    """UI-safe error from a dispatch provider call."""


def _sniff_audio_format(b64: str) -> str:
    """Detect the container of base64 audio from its magic bytes (the audio
    delta carries no format hint). Falls back to mp3 (broadly playable)."""
    head = b64[:16]
    if head.startswith("UklGR"):   # "RIFF" → WAV
        return "wav"
    if head.startswith("T2dn"):    # "OggS" → Ogg
        return "ogg"
    if head.startswith("ZkxhQw"):  # "fLaC" → FLAC
        return "flac"
    if head.startswith("SUQz") or head.startswith("//"):  # "ID3" / MPEG frame → MP3
        return "mp3"
    return "mp3"


# ----------------------------------------------------------- OpenRouter ---

class OpenRouterProvider:
    """OpenRouter sub-agent provider.  Implements the dispatch interface:
    validate / list_models / complete / stream.

    NEVER use this for user-facing chat — it belongs in the dispatch
    registry only.  The chat registry lives in providers.chat.
    """
    name = "openrouter"

    def __init__(self, key: str) -> None:
        self._key = key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization":  f"Bearer {self._key}",
            "HTTP-Referer":   OPENROUTER_REFERER,
            "X-Title":        OPENROUTER_TITLE,
            "content-type":   "application/json",
        }

    @staticmethod
    async def validate(key: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(
                    f"{OPENROUTER_BASE}/models",
                    headers={"Authorization": f"Bearer {key}"},
                )
            return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[DispatchModelInfo]:
        """Return models filtered to text-generation capable ones, tagged by tier."""
        try:
            async with httpx.AsyncClient(timeout=15.0) as c:
                r = await c.get(
                    f"{OPENROUTER_BASE}/models",
                    headers={"Authorization": f"Bearer {self._key}"},
                )
            if r.status_code != 200:
                raise DispatchProviderError(f"OpenRouter models HTTP {r.status_code}")
            data = r.json().get("data") or []
        except DispatchProviderError:
            raise
        except Exception as e:
            log.warning("openrouter list_models failed: %s", e)
            data = []

        skip_kinds = {"image", "audio", "embedding", "moderation"}
        out: list[DispatchModelInfo] = []
        fast_ids = set(TIER_MODELS.values())

        for m in data:
            mid = m.get("id") or ""
            if not mid:
                continue
            # Filter out non-text modalities.
            arch = m.get("architecture") or {}
            modality = (arch.get("input_modalities") or [])
            if any(k in modality for k in skip_kinds):
                continue
            # Tag tier.
            if mid in fast_ids:
                tier = next(k for k, v in TIER_MODELS.items() if v == mid)
            else:
                tier = "other"
            display = m.get("name") or mid
            out.append(DispatchModelInfo(id=mid, display_name=display, tier=tier))

        # Ensure tier models are present even if the API didn't return them.
        existing = {m.id for m in out}
        for tier_name, model_id in TIER_MODELS.items():
            if model_id not in existing:
                out.insert(0, DispatchModelInfo(id=model_id,
                                                display_name=model_id,
                                                tier=tier_name))
        return out

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.5,
        max_tokens: int = 2048,
    ) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "provider": OPENROUTER_DATA_POLICY,
        }
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(
                f"{OPENROUTER_BASE}/chat/completions",
                json=payload,
                headers=self._headers(),
            )
        if r.status_code != 200:
            raise DispatchProviderError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["choices"][0]["message"]["content"] or ""
        except Exception as e:
            raise DispatchProviderError(f"OpenRouter unexpected response: {e}") from e

    # ── Per-modality generative dispatch ─────────────────────────────────────
    # Each output modality has a DIFFERENT OpenRouter contract:
    #   • image → SYNC POST; image on message.images[].image_url.url
    #   • audio → STREAM (audio output is rejected non-streamed: HTTP 400
    #     "Audio output requires stream: true"); bytes arrive in delta.audio.data
    #   • video → ASYNC (submit → poll); NO OpenRouter model hosts it today
    # generate_media() picks the right handler from `modalities` so callers stay
    # contract-agnostic and future models slot into the matching handler.
    # All return {images:[data-urls], audio:<data-url>|None, text, usage}.
    # Real errors are LOGGED server-side (status + body) for diagnosis; callers
    # surface only the opaque "<Kind> unavailable." to the user.

    async def generate_media(
        self,
        messages: list[dict[str, Any]],
        model: str,
        modalities: list[str],
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        if "audio" in modalities:
            return await self._generate_audio_stream(messages, model, modalities, max_tokens)
        return await self._generate_sync(messages, model, modalities, max_tokens)

    async def _generate_sync(
        self, messages: list[dict[str, Any]], model: str,
        modalities: list[str], max_tokens: int,
    ) -> dict[str, Any]:
        """SYNC handler (image). Image on message.images[].image_url.url."""
        payload = {"model": model, "messages": messages, "modalities": modalities,
                   "max_tokens": max_tokens, "provider": OPENROUTER_DATA_POLICY}
        async with httpx.AsyncClient(timeout=300.0) as c:
            r = await c.post(f"{OPENROUTER_BASE}/chat/completions", json=payload, headers=self._headers())
        if r.status_code != 200:
            log.warning("dispatch sync %s HTTP %s: %s", model, r.status_code, r.text[:500])
            raise DispatchProviderError(f"OpenRouter HTTP {r.status_code}")
        try:
            data = r.json()
            msg = data["choices"][0]["message"]
        except Exception as e:
            log.warning("dispatch sync %s bad response: %s | %s", model, e, r.text[:500])
            raise DispatchProviderError(f"OpenRouter unexpected response: {e}") from e

        images: list[str] = []
        for im in (msg.get("images") or []):
            url = ((im or {}).get("image_url") or {}).get("url")
            if url:
                images.append(url)
        text = ""
        content = msg.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    text += part.get("text") or ""
                elif part.get("type") == "image_url":
                    url = (part.get("image_url") or {}).get("url")
                    if url:
                        images.append(url)
        return {"images": images, "audio": None, "text": text, "usage": data.get("usage") or {}}

    async def _generate_audio_stream(
        self, messages: list[dict[str, Any]], model: str,
        modalities: list[str], max_tokens: int,
    ) -> dict[str, Any]:
        """STREAM handler (audio). OpenRouter rejects non-streamed audio output;
        bytes arrive as base64 in delta.audio.data — accumulated, sniffed for
        container format, and assembled into a data URL the tile can play.
        NOTE: max_tokens is intentionally omitted — capping it truncates the
        audio clip (the verified-working request sends no cap)."""
        payload = {"model": model, "messages": messages, "modalities": modalities,
                   "stream": True, "provider": OPENROUTER_DATA_POLICY}
        audio_b64 = ""
        text = ""
        async with httpx.AsyncClient(timeout=300.0) as c:
            async with c.stream("POST", f"{OPENROUTER_BASE}/chat/completions",
                                json=payload, headers=self._headers()) as r:
                if r.status_code != 200:
                    raw = (await r.aread())[:500].decode("utf-8", "replace")
                    log.warning("dispatch audio %s HTTP %s: %s", model, r.status_code, raw)
                    raise DispatchProviderError(f"OpenRouter HTTP {r.status_code}")
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
                        d = ch.get("delta") or {}
                        a = d.get("audio")
                        if isinstance(a, dict) and a.get("data"):
                            audio_b64 += a["data"]
                        if d.get("content"):
                            text += d["content"]
        audio = f"data:audio/{_sniff_audio_format(audio_b64)};base64,{audio_b64}" if audio_b64 else None
        return {"images": [], "audio": audio, "text": text, "usage": {}}

    async def generate_image(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int = 4096,
    ) -> tuple[list[str], str, dict[str, Any]]:
        """Back-compat thin wrapper over generate_media (image modality)."""
        res = await self.generate_media(messages, model, ["image", "text"], max_tokens)
        return res["images"], res["text"], res["usage"]

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.5,
        max_tokens: int = 2048,
    ) -> AsyncIterator[str]:
        payload = {
            "model":       model,
            "messages":    messages,
            "stream":      True,
            "temperature": temperature,
            "max_tokens":  max_tokens,
            "provider":    OPENROUTER_DATA_POLICY,
        }
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream(
                "POST",
                f"{OPENROUTER_BASE}/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as r:
                if r.status_code != 200:
                    raw = await r.aread()
                    raise DispatchProviderError(
                        f"OpenRouter HTTP {r.status_code}: {raw[:300].decode('utf-8','replace')}"
                    )
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


# ---------------------------------------------------------------- registry ---

_PROVIDER: OpenRouterProvider | None = None
_LAST_LOAD_REPORT: keystore.KeyStoreReport = keystore.KeyStoreReport(needs_reconnect=[])
_MODEL_CACHE: list[DispatchModelInfo] = []


def _load_keys() -> dict[str, str]:
    global _LAST_LOAD_REPORT
    keys, report = keystore.load_dispatch_keys()
    _LAST_LOAD_REPORT = report
    if report.needs_reconnect:
        log.warning("dispatch keystore: %d entr%s failed to decrypt: %s",
                    len(report.needs_reconnect),
                    "y" if len(report.needs_reconnect) == 1 else "ies",
                    ",".join(report.needs_reconnect))
    return keys


def _save_keys(d: dict[str, str]) -> None:
    keystore.save_dispatch_keys(d)


def init_from_disk() -> None:
    """Called once at startup: load the OpenRouter key and register the provider.
    Source order: the DPAPI keystore (Windows venv runs) → env OPENROUTER_API_KEY.
    The env fallback is what makes the dockerized backend self-sufficient: a Linux
    container has no Windows DPAPI store, so dispatch reads its key from env_file
    (the chat lane reads the same env var independently — Rule 7 holds)."""
    global _PROVIDER, _MODEL_CACHE
    keys = _load_keys()
    key = keys.get("openrouter", "") or (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if key:
        _PROVIDER = OpenRouterProvider(key)
        log.info("dispatch: OpenRouter loaded")
        # Model cache refresh is deferred to the FastAPI startup event so it
        # runs inside the running event loop, not at import time.
    else:
        log.info("dispatch: OpenRouter not configured")


async def _refresh_model_cache() -> None:
    global _MODEL_CACHE
    if _PROVIDER is None:
        return
    try:
        _MODEL_CACHE = await _PROVIDER.list_models()
    except Exception as e:
        log.warning("dispatch model cache refresh failed: %s", e)


async def connect(key: str) -> tuple[bool, str | None, list[DispatchModelInfo]]:
    """Validate + store + register. Returns (ok, err, models)."""
    global _PROVIDER, _MODEL_CACHE
    ok = await OpenRouterProvider.validate(key)
    if not ok:
        return (False, "Key validation failed — check your OpenRouter key and try again.", [])
    # Write before instantiating so restart also picks it up.
    _save_keys({"openrouter": key})
    _PROVIDER = OpenRouterProvider(key)
    try:
        models = await _PROVIDER.list_models()
        _MODEL_CACHE = models
    except Exception as e:
        log.warning("dispatch: post-connect model list failed: %s", e)
        models = []
    return (True, None, models)


def disconnect() -> bool:
    global _PROVIDER, _MODEL_CACHE
    _PROVIDER = None
    _MODEL_CACHE = []
    _save_keys({})
    return True


def status() -> dict[str, Any]:
    connected = _PROVIDER is not None
    needs_rc = list(_LAST_LOAD_REPORT.needs_reconnect)
    info: dict[str, Any] = {"connected": connected}
    if "openrouter" in needs_rc:
        info["needs_reconnect"] = True
    return {"openrouter": info}


def store_info() -> dict[str, Any]:
    return {
        "backend":   keystore.backend_name(),
        "encrypted": keystore.backend_name() == "dpapi",
    }


async def list_all_models() -> list[dict[str, str]]:
    if not _PROVIDER:
        return []
    if not _MODEL_CACHE:
        await _refresh_model_cache()
    return [m.to_dict() for m in _MODEL_CACHE]


def get_provider() -> OpenRouterProvider | None:
    return _PROVIDER


def resolve_model(tier: str) -> str:
    """Map a tier name ('fast' | 'smart') to an actual OpenRouter model id."""
    return TIER_MODELS.get(tier, TIER_MODELS["fast"])
