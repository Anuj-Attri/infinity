"""Generic MCP (Model Context Protocol) client — connect ANY MCP server.

This is the connect-any-MCP MECHANISM, not a one-off per service: you add a
server (a Streamable-HTTP MCP endpoint URL + optional bearer token), we
`initialize` + `tools/list` it, and its tools become callable. Nova invokes them
through the existing <nova_action> tool loop via the `mcp_call` action (the
frontend executes it against /mcp/call and re-asks with the result) — so this
slots into the dispatch/tool architecture without changing how tools are run.

Transports:
  http     — MCP **Streamable HTTP** (one POST endpoint; replies as
             application/json OR text/event-stream). Stateless from our side —
             each op runs initialize → initialized → <op> in a short-lived
             client, carrying the Mcp-Session-Id the server hands back.
  stdio    — local subprocess servers (mcp_stdio.py): spawned once, kept alive,
             newline-delimited JSON-RPC. This is how the built-in "hands"
             (filesystem, Playwright browser) run.
  internal — in-process python modules (mcp_self.py) exposing the same
             tools/list + tools/call shape. This is the Shell hand.

The built-in hands are CONFIG-DRIVEN: backend/mcp.hands.json lists them;
ensure_builtin_servers() syncs that file into the registry at startup.

RULE 7 / fail-closed: this is its OWN module + key store (mcp_servers.json,
tokens DPAPI-encrypted); it does NOT import chat/dispatch providers. Every
failure is caught and surfaced as a short, safe message — never a stack/secret.

CONSENT GATE: every tool is classified read|gated (classify_risk — fail-closed:
anything not provably read-only is gated). call_tool REFUSES a gated tool
without approved=True and raises ApprovalRequired; the frontend turns that into
the approval popup showing the exact action + args, then re-calls approved.
Read-only tools auto-run. Internal (shell) tools are ALWAYS gated.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import httpx

from . import mcp_self, mcp_stdio
from .providers import keystore

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STORE_PATH = _REPO_ROOT / ".data" / "mcp_servers.json"
_STATE_PATH = _REPO_ROOT / ".data" / "mcp_state.json"     # {enabled: bool} — global kill switch
_AUDIT_PATH = _REPO_ROOT / ".data" / "mcp_audit.jsonl"    # one line per tool call (no secrets)
_HANDS_PATH = _REPO_ROOT / "backend" / "mcp.hands.json"   # config-driven built-in servers

_PROTOCOL = "2025-06-18"
_CLIENT_INFO = {"name": "Infinity (Reactor Mk3)", "version": "1.0"}
_TIMEOUT = 30.0

# In-memory cache of the last successful tools/list per server id.
_TOOLS_CACHE: dict[str, list[dict[str, Any]]] = {}


# ── registry persistence ─────────────────────────────────────────────────────

def _load() -> dict[str, dict[str, Any]]:
    try:
        return json.loads(_STORE_PATH.read_text("utf-8"))
    except Exception:
        return {}


def _save(d: dict[str, dict[str, Any]]) -> None:
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STORE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2), "utf-8")
        tmp.replace(_STORE_PATH)
    except Exception as e:
        log.warning("mcp: save failed: %s", e)


def _server_id(url: str) -> str:
    import hashlib
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def _auth_token(rec: dict[str, Any]) -> str | None:
    enc = rec.get("auth_enc")
    return keystore.decrypt_blob(enc) if enc else None


# ── global kill switch ───────────────────────────────────────────────────────

def is_enabled() -> bool:
    """Global MCP master switch. Default ON; flip OFF to expose ZERO tools to
    Nova and refuse every call — a one-flag kill switch."""
    try:
        return bool(json.loads(_STATE_PATH.read_text("utf-8")).get("enabled", True))
    except Exception:
        return True


def set_enabled(on: bool) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps({"enabled": bool(on)}), "utf-8")
    except Exception as e:
        log.warning("mcp: state save failed: %s", e)


# ── transport-trust + injection sanitising ───────────────────────────────────

def is_insecure(url: str) -> bool:
    """True for EXTERNAL plain-http URLs. http to localhost / RFC1918 / tailnet
    (100.64-127.x, *.ts.net) is fine; external http is flagged insecure."""
    from urllib.parse import urlparse
    try:
        u = urlparse(url)
    except Exception:
        return True
    if u.scheme == "https":
        return False
    host = (u.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".ts.net"):
        return False
    if host.startswith(("10.", "192.168.")):
        return False
    if host.startswith("100."):
        try:
            if 64 <= int(host.split(".")[1]) <= 127:   # tailnet CGNAT range
                return False
        except Exception:
            pass
    if host.startswith("172."):
        try:
            if 16 <= int(host.split(".")[1]) <= 31:
                return False
        except Exception:
            pass
    return True   # external http → insecure


def sanitize_for_prompt(s: str, limit: int = 160) -> str:
    """Neutralise MCP-supplied TEXT before it enters the system prompt. Strips
    control chars / newlines (so a description can't open a new instruction
    block) and defangs the classic injection verbs. The result is DATA only."""
    s = (s or "")
    s = "".join(ch if (ch == " " or ch.isprintable()) else " " for ch in s)
    s = " ".join(s.split())                       # collapse whitespace/newlines
    # Defang common override phrases so a poisoned description reads inert.
    for bad in ("ignore previous", "ignore all previous", "disregard previous",
                "system prompt", "you are now", "new instructions", "</", "<nova_action"):
        s = s.replace(bad, bad.replace(" ", "·").replace("<", "‹").replace("/", "∕"))
    return s[:limit]


# ── audit log ────────────────────────────────────────────────────────────────

def record_audit(server_id: str, server_name: str, tool: str, args: Any, decision: str) -> None:
    """Append one tool-call event (no secrets — args are the model's, never the
    bearer token). decision ∈ approved|executed|denied|blocked."""
    import time as _t
    try:
        line = json.dumps({
            "ts": _t.time(), "server_id": server_id, "server": server_name,
            "tool": tool, "args": args, "decision": decision,
        }, default=str)[:4000]
        _AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        log.warning("mcp: audit write failed: %s", e)


def read_audit(limit: int = 100) -> list[dict[str, Any]]:
    try:
        lines = _AUDIT_PATH.read_text("utf-8").splitlines()[-limit:]
    except Exception:
        return []
    out = []
    for ln in reversed(lines):                    # newest first
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


# ── Streamable-HTTP JSON-RPC ─────────────────────────────────────────────────

class MCPError(RuntimeError):
    """UI-safe MCP failure."""


class ApprovalRequired(MCPError):
    """A gated tool was called without user approval — the caller must show
    the consent popup and re-call with approved=True."""


# ── consent-gate risk classification ─────────────────────────────────────────
# read  → auto-runs (no popup). gated → ALWAYS needs per-call user approval.
# Fail-closed: a tool is `read` only if it is provably read-only; anything
# ambiguous, unannotated, or matching an action/spend keyword is gated.

# Destructive / outbound / money verbs — these gate a tool NO MATTER what its
# (untrusted, server-supplied) annotations claim.
_GATED_NAME_RE = re.compile(
    r"(write|edit|delete|remove|move|rename|create|mkdir|copy|install|uninstall|"
    r"order|buy|purchase|pay|checkout|cart|send|post|submit|publish|upload|"
    r"commit|push|merge|deploy|execute|run|kill|drop|truncate|update|set_|"
    r"click|type|fill|press|drag|select_option|upload|navigate|close|resize|"
    r"evaluate|dialog)",
    re.IGNORECASE,
)

# Conservative read-only name shapes — fallback for servers that don't ship
# readOnlyHint annotations (only consulted after the gated-keyword check).
_READ_NAME_RE = re.compile(
    r"^(read_|list_|get_|search_|query_|fetch_|directory_tree$|browser_snapshot$|"
    r"browser_take_screenshot$|browser_console_messages$|browser_network_requests$)",
    re.IGNORECASE,
)


def classify_risk(rec: dict[str, Any], tool: dict[str, Any]) -> str:
    """'read' (auto-run) or 'gated' (approval popup). Fail-closed."""
    if rec.get("transport") == "internal":
        return "gated"                       # shell: every call asks, always
    name = str(tool.get("name") or "")
    if _GATED_NAME_RE.search(name):
        return "gated"                       # verbs override annotations
    ann = tool.get("annotations") or {}
    if isinstance(ann, dict) and ann.get("readOnlyHint") is True:
        return "read"
    if _READ_NAME_RE.search(name):
        return "read"
    return "gated"                           # unknown → ask


def _parse_rpc_response(r: httpx.Response, want_id: int) -> dict[str, Any]:
    """Body is application/json OR text/event-stream — return the JSON-RPC
    message matching want_id (or the first result/error)."""
    ctype = r.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in r.text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                msg = json.loads(line[len("data:"):].strip())
            except Exception:
                continue
            if msg.get("id") == want_id or "result" in msg or "error" in msg:
                return msg
        raise MCPError("no JSON-RPC message in SSE stream")
    return r.json()


async def _session(client: httpx.AsyncClient, base_headers: dict[str, str]) -> dict[str, str]:
    """initialize + notifications/initialized → headers incl. any session id."""
    init_id = 1
    payload = {
        "jsonrpc": "2.0", "id": init_id, "method": "initialize",
        "params": {"protocolVersion": _PROTOCOL, "capabilities": {}, "clientInfo": _CLIENT_INFO},
    }
    r = await client.post("", json=payload, headers=base_headers)
    if r.status_code >= 400:
        raise MCPError(f"initialize HTTP {r.status_code}")
    msg = _parse_rpc_response(r, init_id)
    if "error" in msg:
        raise MCPError(f"initialize error: {str(msg['error'])[:120]}")
    headers = dict(base_headers)
    sid = r.headers.get("mcp-session-id")
    if sid:
        headers["Mcp-Session-Id"] = sid
    # Best-effort initialized notification (no id, no response expected).
    try:
        await client.post("", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=headers)
    except Exception:
        pass
    return headers


async def _rpc(url: str, token: str | None, method: str, params: dict[str, Any] | None) -> Any:
    base_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if token:
        base_headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(base_url=url, timeout=_TIMEOUT) as client:
        headers = await _session(client, base_headers)
        call_id = 2
        r = await client.post("", json={"jsonrpc": "2.0", "id": call_id, "method": method, "params": params or {}}, headers=headers)
        if r.status_code >= 400:
            raise MCPError(f"{method} HTTP {r.status_code}")
        msg = _parse_rpc_response(r, call_id)
        if "error" in msg:
            raise MCPError(f"{method} error: {str(msg['error'])[:160]}")
        return msg.get("result")


# ── transport dispatch ───────────────────────────────────────────────────────

def _transport(rec: dict[str, Any]) -> str:
    return str(rec.get("transport") or "http")


async def _list_tools_raw(sid: str, rec: dict[str, Any]) -> list[dict[str, Any]]:
    tr = _transport(rec)
    if tr == "internal":
        if rec.get("module") == "self":
            return mcp_self.get_tools()
        raise MCPError(f"unknown internal MCP module: {rec.get('module')!r}")
    if tr == "stdio":
        try:
            s = await mcp_stdio.get_session(sid, rec.get("name") or sid,
                                            rec.get("command") or [], rec.get("env"))
            result = await s.request("tools/list", {})
        except mcp_stdio.StdioError as e:
            raise MCPError(str(e))
        return (result or {}).get("tools") or []
    result = await _rpc(rec["url"], _auth_token(rec), "tools/list", {})
    return (result or {}).get("tools") or []


async def _call_raw(sid: str, rec: dict[str, Any], name: str, args: dict[str, Any]) -> dict[str, Any]:
    tr = _transport(rec)
    if tr == "internal":
        if rec.get("module") == "self":
            return await mcp_self.call(name, args or {})
        raise MCPError(f"unknown internal MCP module: {rec.get('module')!r}")
    if tr == "stdio":
        try:
            s = await mcp_stdio.get_session(sid, rec.get("name") or sid,
                                            rec.get("command") or [], rec.get("env"))
            return (await s.request("tools/call", {"name": name, "arguments": args or {}})) or {}
        except mcp_stdio.StdioError as e:
            raise MCPError(str(e))
    return (await _rpc(rec["url"], _auth_token(rec), "tools/call",
                       {"name": name, "arguments": args or {}})) or {}


# ── built-in hands (config-driven) ───────────────────────────────────────────

def ensure_builtin_servers() -> list[str]:
    """Sync backend/mcp.hands.json into the registry as `hand-<key>` entries.
    Idempotent; called at startup. New hands seed TRUSTED (they are our own
    local config, not a third-party URL) but a user un-trust via /mcp/trust is
    PRESERVED across restarts. Hands removed from the config are removed from
    the registry. Returns the seeded server ids."""
    try:
        cfg = json.loads(_HANDS_PATH.read_text("utf-8"))
    except Exception as e:
        log.warning("mcp: hands config unreadable: %s", e)
        return []
    d = _load()
    seeded: list[str] = []
    keys = {k for k in cfg if not k.startswith("_")}
    for key in keys:
        spec = cfg[key] or {}
        sid = f"hand-{key}"
        prev = d.get(sid) or {}
        rec = {
            "name": spec.get("name") or key,
            "transport": spec.get("transport") or "stdio",
            "module": spec.get("module") or "",
            "command": spec.get("command") or [],
            "env": spec.get("env") or {},
            "builtin": True,
            "trusted": prev.get("trusted", True),   # preserve explicit un-trust
            "connected_at": prev.get("connected_at") or time.time(),
        }
        d[sid] = rec
        seeded.append(sid)
    # Drop hands that left the config.
    for sid in [s for s in d if s.startswith("hand-") and s.removeprefix("hand-") not in keys]:
        d.pop(sid, None)
        _TOOLS_CACHE.pop(sid, None)
    _save(d)
    return seeded


async def startup_warm() -> None:
    """Background startup task: seed the hands + pre-list their tools so the
    first chat turn doesn't pay npx spawn latency. Every failure is logged and
    swallowed — a dead hand just exposes no tools (fail-closed)."""
    try:
        for sid in ensure_builtin_servers():
            try:
                await refresh_tools(sid)
                log.info("mcp: hand %s ready (%d tools)", sid, len(_TOOLS_CACHE.get(sid, [])))
            except Exception as e:
                log.warning("mcp: hand %s not available: %s", sid, e)
    except Exception as e:
        log.warning("mcp: startup warm failed: %s", e)


async def shutdown() -> None:
    await mcp_stdio.stop_all()


# ── public API ───────────────────────────────────────────────────────────────

def list_servers() -> list[dict[str, Any]]:
    """Connected servers (no secrets). `trusted` gates tool exposure; an
    untrusted server shows exposed=0 even if it has tools cached."""
    out = []
    for sid, rec in _load().items():
        trusted = bool(rec.get("trusted"))
        tr = _transport(rec)
        if tr == "stdio":
            status = mcp_stdio.session_status(sid).get("status", "stopped")
        elif tr == "internal":
            status = "running"
        else:
            status = "http"
        out.append({
            "id": sid,
            "name": rec.get("name") or rec.get("url"),
            "url": rec.get("url"),
            "transport": tr,
            "builtin": bool(rec.get("builtin")),
            "status": status,
            "has_auth": bool(rec.get("auth_enc")),
            "trusted": trusted,
            "insecure": bool(rec.get("insecure")),
            "tool_count": len(_TOOLS_CACHE.get(sid, [])),
            "exposed": len(_TOOLS_CACHE.get(sid, [])) if (trusted and is_enabled()) else 0,
            "connected_at": rec.get("connected_at"),
        })
    return out


async def connect(name: str, url: str, auth_token: str | None) -> dict[str, Any]:
    """Validate (tools/list) + persist as UNTRUSTED. Returns the tool LIST so the
    user can review capabilities before trusting. Until trust() is called, the
    server exposes ZERO tools to Nova (default-deny). Connect ≠ trust."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise MCPError("URL must be http(s)://")
    result = await _rpc(url, auth_token, "tools/list", {})
    tools = (result or {}).get("tools") or []
    sid = _server_id(url)
    insecure = is_insecure(url)
    rec: dict[str, Any] = {
        "name": (name or url).strip(), "url": url, "connected_at": time.time(),
        "trusted": False, "insecure": insecure,
    }
    if auth_token:
        rec["auth_enc"] = keystore.encrypt_blob(auth_token)
    d = _load(); d[sid] = rec; _save(d)
    _TOOLS_CACHE[sid] = tools
    return {"id": sid, "name": rec["name"], "trusted": False, "insecure": insecure,
            "tools": [{"name": t.get("name"), "description": t.get("description") or "",
                       "input_schema": t.get("inputSchema") or {}} for t in tools]}


