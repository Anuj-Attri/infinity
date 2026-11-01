"""MCP stdio transport — persistent local subprocess sessions.

Streamable-HTTP servers (mcp_client._rpc) are stateless per-op; stdio servers
are the opposite: we spawn the process ONCE (npx cold-starts are seconds), do
the initialize handshake, and keep the session alive across calls. Messages are
newline-delimited JSON-RPC on stdin/stdout per the MCP stdio transport spec.

Fail-closed: a dead/unspawnable server raises MCPError-shaped RuntimeErrors
with short, safe messages; the last stderr lines are kept in a ring buffer for
/mcp/servers diagnostics (never streamed to the model).
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import shutil
import time
from typing import Any

log = logging.getLogger(__name__)

_PROTOCOL = "2025-06-18"
_CLIENT_INFO = {"name": "Infinity", "version": "1.0"}

# First request after spawn may include an npx package download.
_SPAWN_TIMEOUT = 120.0
_REQUEST_TIMEOUT = 120.0
# Tool results (directory trees, page snapshots) can be large.
_STREAM_LIMIT = 10 * 1024 * 1024


class StdioError(RuntimeError):
    """UI-safe stdio-transport failure."""


def _resolve_argv(command: list[str]) -> list[str]:
    """Resolve command[0] through PATH — on Windows `npx` is really `npx.cmd`
    and CreateProcess wants the real file."""
    if not command:
        raise StdioError("empty command")
    exe = shutil.which(command[0])
    if not exe:
        raise StdioError(f"command not found: {command[0]}")
    return [exe, *command[1:]]


class StdioSession:
    """One live MCP server subprocess + JSON-RPC plumbing."""

    def __init__(self, name: str, command: list[str], env: dict[str, str] | None = None):
        self.name = name
        self.command = list(command)
        self.env = dict(env or {})
        self.proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self.stderr_tail: collections.deque[str] = collections.deque(maxlen=20)
        self.started_at: float | None = None

    # -- lifecycle -------------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> None:
        argv = _resolve_argv(self.command)
        env = {**os.environ, **self.env}
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                limit=_STREAM_LIMIT,
            )
        except Exception as e:
            raise StdioError(f"spawn failed: {type(e).__name__}") from e
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self.started_at = time.time()
        # MCP handshake: initialize -> notifications/initialized.
        init = await self.request(
            "initialize",
            {"protocolVersion": _PROTOCOL, "capabilities": {}, "clientInfo": _CLIENT_INFO},
            timeout=_SPAWN_TIMEOUT,
        )
        if not isinstance(init, dict):
            raise StdioError("bad initialize response")
        await self._notify("notifications/initialized")

    async def stop(self) -> None:
        for t in (self._reader_task, self._stderr_task):
            if t:
                t.cancel()
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self._fail_pending("server stopped")
        self.proc = None

    # -- plumbing ----------------------------------------------------------------

    def _fail_pending(self, why: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(StdioError(why))
        self._pending.clear()

    async def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        try:
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                mid = msg.get("id")
                fut = self._pending.pop(mid, None) if mid is not None else None
                if fut and not fut.done():
                    fut.set_result(msg)
                # Requests FROM the server (sampling/roots) and notifications
                # are ignored — we advertise no such capabilities.
        except asyncio.CancelledError:
            return
        except Exception as e:
            log.warning("mcp-stdio[%s]: reader died: %s", self.name, e)
        finally:
            self._fail_pending("server exited")

    async def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        try:
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                txt = line.decode("utf-8", "replace").strip()
                if txt:
                    self.stderr_tail.append(txt[:300])
        except (asyncio.CancelledError, Exception):
            return

    async def _send(self, obj: dict[str, Any]) -> None:
        if not self.alive or not self.proc or not self.proc.stdin:
            raise StdioError("server not running")
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params:
            msg["params"] = params
        await self._send(msg)

    async def request(self, method: str, params: dict[str, Any] | None,
                      timeout: float = _REQUEST_TIMEOUT) -> Any:
        self._next_id += 1
        mid = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
            msg = await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise StdioError(f"{method} timed out")
        finally:
            self._pending.pop(mid, None)
        if "error" in msg:
            raise StdioError(f"{method} error: {str(msg['error'])[:160]}")
        return msg.get("result")


# ── session manager ──────────────────────────────────────────────────────────
# One session per server id; lazy spawn, restart-on-death, per-id lock so two
# concurrent chat turns can't double-spawn the same server.

_SESSIONS: dict[str, StdioSession] = {}
_LOCKS: dict[str, asyncio.Lock] = {}
# Failure cooldown: a server that failed to spawn is not retried for a while,
# so every chat turn doesn't pay the spawn timeout again.
_FAILED_AT: dict[str, float] = {}
_FAIL_COOLDOWN = 60.0


def _lock(sid: str) -> asyncio.Lock:
    if sid not in _LOCKS:
        _LOCKS[sid] = asyncio.Lock()
    return _LOCKS[sid]


def session_status(sid: str) -> dict[str, Any]:
    s = _SESSIONS.get(sid)
    if s and s.alive:
        return {"status": "running", "since": s.started_at}
    if sid in _FAILED_AT:
        return {"status": "failed", "stderr": list((s.stderr_tail if s else []))[-3:]}
    return {"status": "stopped"}


async def get_session(sid: str, name: str, command: list[str],
                      env: dict[str, str] | None = None) -> StdioSession:
    async with _lock(sid):
        s = _SESSIONS.get(sid)
        if s and s.alive:
            return s
        failed = _FAILED_AT.get(sid)
        if failed and (time.time() - failed) < _FAIL_COOLDOWN:
            raise StdioError("server unavailable (recent start failure)")
        if s:
            await s.stop()
        s = StdioSession(name, command, env)
        try:
            await s.start()
        except Exception as e:
            _FAILED_AT[sid] = time.time()
            _SESSIONS[sid] = s          # keep for stderr diagnostics
            tail = "; ".join(list(s.stderr_tail)[-2:])
            log.warning("mcp-stdio[%s]: start failed: %s | %s", name, e, tail)
            try:
                await s.stop()
            except Exception:
                pass
            raise StdioError("server failed to start") from e
        _FAILED_AT.pop(sid, None)
        _SESSIONS[sid] = s
        return s


async def stop_session(sid: str) -> None:
    async with _lock(sid):
        s = _SESSIONS.pop(sid, None)
        _FAILED_AT.pop(sid, None)
        if s:
            await s.stop()


async def stop_all() -> None:
    for sid in list(_SESSIONS):
        try:
            await stop_session(sid)
        except Exception:
            pass
