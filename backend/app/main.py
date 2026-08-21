"""Mark 4 — M0 skeleton.

Deliberately almost empty. M0's job is to prove the PIPE before anything rides
on it: container boots, HTTPS terminates, CORS is closed, and NDJSON streaming
survives the proxy. Every later milestone then debugs ONE unfamiliar system at a
time instead of tenancy-plus-hosting simultaneously.

Do not add features here. Endpoints live in `routers/` and every turn goes
through `orchestrator.run_turn(account, ...)` from M2 onward.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .accounts import NotAuthenticated

app = FastAPI(title="Mark 4", version="0.0.1")

# CORS: an explicit allowlist from the environment. NEVER "*" — that was safe on
# loopback in Mark 3 and is fatal hosted (PUNCHLIST B7). An unset origin list
# means "same-origin only", which fails closed rather than open.
_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

_BOOTED_AT = time.time()


@app.exception_handler(NotAuthenticated)
async def _not_authenticated(request: Request, exc: NotAuthenticated) -> JSONResponse:
    """No session resolves to 401 — never to a default account."""
    return JSONResponse(status_code=401, content={"detail": "Not authenticated."})


@app.get("/health")
async def health() -> dict[str, object]:
    """M0 definition of done, part 1: this returns 200 on the public URL."""
    return {
        "ok": True,
        "service": "mark4",
        "version": app.version,
        "uptime_s": round(time.time() - _BOOTED_AT, 1),
        "cors_origins": len(_origins),
    }


class EchoIn(BaseModel):
    text: str = "the quick brown fox jumps over the lazy dog"
    delay_ms: int = 120


@app.post("/echo/stream")
async def echo_stream(body: EchoIn) -> StreamingResponse:
    """M0 definition of done, part 2: NDJSON that arrives INCREMENTALLY.

    The per-token delay is the whole point. A buffering proxy or a serverless
    adapter will hold the response and deliver it as one lump at the end — the
    app still "works" and feels dead, and it is miserable to diagnose later when
    a real model is also in the path. Find out here, with nothing at stake.

    Verify from a terminal, NOT a browser fetch that awaits the whole body:
        curl -N -X POST https://<host>/echo/stream \\
             -H 'content-type: application/json' -d '{}'
    Words must appear one at a time. If they all land together, the chat path
    will never stream — fix the transport before building on it.
    """
    if body.delay_ms < 0 or body.delay_ms > 2000:
        raise HTTPException(status_code=400, detail="delay_ms out of range")

    async def gen():
        t0 = time.monotonic()
        for i, word in enumerate(body.text.split()):
            await asyncio.sleep(body.delay_ms / 1000)
            yield json.dumps({"i": i, "delta": word + " ",
                              "t_ms": round((time.monotonic() - t0) * 1000)}) + "\n"
        yield json.dumps({"done": True,
                          "total_ms": round((time.monotonic() - t0) * 1000)}) + "\n"

    return StreamingResponse(
        gen(),
        media_type="application/x-ndjson",
        # Disables proxy buffering on nginx-family front ends. Harmless
        # elsewhere; the difference between a live stream and a dead one.
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
