"""HTTP surface for the user-facing chat-provider registry.

Endpoints
---------
GET    /chat-providers/status                     — {provider: {connected: bool}}
GET    /chat-providers/models                     — flat list for the chat selector
POST   /chat-providers/{provider}/connect {key}   — validate + store + register
DELETE /chat-providers/{provider}                 — disconnect / forget key

The dispatch (sub-agent) registry is a separate module (providers/dispatch.py)
and has its OWN HTTP surface to be added in the sub-agent pass. The two
never share storage, keys, models, or endpoints — by design.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .providers import chat as chat_providers

log = logging.getLogger(__name__)

router = APIRouter(prefix="/chat-providers", tags=["chat-providers"])


@router.get("/status")
async def status() -> dict[str, Any]:
    # No "local" entry. The desktop version injected one here AFTER chat.status() returned,
    # reporting connected: true without probing anything — so removing the local
    # lane from the provider registry would not have removed it from this API
    # response, and the picker would still offer a model that cannot exist.
    providers = chat_providers.status()
    return {
        "providers": providers,
        "store":     chat_providers.store_info(),
    }


@router.get("/models")
async def models() -> dict[str, Any]:
    return {"items": await chat_providers.list_all_models()}


class ConnectIn(BaseModel):
    key: str


@router.post("/{provider}/connect")
async def connect(provider: str, body: ConnectIn) -> dict[str, Any]:
    ok, err, models = await chat_providers.connect(provider, body.key.strip())
    if not ok:
        # 422 — auth/format failure (vs 500 backend bug). Caller renders inline.
        raise HTTPException(status_code=422, detail=err or "connect failed")
    return {
        "ok":      True,
        "models":  [m.to_dict() for m in models],
    }


@router.delete("/{provider}")
async def disconnect(provider: str) -> dict[str, Any]:
    return {"ok": chat_providers.disconnect(provider)}
