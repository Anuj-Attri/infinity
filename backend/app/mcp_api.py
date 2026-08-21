"""HTTP surface for the MCP connector marketplace.

  GET    /mcp/servers           — connected servers (no secrets)
  GET    /mcp/tools             — flattened tools across all connected servers
  POST   /mcp/connect {name,url,auth?}  — validate + persist a server
  POST   /mcp/refresh/{id}      — re-list a server's tools
  DELETE /mcp/servers/{id}      — disconnect + forget
  POST   /mcp/call {server_id,name,args}  — invoke one tool (gated client-side)
  GET    /mcp/marketplace       — curated suggestions to browse

Rides the app-level auth gate. Fail-closed: MCP errors → 502 with a short,
safe message (never a stack/secret).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import mcp_client

router = APIRouter(prefix="/mcp", tags=["mcp"])


# A small curated marketplace — browse + connect (or add a custom URL). These are
# suggestions only; the mechanism connects ANY Streamable-HTTP MCP server.
MARKETPLACE: list[dict[str, str]] = [
    {"name": "Web Search / Research", "category": "research",
     "desc": "Live web search + page fetch for /research-style grounding.",
     "url_hint": "https://<your-search-mcp>/mcp", "needs_auth": "true"},
    {"name": "GitHub (SWE)", "category": "swe",
     "desc": "Repos, issues, PRs, code search — software-engineering tools.",
     "url_hint": "https://api.githubcopilot.com/mcp/", "needs_auth": "true"},
    {"name": "Code Review (CodeRabbit-style)", "category": "review",
     "desc": "Diff review + inline suggestions over a connected repo.",
     "url_hint": "https://<your-review-mcp>/mcp", "needs_auth": "true"},
    {"name": "Filesystem / Docs", "category": "swe",
     "desc": "Read/search a scoped docs or filesystem tree.",
     "url_hint": "https://<your-fs-mcp>/mcp", "needs_auth": "false"},
    {"name": "Custom server", "category": "custom",
     "desc": "Any Streamable-HTTP MCP endpoint — paste its URL.",
     "url_hint": "https://host/mcp", "needs_auth": "false"},
]


class ConnectIn(BaseModel):
    name: str = ""
    url: str
    auth: str | None = None


class CallIn(BaseModel):
    server_id: str
    name: str
    args: dict[str, Any] = {}
    # Consent gate: gated tools refuse to run unless the frontend re-sends the
    # call with approved=true AFTER the user clicked Allow in the popup.
    approved: bool = False


class TrustIn(BaseModel):
    trusted: bool = True


class EnabledIn(BaseModel):
    enabled: bool


class AuditDenyIn(BaseModel):
    server_id: str = ""
    server: str = ""
    name: str
    args: dict[str, Any] = {}


@router.get("/marketplace")
async def marketplace() -> dict[str, Any]:
    return {"items": MARKETPLACE}


@router.get("/servers")
async def servers() -> dict[str, Any]:
    return {"servers": mcp_client.list_servers(), "enabled": mcp_client.is_enabled()}


@router.post("/trust/{sid}")
async def trust(sid: str, body: TrustIn) -> dict[str, Any]:
    try:
        return {"ok": True, "trusted": mcp_client.trust(sid, body.trusted)}
    except mcp_client.MCPError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/enabled")
async def enabled() -> dict[str, Any]:
    return {"enabled": mcp_client.is_enabled()}


@router.post("/enabled")
async def set_enabled(body: EnabledIn) -> dict[str, Any]:
    mcp_client.set_enabled(body.enabled)
    return {"ok": True, "enabled": mcp_client.is_enabled()}


@router.post("/disconnect-all")
async def disconnect_all() -> dict[str, Any]:
    return {"ok": True, "removed": mcp_client.disconnect_all()}


@router.get("/audit")
async def audit() -> dict[str, Any]:
    return {"events": mcp_client.read_audit(120)}


@router.post("/audit/deny")
async def audit_deny(body: AuditDenyIn) -> dict[str, Any]:
    mcp_client.record_audit(body.server_id, body.server or body.server_id, body.name, body.args, "denied")
    return {"ok": True}


@router.get("/tools")
async def tools() -> dict[str, Any]:
    try:
        return {"tools": await mcp_client.all_tools()}
    except Exception:
        return {"tools": []}   # fail-closed: never break the caller


@router.post("/connect")
async def connect(body: ConnectIn) -> dict[str, Any]:
    try:
        res = await mcp_client.connect(body.name, body.url, (body.auth or "").strip() or None)
        return {"ok": True, **res}
    except mcp_client.MCPError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="MCP connect failed.")


@router.post("/refresh/{sid}")
async def refresh(sid: str) -> dict[str, Any]:
    try:
        return {"ok": True, "tools": await mcp_client.refresh_tools(sid)}
    except mcp_client.MCPError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="MCP refresh failed.")


@router.delete("/servers/{sid}")
async def remove(sid: str) -> dict[str, Any]:
    return {"ok": mcp_client.disconnect(sid)}


@router.post("/call")
async def call(body: CallIn) -> dict[str, Any]:
    try:
        return await mcp_client.call_tool(body.server_id, body.name, body.args,
                                          approved=body.approved)
    except mcp_client.ApprovalRequired:
        # Not an error: the caller must show the consent popup, then re-call
        # with approved=true. 200 keeps the client flow simple.
        return {"ok": False, "needs_approval": True}
    except mcp_client.MCPError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="MCP tool call failed.")
