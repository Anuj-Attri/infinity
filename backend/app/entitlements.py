"""The killswitch — one place that can refuse anything which costs money.

WHY IT IS A REQUEST-PATH CHECK AND NOT A CRON. A scheduled job that disables an
account runs minutes later, and it fails silently when the scheduler dies — so
the one control that is supposed to stop a runaway bill is the control most
likely to be quietly broken when it is needed. This check sits in the dependency
chain: authenticate -> resolve account -> CHECK ENTITLEMENT -> run. There is no
window.

WHY IT IS READ FROM DISK EVERY TIME. Caching the flag for even sixty seconds
means "flip the switch" does not mean "it is off"; it means "it is off soon".
The requirement is that the NEXT request refuses. A SQLite read of
one indexed row is microseconds and this runs once per request, not per token.

WHAT IT DELIBERATELY DOES NOT BLOCK. Signing in, reading data you already have,
and the operator console all keep working. A killswitch that makes the app look
crashed is one nobody dares use, and the point is to stop spend, not to deny the
owner access to their own instance while they work out what happened.

STORAGE. SQLite under DATA_DIR, keyed by account id. It moves to Postgres with
the rest of the state later — `check()` is the only thing anything else calls,
so that swap touches this file alone.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any

from .paths import data_path

_LOCK = threading.Lock()
_conn: sqlite3.Connection | None = None
_DB_PATH = data_path("entitlements.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entitlements (
    account_id   TEXT PRIMARY KEY,
    killswitch   INTEGER NOT NULL DEFAULT 0,
    reason       TEXT NOT NULL DEFAULT '',
    updated_at   REAL NOT NULL DEFAULT 0
);
"""


class Killswitched(Exception):
    """This account may not spend right now.

    Carries the operator's reason so the refusal can say something true instead
    of a generic error. Callers turn it into a 403 — never a 500, which reads as
    "we broke" rather than "we stopped you".
    """

    def __init__(self, reason: str = "") -> None:
        self.reason = reason or "This deployment is paused by its operator."
        super().__init__(self.reason)


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.executescript(_SCHEMA)
        _conn.commit()
    return _conn


def status(account_id: str) -> dict[str, Any]:
    """Current entitlement for one account. Read fresh, never cached."""
    with _LOCK:
        row = _db().execute(
            "SELECT killswitch, reason, updated_at FROM entitlements WHERE account_id=?",
            (account_id,),
        ).fetchone()
    if not row:
        # No row means never switched off. Default ON is correct here: the
        # killswitch is an intervention, and an account that has never been
        # touched has not been intervened on.
        return {"account_id": account_id, "killswitch": False, "reason": "", "updated_at": 0.0}
    return {"account_id": account_id, "killswitch": bool(row[0]),
            "reason": row[1] or "", "updated_at": row[2]}


def check(account_id: str) -> None:
    """Raise Killswitched if this account may not spend. Otherwise return.

    The whole gate. Everything that costs money calls this and nothing else.
    """
    st = status(account_id)
    if st["killswitch"]:
        raise Killswitched(st["reason"])


def assert_may_spend() -> None:
    """The PROVIDER-LAYER gate. Call this immediately before billing anyone.

    A request-level dependency cannot cover everything, and the gap is exactly
    the expensive part: a mission started before the switch was flipped runs in
    a background task for minutes afterwards, making model call after model
    call, on no request path at all. Warmups and scheduled work are the same
    shape. Putting the check where the money is actually spent closes that —
    the request gate is then a nicety that produces a clean 403 instead of a
    failure part-way through a turn.

    Single-tenant for now, so it resolves the one account itself. Later this
    takes an explicit account, like everything else that touches state.
    """
    from .accounts import SINGLE_TENANT   # lazy: accounts imports this module
    check(SINGLE_TENANT.id)


def set_killswitch(account_id: str, on: bool, reason: str = "") -> dict[str, Any]:
    """Flip it. Takes effect on the next request — there is nothing to reload."""
    now = time.time()
    with _LOCK:
        db = _db()
        db.execute(
            "INSERT INTO entitlements (account_id, killswitch, reason, updated_at)"
            " VALUES (?,?,?,?)"
            " ON CONFLICT(account_id) DO UPDATE SET"
            "   killswitch=excluded.killswitch,"
            "   reason=excluded.reason,"
            "   updated_at=excluded.updated_at",
            (account_id, 1 if on else 0, reason.strip()[:400], now),
        )
        db.commit()
    print(f"[entitlements] killswitch {'ON' if on else 'OFF'} for {account_id}"
          f"{' — ' + reason.strip()[:120] if reason.strip() else ''}", flush=True)
    return status(account_id)


def all_switched_off() -> list[dict[str, Any]]:
    """Every account currently stopped. For the operator console."""
    with _LOCK:
        rows = _db().execute(
            "SELECT account_id, reason, updated_at FROM entitlements WHERE killswitch=1"
        ).fetchall()
    return [{"account_id": r[0], "reason": r[1], "updated_at": r[2]} for r in rows]


def _reset_for_tests() -> None:
    """Drop the in-process handle so a test can point DATA_DIR somewhere new."""
    global _conn, _DB_PATH
    with _LOCK:
        if _conn is not None:
            _conn.close()
        _conn = None
    _DB_PATH = data_path("entitlements.db")
