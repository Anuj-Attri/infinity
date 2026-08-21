"""HTTP surface for the dispatch (sub-agent) provider registry.

Endpoints
---------
GET    /dispatch-providers/status
GET    /dispatch-providers/models
POST   /dispatch-providers/openrouter/connect  {key}
DELETE /dispatch-providers/openrouter

The user-facing chat registry (Anthropic / OpenAI / Gemini) lives in
app/chat_providers.py and is NEVER referenced here. These are two
completely separate registries with separate storage and endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .providers import dispatch as dispatch_registry

router = APIRouter(prefix="/dispatch-providers", tags=["dispatch-providers"])


@router.get("/status")
async def status() -> dict[str, Any]:
    return {
        "providers": dispatch_registry.status(),
        "store":     dispatch_registry.store_info(),
    }


@router.get("/models")
async def models() -> dict[str, Any]:
    return {"items": await dispatch_registry.list_all_models()}


class ConnectIn(BaseModel):
    key: str


@router.post("/{provider}/connect")
async def connect(provider: str, body: ConnectIn) -> dict[str, Any]:
    if provider != "openrouter":
        raise HTTPException(status_code=404, detail=f"unknown dispatch provider: {provider}")
    ok, err, models = await dispatch_registry.connect(body.key.strip())
    if not ok:
        raise HTTPException(status_code=422, detail=err or "connect failed")
    return {"ok": True, "models": [m.to_dict() for m in models]}


@router.delete("/{provider}")
async def disconnect(provider: str) -> dict[str, Any]:
    if provider != "openrouter":
        raise HTTPException(status_code=404, detail=f"unknown dispatch provider: {provider}")
    return {"ok": dispatch_registry.disconnect()}
