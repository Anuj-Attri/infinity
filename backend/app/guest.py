"""Guest sessions: a throwaway account per browser tab, deleted when it closes.

Turned on with GUEST_SESSIONS=1, for running the app as a public website where
nobody signs in. Off by default; a self-hosted instance keeps its single owner.

HOW IT WORKS
  - The browser asks POST /guest/start and gets a random token. The token maps,
    in this process's memory only, to a brand-new account with its own id and
    its own org. Every store in the app already files state under an account,
    so a guest's notes, files, memories and missions are invisible to everyone
    else by the same predicates that separate any two accounts.
  - When the tab closes the page sends POST /guest/end. The account is not
    deleted at once: a reload also fires that event, so deletion waits a short
    grace period and is cancelled if the same token comes back.
  - A browser does not guarantee the close event arrives. Crashes, killed
    phones and lost connections all skip it. So an idle sweeper deletes any
    guest not seen for GUEST_IDLE_MINUTES, and that sweeper, not the close
    event, is the guarantee.
  - Tokens live in memory, so a restart invalidates them all. Guest ids are
    also written to a small registry file, and boot deletes everything listed
    there, so a restart cannot strand a guest's data on disk.

WHAT A GUEST MAY NOT DO
  Anything that changes the instance for everyone: operator diagnostics, the
  killswitch, model provider keys, connector configuration, the shared
  connector audit log, skill toggles, spend settings and notifications. See
  OPERATOR_ONLY below.

A guest still spends the operator's model credit. Set a spend cap and a hard
credit limit on the provider key before opening an instance to the public.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .accounts import SINGLE_TENANT, Account
from .paths import data_path

log = logging.getLogger(__name__)

_REGISTRY_PATH = data_path("guest_accounts.json")

# Wait this long after a close event before deleting, so a reload survives.
GRACE_S = 45.0
_SWEEP_EVERY_S = 30.0


def enabled() -> bool:
    """Read per call so a test can switch it without reloading the module."""
    return os.environ.get("GUEST_SESSIONS") == "1"


def idle_seconds() -> float:
    try:
        minutes = float(os.environ.get("GUEST_IDLE_MINUTES", "30"))
    except ValueError:
        minutes = 30.0
    return max(1.0, minutes) * 60.0


def max_sessions() -> int:
    try:
        return max(1, int(os.environ.get("GUEST_MAX_SESSIONS", "200")))
    except ValueError:
        return 200


# (method or "*", path prefix). A guest token is refused on all of these.
OPERATOR_ONLY: tuple[tuple[str, str], ...] = (
    ("*", "/admin"),
    ("*", "/auth/devices"),
    ("*", "/auth/issue"),
    ("*", "/auth/reset"),
    ("POST", "/chat-providers/"),
    ("DELETE", "/chat-providers/"),
    ("POST", "/dispatch-providers/"),
    ("DELETE", "/dispatch-providers/"),
    ("POST", "/mcp/connect"),
    ("POST", "/mcp/disconnect-all"),
    ("POST", "/mcp/enabled"),
    ("DELETE", "/mcp/servers"),
    ("POST", "/mcp/trust/"),
    ("POST", "/mcp/refresh/"),
    ("GET", "/mcp/audit"),
    ("POST", "/skills/reindex"),
    ("POST", "/skills/toggle"),
    ("*", "/spend/"),
    ("*", "/usage/"),
    ("POST", "/notify/settings"),
    ("POST", "/notify/test"),
)


def is_operator_only(method: str, path: str) -> bool:
    for m, prefix in OPERATOR_ONLY:
        if (m == "*" or m == method) and path.startswith(prefix):
            return True
    return False


class TooManyGuests(Exception):
    """The instance is at GUEST_MAX_SESSIONS. Refuse rather than fill the disk."""


@dataclass
class _Session:
    account: Account
    last_seen: float
    ending_at: float | None = None


_lock = threading.Lock()
_sessions: dict[str, _Session] = {}      # sha256(token) -> session
_purgers: list[tuple[str, Callable[[str], Any]]] = []


def _key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def register_purger(name: str, fn: Callable[[str], Any]) -> None:
    """Add a function that deletes one store's data for an account id."""
    if any(n == name for n, _ in _purgers):
        return
    _purgers.append((name, fn))