def trust(sid: str, trusted: bool = True) -> bool:
    """Explicit consent gate — only a trusted server exposes its tools to Nova."""
    d = _load()
    rec = d.get(sid)
    if not rec:
        raise MCPError("unknown server")
    rec["trusted"] = bool(trusted)
    _save(d)
    return rec["trusted"]


def disconnect(sid: str) -> bool:
    d = _load()
    existed = d.pop(sid, None) is not None
    _save(d)
    _TOOLS_CACHE.pop(sid, None)
    try:
        import asyncio
        asyncio.get_running_loop().create_task(mcp_stdio.stop_session(sid))
    except Exception:
        pass
    return existed


def disconnect_all() -> int:
    """Kill switch — forget every server + its tools."""
    d = _load()
    n = len([k for k in d if not k.startswith("_")])
    _save({})
    _TOOLS_CACHE.clear()
    return n


async def refresh_tools(sid: str) -> list[dict[str, Any]]:
    rec = _load().get(sid)
    if not rec:
        raise MCPError("unknown server")
    tools = await _list_tools_raw(sid, rec)
    _TOOLS_CACHE[sid] = tools
    return tools


async def all_tools() -> list[dict[str, Any]]:
    """Flattened tools EXPOSED to Nova — ONLY from servers that are (a) globally
    enabled AND (b) explicitly TRUSTED. Untrusted/disabled → nothing. Each tool
    carries its server_id so a call can't be ambiguous or cross-server."""
    if not is_enabled():
        return []
    out: list[dict[str, Any]] = []
    for sid, rec in _load().items():
        if not rec.get("trusted"):
            continue   # default-deny: untrusted servers expose no tools
        tools = _TOOLS_CACHE.get(sid)
        if tools is None:
            try:
                tools = await refresh_tools(sid)
            except Exception as e:
                log.warning("mcp: tools/list failed for %s: %s", sid, e)
                tools = []
        for t in tools:
            out.append({
                "server_id": sid,
                "server_name": rec.get("name") or rec.get("url"),
                "name": t.get("name"),
                "description": t.get("description") or "",
                "input_schema": t.get("inputSchema") or {},
                "risk": classify_risk(rec, t),   # read → auto-run, gated → popup
            })
    return out


