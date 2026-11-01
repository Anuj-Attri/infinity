"""Who the account holder is, in their own words.

Nova's persona used to name one person in a module constant: "When <name> is
wrong, impulsive, or hand-waving, you say so." That is fine for an app with one
user and wrong for one with accounts — the second person to sign in would have
been addressed by the first person's name, in every reply, with no way to change
it. It also leaked one user's identity into another user's model context, which
is the same class of failure as a data leak even though no stored row moved.

So the name is state, and state belongs to exactly one account. This module is
the smallest store that can say so: `{account_id: {"display_name": ...}}` on
disk, read per request, never cached.

Deliberately NOT sourced from an env var. `OWNER_NAME` would have been one line
and would have re-created the same bug one layer down — a single global name for
a deployment that is about to have many accounts.

Later this becomes a column on `accounts`, and the name can come from the
identity provider's profile. Both are the same shape as what is here, which is
the point: `display_name(account_id)` does not change when the storage does.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .paths import data_path

_PATH = data_path("account_profile.json")

# A name goes into every model request, so it is bounded. Long enough for a
# real full name, short enough that it cannot be used to smuggle instructions
# into the system prompt.
MAX_NAME_LEN = 64


def _read() -> dict[str, Any]:
    try:
        raw = json.loads(_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as e:
        # Fail closed: an unreadable profile means NO name, which degrades to
        # the neutral persona. It must never raise into a chat turn — a
        # corrupted cosmetic file taking down every reply would be a far worse
        # outcome than an un-personalised one.
        print(f"[profile] unreadable, treating as empty: {e}", flush=True)
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(raw: dict[str, Any]) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    os.replace(tmp, _PATH)


def clean_name(raw: str | None) -> str:
    """Normalise a submitted name, or return "" if it is not usable.

    Newlines are stripped rather than escaped. The name is interpolated into
    the system prompt, so a value containing a line break could open what looks
    to the model like a new instruction block — the one injection vector a free
    text field in the system prompt actually has.
    """
    if not raw:
        return ""
    flat = " ".join(str(raw).split())
    return flat[:MAX_NAME_LEN].strip()


def display_name(account_id: str) -> str:
    """What to call this account holder, or "" if they have not said.

    Empty is a first-class answer, not a missing value to paper over: the
    persona has a neutral form and uses it. Guessing a name would be worse
    than not having one.
    """
    got = _read().get(account_id)
    if not isinstance(got, dict):
        return ""
    return clean_name(got.get("display_name"))


def set_display_name(account_id: str, name: str | None) -> str:
    """Set (or, with an empty value, clear) the name. Returns what was stored."""
    cleaned = clean_name(name)
    raw = _read()
    entry = raw.get(account_id)
    if not isinstance(entry, dict):
        entry = {}
    if cleaned:
        entry["display_name"] = cleaned
    else:
        entry.pop("display_name", None)
    if entry:
        raw[account_id] = entry
    else:
        raw.pop(account_id, None)
    _write(raw)
    return cleaned


def forget(account_id: str) -> None:
    """Remove everything stored for this account."""
    raw = _read()
    if raw.pop(account_id, None) is not None:
        _write(raw)
