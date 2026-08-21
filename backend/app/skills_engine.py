"""Skill architecture — folders with a SKILL.md, loaded only when the
moment needs them.

    skills/
      daily-brief/
        SKILL.md        <- frontmatter (name/description/triggers) + body
      code-review/
        SKILL.md

Matching per turn, cheapest signal first:
  1. trigger keywords (word-boundary, case-insensitive) — deterministic;
  2. embedding cosine between the skill description and the message
     (local nomic-embed via the existing memory stack) — semantic.

The top matches (bounded by count AND token budget) are injected into the
system prompt as ACTIVE SKILL blocks. The chat stream's `done` event lists
which skills fired, so the UI can show its work.

Disable state lives in .data/skills.json (no secrets). Files are re-read
when their mtime changes — edit a SKILL.md and the next turn has it.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from .paths import data_path

# Mark 3 looked at the REPO ROOT (parents[2]). The image only copies backend/,
# so skills at the repo root simply are not in the container — the engine finds
# nothing, indexes 0, and every turn silently loses its skill blocks. Same shape
# as the `reactor` package relocation. They live under backend/ now: parents[1].
#
# TODO(M4): skills are account-owned state per DIRECTION's ACCOUNT OWNERSHIP
# invariant. This directory is the built-in set; a user's own skills become rows
# with an owner column rather than files on a shared disk.
_REPO_ROOT = Path(__file__).resolve().parents[2]     # <repo> — runtime state (.data)
_BACKEND_ROOT = Path(__file__).resolve().parents[1]  # <repo>/backend — shipped content
SKILLS_DIR = Path(_BACKEND_ROOT / "skills")
_STATE_PATH = data_path("skills.json")

_LOCK = threading.Lock()
_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

MAX_ACTIVE = 2                 # at most N skills injected per turn
SIM_THRESHOLD = 0.62           # cosine gate for semantic matches
BODY_CHAR_BUDGET = 6000        # per-skill body clamp before injection


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str]
    body: str
    path: str
    mtime: float
    enabled: bool = True
    vec: np.ndarray | None = field(default=None, repr=False)
    fired: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "enabled": self.enabled,
            "fired": self.fired,
            "chars": len(self.body),
        }


_skills: dict[str, Skill] = {}
_last_scan = 0.0
_SCAN_EVERY_S = 20.0


def _parse_skill_md(text: str, path: str, mtime: float) -> Skill | None:
    m = _FM_RE.match(text)
    if not m:
        return None
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    name = meta.get("name") or ""
    desc = meta.get("description") or ""
    if not name or not desc:
        return None
    triggers = [t.strip().lower() for t in (meta.get("triggers") or "").split(",")
                if t.strip()]
    body = text[m.end():].strip()
    if not body:
        return None
    return Skill(name=name[:60], description=desc[:400], triggers=triggers[:12],
                 body=body[:BODY_CHAR_BUDGET], path=path, mtime=mtime)


def _load_disabled() -> set[str]:
    try:
        data = json.loads(_STATE_PATH.read_text("utf-8"))
        return set(data.get("disabled") or [])
    except Exception:
        return set()


def _save_disabled(disabled: set[str]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps({"disabled": sorted(disabled)}, indent=2),
                               "utf-8")
    except Exception as e:
        print(f"[skills] state save failed: {e}", flush=True)


def scan(force: bool = False) -> int:
    """Reconcile the in-memory index with skills/ on disk. Cheap; called
    lazily before matching, throttled by _SCAN_EVERY_S."""
    global _last_scan
    now = time.monotonic()
    with _LOCK:
        if not force and now - _last_scan < _SCAN_EVERY_S:
            return len(_skills)
        _last_scan = now
        disabled = _load_disabled()
        seen: set[str] = set()
        if SKILLS_DIR.is_dir():
            for md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
                try:
                    mtime = md.stat().st_mtime
                    key = md.parent.name
                    seen.add(key)
                    cur = _skills.get(key)
                    if cur is not None and cur.mtime == mtime:
                        cur.enabled = key not in disabled
                        continue
                    sk = _parse_skill_md(md.read_text("utf-8"), str(md), mtime)
                    if sk is None:
                        _skills.pop(key, None)
                        continue
                    if cur is not None:
                        sk.fired = cur.fired
                    sk.enabled = key not in disabled
                    _skills[key] = sk
                except Exception as e:
                    print(f"[skills] {md}: {e}", flush=True)
        for gone in set(_skills) - seen:
            _skills.pop(gone, None)
        return len(_skills)


async def _ensure_vec(sk: Skill) -> None:
    if sk.vec is not None:
        return
    try:
        from reactor.memory.embed import embed_text
        v = await embed_text(f"{sk.name}: {sk.description}")
        if v is not None and float(np.linalg.norm(v)) > 0:
            sk.vec = v / np.linalg.norm(v)
    except Exception:
        sk.vec = None  # keyword matching still works


def _trigger_hit(sk: Skill, message_low: str) -> bool:
    for t in sk.triggers:
        if re.search(rf"(?<![\w]){re.escape(t)}(?![\w])", message_low):
            return True
    return False


async def match(message: str) -> list[Skill]:
    """Skills that should be active for this message, best-first,
    bounded by MAX_ACTIVE. Never raises."""
    try:
        scan()
        msg = (message or "").strip()
        if not msg:
            return []
        low = msg.lower()
        candidates: list[tuple[float, Skill]] = []
        keyword_hits: list[Skill] = []
        with _LOCK:
            live = [s for s in _skills.values() if s.enabled]
        for sk in live:
            if _trigger_hit(sk, low):
                keyword_hits.append(sk)
        semantic_pool = [s for s in live if s not in keyword_hits]
        if semantic_pool and len(keyword_hits) < MAX_ACTIVE:
            try:
                from reactor.memory.embed import embed_text
                qv = await embed_text(msg[:1000])
                qn = float(np.linalg.norm(qv))
                if qn > 0:
                    qv = qv / qn
                    for sk in semantic_pool:
                        await _ensure_vec(sk)
                        if sk.vec is None:
                            continue
                        sim = float(np.dot(qv, sk.vec))
                        if sim >= SIM_THRESHOLD:
                            candidates.append((sim, sk))
            except Exception:
                pass
        candidates.sort(key=lambda p: -p[0])
        out = keyword_hits + [sk for _, sk in candidates]
        out = out[:MAX_ACTIVE]
        with _LOCK:
            for sk in out:
                sk.fired += 1
        return out
    except Exception as e:
        print(f"[skills] match failed (non-fatal): {e}", flush=True)
        return []


def render_blocks(skills: list[Skill], char_budget: int = 9000) -> str:
    """System-prompt injection text for the matched skills, budget-clamped."""
    parts: list[str] = []
    used = 0
    for sk in skills:
        block = (f"\n\n=== ACTIVE SKILL: {sk.name} ===\n"
                 f"({sk.description})\n{sk.body}\n=== END SKILL ===")
        if used + len(block) > char_budget:
            break
        parts.append(block)
        used += len(block)
    return "".join(parts)


def list_all() -> list[dict[str, Any]]:
    scan()
    with _LOCK:
        return [s.to_dict() for s in sorted(_skills.values(), key=lambda s: s.name)]


def set_enabled(name: str, enabled: bool) -> bool:
    scan(force=True)
    with _LOCK:
        key = next((k for k, s in _skills.items()
                    if s.name == name or k == name), None)
        if key is None:
            return False
        _skills[key].enabled = enabled
        disabled = _load_disabled()
        (disabled.discard if enabled else disabled.add)(key)
        _save_disabled(disabled)
        return True
