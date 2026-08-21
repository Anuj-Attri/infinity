"""Notification bus — the EARNED gate is the feature.

Notifications are the next thing that can become annoying, so almost nothing
gets through. Two tiers:

  * JOB (unambiguous value): a background render/research finished, a gated
    action is waiting on approval, a task failed. These bypass the value gate,
    the rate cap, AND quiet hours — they are always delivered, because the
    user explicitly started the work and is waiting on the outcome.

  * EARNED (rare, proactive): nudges (spend crossed a line, a risk), a
    surfaced connection worth interrupting for. Every one of these must clear
    the gate: enabled, a confidence/value threshold ("would the user thank me or
    be annoyed?"), a rolling rate cap, and a quiet-hours window. Default is
    SILENCE. Never fire on a timer or a vibe — only on real signal.

Delivery is tailnet-native, NOT Firebase: the backend queues here and the
phone drains the queue over the tailnet (poll while foreground, drain on
resume) and raises a local notification. No cloud dependency, no new spend.
The trade-off: a fully-killed app won't wake until it next runs — acceptable
for a single-user personal tool, and the stated verify case (backgrounded)
is covered.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from .paths import data_dir

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA = data_dir()
_QUEUE_PATH = _DATA / "notify_queue.json"
_SETTINGS_PATH = _DATA / "notify_settings.json"

_KEEP = 60          # trim the persisted queue to the last N
_lock = threading.Lock()

# Categories that bypass the earned gate entirely (explicit user-initiated work
# whose outcome the user is waiting on).
_JOB_CATEGORIES = frozenset({"job", "approval", "failed"})

_DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,            # master switch for the whole channel
    "briefing_enabled": False,  # opt-in morning briefing, OFF by default
    "quiet_start": 22,          # local hour [0-23]: quiet window start (inclusive)
    "quiet_end": 8,             # local hour: quiet window end (exclusive)
    "min_confidence": 0.7,      # earned notifications must exceed this
    "rate_cap": 3,              # max EARNED notifications per window
    "rate_window_h": 6,         # rolling window for the rate cap (hours)
}

_settings: dict[str, Any] = {}
_queue: list[dict[str, Any]] = []
_next_id = 1
# Delivery timestamps of EARNED notifications, for the rolling rate cap.
_earned_history: list[float] = []


def _load() -> None:
    global _settings, _queue, _next_id
    _settings = dict(_DEFAULT_SETTINGS)
    try:
        if _SETTINGS_PATH.exists():
            _settings.update(json.loads(_SETTINGS_PATH.read_text("utf-8")))
    except Exception:
        pass
    try:
        if _QUEUE_PATH.exists():
            data = json.loads(_QUEUE_PATH.read_text("utf-8"))
            _queue = list(data.get("items") or [])
            _next_id = int(data.get("next_id") or (len(_queue) + 1))
    except Exception:
        _queue = []
        _next_id = 1


def _persist() -> None:
    try:
        _DATA.mkdir(parents=True, exist_ok=True)
        _QUEUE_PATH.write_text(json.dumps(
            {"items": _queue[-_KEEP:], "next_id": _next_id}, indent=2), "utf-8")
    except Exception:
        pass


def _persist_settings() -> None:
    try:
        _DATA.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(json.dumps(_settings, indent=2), "utf-8")
    except Exception:
        pass


_load()


# ---- the gate ----------------------------------------------------------------

def _in_quiet_hours(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    h = now.hour
    start, end = int(_settings["quiet_start"]), int(_settings["quiet_end"])
    if start == end:
        return False
    if start < end:
        return start <= h < end
    return h >= start or h < end        # window wraps midnight (e.g. 22 → 8)


def _rate_ok(now_ts: float) -> bool:
    window = float(_settings["rate_window_h"]) * 3600.0
    recent = [t for t in _earned_history if now_ts - t < window]
    _earned_history[:] = recent
    return len(recent) < int(_settings["rate_cap"])


def gate_reason(category: str, confidence: float) -> str | None:
    """Return None if this notification would be delivered, else a short reason
    it was suppressed. Job-tier categories always pass. Pure (no side effects)
    so callers can preview a decision."""
    if not _settings.get("enabled", True):
        return "channel disabled"
    if category in _JOB_CATEGORIES:
        return None                      # unambiguous value — always through
    if confidence < float(_settings["min_confidence"]):
        return f"below confidence threshold ({confidence:.2f} < {_settings['min_confidence']})"
    if _in_quiet_hours():
        return "quiet hours"
    if not _rate_ok(time.time()):
        return "rate cap reached"
    return None


def enqueue(
    *, category: str, title: str, body: str, route: str | None = None,
    confidence: float = 1.0, dedup_key: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Run the gate, and if it passes, queue the notification for the phone to
    drain. Returns the queued item, or None if the gate suppressed it. The
    suppression path is the common one for EARNED notifications — that IS the
    feature working."""
    global _next_id
    with _lock:
        reason = gate_reason(category, confidence)
        if reason is not None:
            print(f"[NOTIFY] suppressed ({reason}) category={category} "
                  f"conf={confidence:.2f} title={title!r}", flush=True)
            return None
        now_ts = time.time()
        if dedup_key:
            for it in _queue[-_KEEP:]:
                if it.get("dedup_key") == dedup_key and now_ts - it.get("ts", 0) < 3600:
                    return None          # same event within the hour — skip
        item = {
            "id": _next_id, "ts": now_ts, "category": category,
            "title": title, "body": body, "route": route,
            "dedup_key": dedup_key, "data": data or {},
        }
        _next_id += 1
        _queue.append(item)
        if category not in _JOB_CATEGORIES:
            _earned_history.append(now_ts)
        _persist()
        print(f"[NOTIFY] queued #{item['id']} category={category} title={title!r} "
              f"route={route}", flush=True)
        return item


def poll(since_id: int = 0) -> dict[str, Any]:
    """Items the phone hasn't seen yet (id > since_id). Everything here already
    cleared the gate, so the client just delivers it."""
    with _lock:
        items = [it for it in _queue if it["id"] > since_id]
        last_id = _queue[-1]["id"] if _queue else since_id
        return {"items": items, "last_id": last_id}


def get_settings() -> dict[str, Any]:
    with _lock:
        return dict(_settings)


def set_settings(patch: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        for k in _DEFAULT_SETTINGS:
            if k in patch:
                _settings[k] = patch[k]
        _persist_settings()
        return dict(_settings)
