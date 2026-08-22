"""Mission persistence — SQLite, WAL, own file (.data/missions.db).

Missions are resumable: every state transition is written through. On
backend restart, missions stuck in a live status are marked 'failed'
with an honest error (the process died) rather than silently vanishing —
fail-closed, never fail-invisible.

No secrets in this file: goals, tasks, outputs, timings only.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .schemas import MissionState
from ..paths import data_path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DB_PATH = data_path("missions.db")
_LOCK = threading.Lock()
_conn: sqlite3.Connection | None = None

_LIVE = ("awaiting_approval", "running", "synthesizing")


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute(
            """CREATE TABLE IF NOT EXISTS missions (
                   id         TEXT PRIMARY KEY,
                   status     TEXT NOT NULL,
                   title      TEXT NOT NULL,
                   created_at REAL NOT NULL,
                   updated_at REAL NOT NULL,
                   state_json TEXT NOT NULL
               )"""
        )
        # WHOSE mission. Added by migration because rows predate it; existing
        # rows are backfilled to the single tenant, which is the true owner —
        # there has only ever been one. Doing this while that is still true is
        # the point: the same backfill after a second account exists would be a
        # guess about which of them owns each row.
        have = {r[1] for r in _conn.execute("PRAGMA table_info(missions)").fetchall()}
        if "account_id" not in have:
            _conn.execute("ALTER TABLE missions ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")
            from ..accounts import SINGLE_TENANT
            _conn.execute("UPDATE missions SET account_id=? WHERE account_id=''",
                          (SINGLE_TENANT.id,))
        _conn.execute("CREATE INDEX IF NOT EXISTS idx_missions_account"
                      " ON missions(account_id, updated_at DESC)")
        _conn.commit()
    return _conn


def recover_interrupted() -> int:
    """Called once at startup. Any mission left in a live status by a dead
    process is closed out as failed. Returns how many were closed.

    DELIBERATELY SPANS ACCOUNTS. A crash does not respect tenancy: every mission
    the dead process was running is stranded, whoever owns it, and leaving other
    accounts' missions stuck in "running" forever would be worse than the sweep.
    It writes no data across accounts — it only closes out what was already
    theirs.
    """
    with _LOCK:
        db = _db()
        rows = db.execute(
            f"SELECT id, state_json FROM missions WHERE status IN "
            f"({','.join('?' * len(_LIVE))})",
            _LIVE,
        ).fetchall()
        n = 0
        for mid, blob in rows:
            try:
                st = MissionState.model_validate_json(blob)
            except Exception:
                db.execute("DELETE FROM missions WHERE id=?", (mid,))
                n += 1
                continue
            st.status = "failed"
            st.error = "backend restarted while the mission was running"
            for ns in st.nodes.values():
                if ns.status in ("pending", "running", "verifying", "retrying"):
                    ns.status = "failed"
                    ns.error = ns.error or "interrupted by backend restart"
            st.touch()
            _write(db, st, account_id=st.account_id)
            n += 1
        db.commit()
        return n


def _write(db: sqlite3.Connection, st: MissionState, *, account_id: str) -> None:
    db.execute(
        """INSERT INTO missions (id, account_id, status, title, created_at, updated_at, state_json)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               status=excluded.status, updated_at=excluded.updated_at,
               state_json=excluded.state_json""",
        (st.id, account_id, st.status, st.title, st.created_at, st.updated_at,
         st.model_dump_json()),
    )


def save(st: MissionState) -> None:
    """Write-through persistence. Never raises into the runtime."""
    try:
        with _LOCK:
            db = _db()
            _write(db, st, account_id=st.account_id)
            db.commit()
    except Exception as e:  # pragma: no cover — disk trouble must not kill a run
        print(f"[mission.store] save failed (non-fatal): {e}", flush=True)


def load(mission_id: str, *, account_id: str) -> MissionState | None:
    """Load a mission BY ID AND OWNER.

    The owner is not optional. Selecting on id alone is the shape that lets
    anyone who can guess an id read someone else's run — prompts, outputs and
    all. It is invisible with one tenant and it is a breach with two, so the
    predicate goes in now while it costs nothing.
    """
    with _LOCK:
        row = _db().execute(
            "SELECT state_json FROM missions WHERE id=? AND account_id=?",
            (mission_id, account_id),
        ).fetchone()
    if not row:
        return None
    try:
        return MissionState.model_validate_json(row[0])
    except Exception:
        return None


def recent(limit: int = 20, *, account_id: str) -> list[dict]:
    with _LOCK:
        rows = _db().execute(
            "SELECT state_json FROM missions WHERE account_id=?"
            " ORDER BY updated_at DESC LIMIT ?",
            (account_id, max(1, min(100, limit))),
        ).fetchall()
    out: list[dict] = []
    for (blob,) in rows:
        try:
            out.append(MissionState.model_validate_json(blob).snapshot())
        except Exception:
            continue
    return out


def prune(older_than_days: float = 30.0, *, account_id: str) -> int:
    """Delete one account's old missions. Never everyone's."""
    cutoff = time.time() - older_than_days * 86400.0
    with _LOCK:
        db = _db()
        cur = db.execute(
            "DELETE FROM missions WHERE account_id=? AND updated_at < ? AND status NOT IN "
            f"({','.join('?' * len(_LIVE))})",
            (account_id, cutoff, *_LIVE),
        )
        db.commit()
        return cur.rowcount