def purger_names() -> list[str]:
    return [n for n, _ in _purgers]


# ---------------------------------------------------------------- registry ---

def _read_registry() -> list[str]:
    if not _REGISTRY_PATH.exists():
        return []
    try:
        raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # Unreadable means we no longer know which ids to clean up. Say so.
        log.error("guest registry unreadable (%s); orphaned guest data may remain", e)
        return []
    return [x for x in raw if isinstance(x, str)] if isinstance(raw, list) else []


def _write_registry(ids: list[str]) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _REGISTRY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(set(ids))), encoding="utf-8")
    os.replace(tmp, _REGISTRY_PATH)


# ---------------------------------------------------------------- lifecycle ---

def start() -> tuple[str, Account]:
    """Mint a token and a brand-new account for it."""
    token = secrets.token_urlsafe(32)
    account = Account(id=str(uuid.uuid4()), org_id=str(uuid.uuid4()), email=None)
    with _lock:
        if len(_sessions) >= max_sessions():
            raise TooManyGuests()
        _sessions[_key(token)] = _Session(account=account, last_seen=time.time())
        _write_registry(_read_registry() + [account.id])
        live = len(_sessions)
    log.info("guest session started (%d live)", live)
    return token, account


def lookup(token: str) -> Account | None:
    """The guest account for this token, or None. Marks the session as seen.

    Seeing a token again cancels a pending close, which is what lets a page
    reload keep its session.
    """
    if not token:
        return None
    with _lock:
        s = _sessions.get(_key(token))
        if s is None:
            return None
        s.last_seen = time.time()
        s.ending_at = None
        return s.account


def end(token: str) -> bool:
    """Schedule deletion after the grace period. True if the token was live."""
    if not token:
        return False
    with _lock:
        s = _sessions.get(_key(token))
        if s is None:
            return False
        s.ending_at = time.time()
        return True


def end_now(token: str) -> bool:
    """Delete immediately. For an explicit "clear my session" action."""
    with _lock:
        s = _sessions.pop(_key(token), None) if token else None
    if s is None:
        return False
    purge(s.account.id)
    return True


def live_count() -> int:
    with _lock:
        return len(_sessions)


def purge(account_id: str) -> bool:
    """Run every registered purger for this id. True if all of them succeeded.

    A failure is logged and the id stays in the registry, so the next boot
    tries again. Never the single tenant: that is the owner's real data.
    """
    if account_id == SINGLE_TENANT.id:
        raise ValueError("refusing to purge the instance owner's account")
    uuid.UUID(account_id)   # raises on anything that is not an id we minted
    ok = True
    for name, fn in _purgers:
        try:
            fn(account_id)
        except Exception as e:
            ok = False
            log.error("guest purge: %s failed for %s: %s: %s",
                      name, account_id, type(e).__name__, e)
    if ok:
        with _lock:
            _write_registry([x for x in _read_registry() if x != account_id])
    return ok


def sweep(now: float | None = None) -> int:
    """Delete sessions past their grace period or idle too long."""
    now = time.time() if now is None else now
    idle = idle_seconds()
    expired: list[Account] = []
    with _lock:
        for k, s in list(_sessions.items()):
            closed = s.ending_at is not None and now - s.ending_at >= GRACE_S
            if closed or now - s.last_seen >= idle:
                expired.append(s.account)
                del _sessions[k]
    for a in expired:
        purge(a.id)
    if expired:
        log.info("guest sweep: deleted %d session(s), %d live",
                 len(expired), live_count())
    return len(expired)


def purge_orphans() -> int:
    """At boot: delete every guest the registry lists. No token survives a
    restart, so none of them can ever be used again."""
    ids = _read_registry()
    for gid in ids:
        try:
            purge(gid)
        except ValueError as e:
            log.error("guest registry held an invalid id %r: %s", gid, e)
    return len(ids)


async def run_sweeper() -> None:
    while True:
        await asyncio.sleep(_SWEEP_EVERY_S)
        try:
            await asyncio.to_thread(sweep)
        except Exception as e:
            log.error("guest sweeper failed: %s: %s", type(e).__name__, e)
