"""Spend accounting — tokens and USD per provider / model / mission.

Foundation for the cost HUD (Usage tile) and the budget guard. No secrets
live here: .data/spend.json holds only counts and dollars, safe to read.

Contract (frozen — COST-2 and the orchestrator build against this):

  record(provider, model, in_tok, out_tok, mission_id=None) -> float
      Adds a usage event to the current month's rolling total. Returns the
      USD cost of THIS event. Unknown models cost 0 (logged) — never raise
      into a streaming path.

  summary() -> {
      "month":       "YYYY-MM",
      "total_usd":   float,
      "by_provider": { provider: usd, ... },
      "by_mission":  { mission_id: usd, ... },   # only tagged events
      "cap_usd":     float | None,
      "over_cap":    bool,
      "warn":        bool,                        # >= 80% of cap
  }

  set_cap(usd | None) -> None
  get_cap() -> float | None
  guard_ok() -> bool        # False when over cap → caller drops to local CPU fallback

Pricing: USD per 1M tokens (input, output). Kept current with the chat +
dispatch model lists. Unknown ids fall through to a conservative default so
an untracked model still shows *some* cost rather than silently zero.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import data_path

log = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEND_PATH = data_path("spend.json")
_LOCK = threading.Lock()

# USD per 1,000,000 tokens: (input, output). Matches current Anthropic + the
# OpenRouter dispatch tiers. Prefix-matched longest-first so dated ids resolve.
PRICING: dict[str, tuple[float, float]] = {
    # Anthropic (chat BYOK)
    "claude-fable-5":            (10.0, 50.0),
    "claude-opus-4-8":           (5.0,  25.0),
    "claude-opus":               (5.0,  25.0),
    "claude-sonnet-4-6":         (3.0,  15.0),
    "claude-sonnet":             (3.0,  15.0),
    "claude-haiku":              (1.0,   5.0),
    # OpenAI (chat BYOK)
    "gpt-5-mini":                (0.30,  1.20),
    "gpt-5":                     (1.25, 10.0),
    "gpt-4o-mini":               (0.15,  0.60),
    "gpt-4o":                    (2.50, 10.0),
    # Gemini (chat BYOK) — representative
    "gemini-2.5-pro":            (1.25, 10.0),
    "gemini-2.5-flash":          (0.30,  2.50),
    "gemini":                    (0.30,  2.50),
    # OpenRouter (chat + dispatch share the upstream id namespace 'vendor/model')
    "openai/gpt-4o-mini":            (0.15,  0.60),
    "openai/gpt-5-mini":             (0.25,  2.0),
    "openai/gpt-5":                  (1.25, 10.0),
    "anthropic/claude-sonnet-4.5":   (3.0,  15.0),
    "anthropic/claude-haiku-4.5":    (1.0,   5.0),
    "anthropic/claude-3.5-sonnet":   (3.0,  15.0),
    "google/gemini-3.1-flash-lite":  (0.25,  1.5),
    "google/gemini-2.5-flash":       (0.30,  2.5),
    # Local CPU fallback — free.
    "llama3.2":                  (0.0,   0.0),
    "llama":                     (0.0,   0.0),
    "qwen":                      (0.0,   0.0),
    "local":                     (0.0,   0.0),
}
# Conservative fallback for an unrecognised id (so it isn't silently free).
_DEFAULT_PRICE = (3.0, 15.0)


def _price_for(model: str) -> tuple[float, float]:
    m = (model or "").lower()
    # Longest prefix wins so "claude-opus-4-8" beats "claude-opus".
    best: tuple[float, float] | None = None
    best_len = -1
    for key, price in PRICING.items():
        k = key.lower()
        if m.startswith(k) and len(k) > best_len:
            best, best_len = price, len(k)
    if best is not None:
        return best
    log.info("spend: no price for model %r — using default", model)
    return _DEFAULT_PRICE


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _empty(month: str) -> dict[str, Any]:
    return {
        "month": month,
        "total_usd": 0.0,
        "by_provider": {},
        "by_mission": {},
    }


def _load() -> dict[str, Any]:
    month = _month_key()
    try:
        data = json.loads(_SPEND_PATH.read_text("utf-8"))
    except Exception:
        data = {}
    # Roll over at month boundary — keep cap, reset counters.
    if data.get("month") != month:
        cap = data.get("cap_usd")
        data = _empty(month)
        if cap is not None:
            data["cap_usd"] = cap
    data.setdefault("by_provider", {})
    data.setdefault("by_mission", {})
    data.setdefault("total_usd", 0.0)
    return data


def _save(data: dict[str, Any]) -> None:
    try:
        _SPEND_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SPEND_PATH.write_text(json.dumps(data, indent=2), "utf-8")
    except Exception as e:
        log.warning("spend: save failed: %s", e)


# Daily-spend nudge — a REAL signal (you crossed $X of API spend today), not a
# timer. In-memory tally (resets on restart; the rate cap keeps a post-restart
# re-notify harmless). Threshold in USD; 0 disables.
import os as _os

_DAILY_NUDGE_USD = float(_os.environ.get("SPEND_DAILY_NUDGE_USD", "5.0"))
_day_state = {"day": "", "usd": 0.0, "nudged": False}


def _daily_nudge(cost: float) -> None:
    if _DAILY_NUDGE_USD <= 0:
        return
    from datetime import date
    today = date.today().isoformat()
    if _day_state["day"] != today:
        _day_state.update(day=today, usd=0.0, nudged=False)
    _day_state["usd"] += cost
    if _day_state["usd"] >= _DAILY_NUDGE_USD and not _day_state["nudged"]:
        _day_state["nudged"] = True
        try:
            from . import notify
            # High confidence — crossing a daily spend line is genuinely worth a
            # heads-up. Still passes through the gate (quiet hours, rate cap).
            notify.enqueue(
                category="nudge", confidence=0.85,
                title="Spend heads-up",
                body=f"API spend crossed ${_DAILY_NUDGE_USD:.0f} today (${_day_state['usd']:.2f} so far).",
                route="tile:usage", dedup_key=f"spend-daily:{today}",
            )
        except Exception:
            pass


def record(
    provider: str,
    model: str,
    in_tok: int,
    out_tok: int,
    mission_id: str | None = None,
) -> float:
    """Tally a usage event. Returns the USD cost of this event. Never raises."""
    try:
        pin, pout = _price_for(model)
        cost = (max(0, in_tok) / 1_000_000.0) * pin + (max(0, out_tok) / 1_000_000.0) * pout
        if cost <= 0:
            return 0.0
        with _LOCK:
            data = _load()
            data["total_usd"] = round(data["total_usd"] + cost, 6)
            bp = data["by_provider"]
            bp[provider] = round(bp.get(provider, 0.0) + cost, 6)
            if mission_id:
                bm = data["by_mission"]
                bm[mission_id] = round(bm.get(mission_id, 0.0) + cost, 6)
            _save(data)
        _daily_nudge(cost)
        return cost
    except Exception as e:
        log.warning("spend.record failed (non-fatal): %s", e)
        return 0.0


def summary() -> dict[str, Any]:
    with _LOCK:
        data = _load()
        cap = data.get("cap_usd")
        total = data.get("total_usd", 0.0)
        over = cap is not None and total >= cap
        warn = cap is not None and cap > 0 and total >= 0.8 * cap
        return {
            "month": data["month"],
            "total_usd": round(total, 4),
            "by_provider": {k: round(v, 4) for k, v in data["by_provider"].items()},
            "by_mission": {k: round(v, 4) for k, v in data["by_mission"].items()},
            "cap_usd": cap,
            "over_cap": bool(over),
            "warn": bool(warn),
        }


def set_cap(usd: float | None) -> None:
    with _LOCK:
        data = _load()
        if usd is None or usd <= 0:
            data.pop("cap_usd", None)
        else:
            data["cap_usd"] = float(usd)
        _save(data)


def get_cap() -> float | None:
    with _LOCK:
        return _load().get("cap_usd")


def guard_ok() -> bool:
    """False when the monthly cap is set AND exceeded — caller should drop to
    the local CPU fallback (fail-closed). No cap set → always True."""
    s = summary()
    return not s["over_cap"]