async def call_tool(sid: str, name: str, args: dict[str, Any],
                    approved: bool = False) -> dict[str, Any]:
    """Call one tool — ENFORCED least-privilege: the global switch must be on,
    the server must be TRUSTED, `name` must be one of THAT server's listed
    tools (no arbitrary/cross-server calls), and a GATED tool refuses to run
    without approved=True (the consent gate — raises ApprovalRequired so the
    frontend can pop the exact-args approval). Every call is audited."""
    rec = _load().get(sid)
    server_name = (rec or {}).get("name") or sid
    if not is_enabled():
        record_audit(sid, server_name, name, args, "blocked")
        raise MCPError("MCP is turned off")
    if not rec:
        record_audit(sid, server_name, name, args, "blocked")
        raise MCPError("unknown server")
    if not rec.get("trusted"):
        record_audit(sid, server_name, name, args, "blocked")
        raise MCPError("server not trusted")
    tools = _TOOLS_CACHE.get(sid)
    if tools is None:                        # e.g. first call after a restart
        try:
            tools = await refresh_tools(sid)
        except Exception:
            tools = []
    tool = next((t for t in tools if t.get("name") == name), None)
    if tools and tool is None:
        record_audit(sid, server_name, name, args, "blocked")
        raise MCPError("tool not in this server's published list")
    # CONSENT GATE — the server-side backstop. Fail-closed: if we can't see
    # the tool's metadata, it's treated as gated.
    risk = classify_risk(rec, tool) if tool else "gated"
    if risk == "gated" and not approved:
        record_audit(sid, server_name, name, args, "needs_approval")
        raise ApprovalRequired("approval required")
    record_audit(sid, server_name, name, args, "approved" if approved else "executed")
    result = await _call_raw(sid, rec, name, args or {})
    parts: list[str] = []
    for c in (result.get("content") or []):
        if isinstance(c, dict):
            if c.get("type") == "text":
                parts.append(str(c.get("text") or ""))
            elif c.get("type") == "resource":
                parts.append(str((c.get("resource") or {}).get("text") or ""))
            elif c.get("type") == "image":
                mime = str(c.get("mimeType") or "image")
                parts.append(f"[{mime} image returned — {len(str(c.get('data') or ''))} b64 chars, omitted]")
    text = "\n".join(p for p in parts if p) or json.dumps(result)[:4000]
    return {"ok": not result.get("isError"), "text": text, "is_error": bool(result.get("isError"))}
