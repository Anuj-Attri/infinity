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

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DB_PATH = _REPO_ROOT / ".data" / "missions.db"
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
        _conn.commit()
    return _conn


def recover_interrupted() -> int:
    """Called once at startup. Any mission left in a live status by a dead
    process is closed out as failed. Returns how many were closed."""
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
            _write(db, st)
            n += 1
        db.commit()
        return n


def _write(db: sqlite3.Connection, st: MissionState) -> None:
    db.execute(
        """INSERT INTO missions (id, status, title, created_at, updated_at, state_json)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               status=excluded.status, updated_at=excluded.updated_at,
               state_json=excluded.state_json""",
        (st.id, st.status, st.title, st.created_at, st.updated_at,
         st.model_dump_json()),
    )


def save(st: MissionState) -> None:
    """Write-through persistence. Never raises into the runtime."""
    try:
        with _LOCK:
            db = _db()
            _write(db, st)
            db.commit()
    except Exception as e:  # pragma: no cover — disk trouble must not kill a run
        print(f"[mission.store] save failed (non-fatal): {e}", flush=True)


def load(mission_id: str) -> MissionState | None:
    with _LOCK:
        row = _db().execute(
            "SELECT state_json FROM missions WHERE id=?", (mission_id,)
        ).fetchone()
    if not row:
        return None
    try:
        return MissionState.model_validate_json(row[0])
    except Exception:
        return None


def recent(limit: int = 20) -> list[dict]:
    with _LOCK:
        rows = _db().execute(
            "SELECT state_json FROM missions ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(100, limit)),),
        ).fetchall()
    out: list[dict] = []
    for (blob,) in rows:
        try:
            out.append(MissionState.model_validate_json(blob).snapshot())
        except Exception:
            continue
    return out


def prune(older_than_days: float = 30.0) -> int:
    cutoff = time.time() - older_than_days * 86400.0
    with _LOCK:
        db = _db()
        cur = db.execute(
            "DELETE FROM missions WHERE updated_at < ? AND status NOT IN "
            f"({','.join('?' * len(_LIVE))})",
            (cutoff, *_LIVE),
        )
        db.commit()
        return cur.rowcount
