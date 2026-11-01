"""Where runtime state lives. One answer, in one place.

The desktop version computed this twelve separate times — `parents[2] / ".data"` in most
modules, `parents[3]` in the keystore — which meant there was no single thing to
change when the answer changed. It has now changed.

**The container filesystem is ephemeral.** Every redeploy wipes it, which takes
the master password with it, so the hosted instance drops back to first-run
setup after each deploy. That is not a bug in the code; it is what a container
is. The fix is a mounted volume, and a volume is only useful if the app reads
one variable to find it:

    DATA_DIR=/data      # a Railway/Fly/Docker volume mount point

Unset, it falls back to `<repo>/.data`, which is what local development wants
and what the desktop version always did.

This is a stopgap with a known end: later this state becomes Postgres rows with
`org_id` and `owner_account_id`, because a shared filesystem cannot express
"belongs to exactly one account" no matter where it is mounted.
"""

from __future__ import annotations

import os
from pathlib import Path

# <repo>/backend/app/paths.py -> parents[2] is <repo>
_REPO_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """The directory holding SQLite files and JSON state. Created on demand."""
    raw = (os.environ.get("DATA_DIR") or "").strip()
    d = Path(raw) if raw else (_REPO_ROOT / ".data")
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        # Not raised here: at import time this would abort startup with a worse
        # message than the write that actually fails. But it is not swallowed
        # either — a read-only or missing DATA_DIR mount is the difference
        # between "state persists" and "state silently does not", and that
        # deserves a line in the log rather than nothing at all.
        print(f"[paths] cannot create DATA_DIR {d}: {e}", flush=True)
    return d


def data_path(*parts: str) -> Path:
    """A file inside the data directory."""
    return data_dir().joinpath(*parts)


def is_ephemeral() -> bool:
    """True when state is on the container's own disk and will not survive a
    redeploy. Surfaced by the operator console so the reset is expected rather
    than mysterious."""
    return not (os.environ.get("DATA_DIR") or "").strip()


DATA_DIR = data_dir()
