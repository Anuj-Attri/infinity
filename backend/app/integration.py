"""Backend feature wiring — one place, one call.

Mounts the Vault and OCR routers plus the mission/skills endpoints, and
mission-runtime + skills endpoints, then initializes them. `main.py` calls
`wire(app, ...)` once.

WHY THIS EXISTS: the sandbox mount's sync layer repeatedly committed stale
copies of the very large `main.py`, silently dropping inline wiring edits.
Consolidating the wiring into this small module (which the sync layer handles
reliably) plus a single call from `main.py` makes the integration robust —
no more features that exist as files but are never mounted.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import vault as vault_mod
from . import skills_engine
from .agent import runtime as mission_runtime
from .agent import store as mission_store
from .agent.schemas import MissionSpec, NodeSpec

_extra = APIRouter()
_dispatch_registry: Any = None   # set in wire(); used by the run endpoint's gate


# ── Mission Control: server-side DAG runtime (MC-3) ──────────────────────────

class _MissionSubtaskIn(BaseModel):
    id: str | None = None
    role: str = "agent"
    task: str
    tier: str = "fast"
    est_tokens: int = 1500
    depends_on: list[str] = []


class _MissionRunIn(BaseModel):
    goal: str
    title: str = "Mission"
    model: str | None = None
    subtasks: list[_MissionSubtaskIn]
    max_concurrency: int = 3


@_extra.post("/mission/run")
async def mission_run(body: _MissionRunIn) -> dict:
    if not mission_runtime.ready():
        raise HTTPException(status_code=503, detail="mission runtime not ready")
    if _dispatch_registry is None or _dispatch_registry.get_provider() is None:
        raise HTTPException(status_code=503,
                            detail="OpenRouter not connected — add a key in Settings → Connectors.")
    try:
        nodes = [
            NodeSpec(id=st.id or f"st{i+1}", role=st.role, task=st.task,
                     tier=st.tier if st.tier in ("fast", "smart") else "fast",
                     est_tokens=st.est_tokens, depends_on=st.depends_on)
            for i, st in enumerate(body.subtasks)
        ]
        spec = MissionSpec(title=(body.title or "Mission").strip()[:60], goal=body.goal,
                           model=body.model, nodes=nodes,
                           max_concurrency=max(1, min(5, body.max_concurrency)))
        return mission_runtime.start(spec)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)[:200])


@_extra.get("/mission/recent")
async def mission_recent(limit: int = Query(20, ge=1, le=100)) -> dict:
    return {"items": mission_store.recent(limit)}


@_extra.get("/mission/{mission_id}")
async def mission_get(mission_id: str) -> dict:
    snap = mission_runtime.get(mission_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="unknown mission")
    return snap


@_extra.get("/mission/{mission_id}/events")
async def mission_events(mission_id: str) -> StreamingResponse:
    if mission_runtime.get(mission_id) is None:
        raise HTTPException(status_code=404, detail="unknown mission")

    async def gen():
        async for snap in mission_runtime.events(mission_id):
            yield json.dumps({"state": snap}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@_extra.post("/mission/{mission_id}/stop")
async def mission_stop(mission_id: str) -> dict:
    return {"stopped": mission_runtime.stop(mission_id)}


# ── Skills ───────────────────────────────────────────────────────────────────

class _SkillToggleIn(BaseModel):
    name: str
    enabled: bool


@_extra.get("/skills")
async def skills_list() -> dict:
    return {"items": skills_engine.list_all()}


@_extra.post("/skills/toggle")
async def skills_toggle(body: _SkillToggleIn) -> dict:
    if not skills_engine.set_enabled(body.name, body.enabled):
        raise HTTPException(status_code=404, detail="unknown skill")
    return {"items": skills_engine.list_all()}


@_extra.post("/skills/reindex")
async def skills_reindex() -> dict:
    return {"count": skills_engine.scan(force=True)}


# ── the single wiring entrypoint ─────────────────────────────────────────────

def wire(app, *, utility_llm, chat_registry, dispatch_registry, spend) -> None:
    """Mount routers + register endpoints + initialize. Idempotent enough for a
    single module-load call from main.py. Never raises past a warning — a
    failed sub-init must not take down the whole backend."""
    global _dispatch_registry
    _dispatch_registry = dispatch_registry

    # Mounting is NOT guarded. An app that boots without its knowledge base is
    # not this product, and "loud in the logs" is not loud enough for that — the
    # service comes up, answers, and quietly has nothing to answer from. The
    # init steps below stay guarded: degraded is survivable, absent is not.
    from .vault import router as vault_router
    from .ocr import router as ocr_router
    for r in (vault_router, ocr_router, _extra):
        app.include_router(r)

    try:
        vault_mod.init(utility_llm)
    except Exception as e:
        print(f"[wire] vault init failed: {e}", flush=True)

    async def _on_done(st) -> None:
        if st.synthesis and len(st.synthesis) > 200:
            await vault_mod.file_report(
                f"Mission — {st.title}", st.synthesis + f"\n\n> goal: {st.goal}",
                source_kind="report", source_ref=st.id,
            )

    try:
        mission_runtime.init(utility_llm=utility_llm, chat_registry=chat_registry,
                             dispatch_registry=dispatch_registry,
                             spend_record=spend.record, on_done=_on_done)
    except Exception as e:
        print(f"[wire] mission runtime init failed: {e}", flush=True)

    try:
        n = skills_engine.scan(force=True)
        print(f"[wire] skills indexed: {n}", flush=True)
    except Exception as e:
        print(f"[wire] skills scan failed: {e}", flush=True)

    print("[wire] backend features mounted: vault, ocr, missions, skills", flush=True)
    # NOTE: serving the built frontend (dist/) is done by mount_frontend()
    # below, called at the END of main.py — a catch-all mount MUST be the last
    # route or it shadows /health and every API endpoint.


def mount_frontend(app) -> None:
    """Serve the built UI (dist/) for thin clients — cyberdeck kiosk, phone
    browser. MUST be called AFTER all API routes are registered (a mount at "/"
    is a catch-all; earlier-registered routes win, this catches the rest).
    No-op when there's no production build, so dev (vite) is untouched."""
    try:
        from pathlib import Path as _P
        from fastapi.staticfiles import StaticFiles
        _dist = _P(__file__).resolve().parents[2] / "dist"
        if _dist.is_dir():
            app.mount("/", StaticFiles(directory=str(_dist), html=True), name="app")
            print(f"[wire] serving built frontend from {_dist}", flush=True)
    except Exception as e:
        print(f"[wire] static frontend mount skipped: {e}", flush=True)
