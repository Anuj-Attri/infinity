"""Device authentication layer.

Two layers:
  1. The door (gatekeeper.py) decides whether a caller reaches the app at all.
  2. Master password + long-lived per-device tokens — THIS module.

Flow:
  - First run: no password set. POST /auth/setup establishes it.
  - A device authenticates once with the password (POST /auth/login) and
    receives a long-lived token. The device sends `Authorization: Bearer
    <token>` on every subsequent request. Tokens never expire; they are
    only invalidated when explicitly revoked (a device, or all of them).

Storage (both DPAPI-encrypted at rest via providers.keystore):
  .data/auth.json    -> {"password_hash": "<bcrypt>"}
  .data/tokens.json  -> {"<token>": {id, device_name, created_at, last_seen_at}}

Security posture (mirrors the BYOK key store):
  - The password is bcrypt-hashed; the plaintext is never stored.
  - Tokens are 32 random bytes, urlsafe-base64. Never logged, never echoed
    back except the one time a token is minted at login.
  - list_devices() NEVER returns full tokens — only a short masked preview
    and a stable opaque id used for revocation.
  - Auth-failure reasons are NOT distinguished to callers; main.py returns
    a generic 401. This module may log specifics for local debugging only.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

import bcrypt

from .providers import keystore
from .paths import data_dir

log = logging.getLogger(__name__)

# .data lives at repo-root/.data — three parents up from this file
# (app/auth.py -> app -> backend -> repo-root).
_DATA_DIR = data_dir()
_AUTH_PATH = _DATA_DIR / "auth.json"
_TOKENS_PATH = _DATA_DIR / "tokens.json"

# last_seen_at is updated in memory on every authenticated request but only
# flushed to disk at most this often per token — a hot endpoint (polling
# tiles, chat streams) must not rewrite the encrypted file on every call.
_LAST_SEEN_PERSIST_THROTTLE = 30.0

# In-memory caches. Loaded once at startup (init_from_disk) and kept warm.
_pw_hash: str | None = None
_tokens: dict[str, dict[str, Any]] = {}
_last_persist_at: float = 0.0
_loaded = False


# ---------------------------------------------------------------- disk ---

def _read_encrypted_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        b64 = path.read_text(encoding="utf-8").strip()
    except Exception:
        return {}
    if not b64:
        return {}
    plain = keystore.decrypt_blob(b64)
    if plain is None:
        # Wrong user/machine or corruption. Treat as empty rather than
        # crashing; the user re-runs first-run setup.
        log.warning("%s could not be decrypted; treating as empty", path.name)
        return {}
    try:
        data = json.loads(plain)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_encrypted_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    b64 = keystore.encrypt_blob(json.dumps(data))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(b64, encoding="utf-8")
    tmp.replace(path)


def _ensure_loaded() -> None:
    global _pw_hash, _tokens, _loaded
    if _loaded:
        return
    auth = _read_encrypted_json(_AUTH_PATH)
    ph = auth.get("password_hash")
    _pw_hash = ph if isinstance(ph, str) and ph else None
    toks = _read_encrypted_json(_TOKENS_PATH)
    _tokens = {k: v for k, v in toks.items() if isinstance(v, dict)}
    _loaded = True


def init_from_disk() -> None:
    """Warm the caches at startup. Safe to call before the event loop
    exists (pure sync)."""
    global _loaded
    _loaded = False
    _ensure_loaded()
    log.info("auth: password_set=%s devices=%d", _pw_hash is not None, len(_tokens))


def _persist_tokens() -> None:
    global _last_persist_at
    _write_encrypted_json(_TOKENS_PATH, _tokens)
    _last_persist_at = time.time()


# ---------------------------------------------------------------- password ---

def is_password_set() -> bool:
    _ensure_loaded()
    return _pw_hash is not None


def set_master_password(plaintext: str) -> None:
    """Hash + store the master password. Idempotent — replaces any existing
    hash. Caller is responsible for the 'already set' policy gate."""
    global _pw_hash
    _ensure_loaded()
    h = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    _write_encrypted_json(_AUTH_PATH, {"password_hash": h})
    _pw_hash = h


def verify_password(plaintext: str) -> bool:
    _ensure_loaded()
    if not _pw_hash:
        return False
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), _pw_hash.encode("ascii"))
    except Exception:
        return False


def clear_password() -> None:
    global _pw_hash
    _pw_hash = None
    _write_encrypted_json(_AUTH_PATH, {})


# ---------------------------------------------------------------- tokens ---

def _token_preview(token: str) -> str:
    """Display-only masked form. Never the full token."""
    if len(token) <= 8:
        return "..."
    return f"{token[:4]}...{token[-4:]}"


def issue_token(device_name: str) -> str:
    _ensure_loaded()
    token = secrets.token_urlsafe(32)
    now = time.time()
    name = (device_name or "").strip() or "device"
    _tokens[token] = {
        "id":           secrets.token_hex(8),
        "device_name":  name[:60],
        "created_at":   now,
        "last_seen_at": now,
    }
    _persist_tokens()
    return token


def verify_token(token: str) -> dict[str, Any] | None:
    """Return the token's metadata if valid, else None. Updates last_seen_at
    in memory always; flushes to disk at most every _LAST_SEEN_PERSIST_THROTTLE
    seconds so hot paths don't thrash the encrypted file."""
    _ensure_loaded()
    if not token:
        return None
    meta = _tokens.get(token)
    if meta is None:
        return None
    now = time.time()
    meta["last_seen_at"] = now
    if (now - _last_persist_at) >= _LAST_SEEN_PERSIST_THROTTLE:
        try:
            _persist_tokens()
        except Exception:
            pass  # best-effort; in-memory value is still correct
    return meta


def revoke_token(token: str) -> bool:
    _ensure_loaded()
    if token in _tokens:
        del _tokens[token]
        _persist_tokens()
        return True
    return False


def revoke_device(device_id: str) -> bool:
    """Revoke by the opaque per-device id returned to the UI (not the token)."""
    _ensure_loaded()
    for tok, meta in list(_tokens.items()):
        if meta.get("id") == device_id:
            del _tokens[tok]
            _persist_tokens()
            return True
    return False


def revoke_all() -> None:
    global _tokens
    _tokens = {}
    _persist_tokens()


def list_devices() -> list[dict[str, Any]]:
    """Device list for the Settings UI. NEVER includes full tokens."""
    _ensure_loaded()
    out: list[dict[str, Any]] = []
    for tok, meta in _tokens.items():
        out.append({
            "id":            meta.get("id"),
            "device_name":   meta.get("device_name"),
            "created_at":    meta.get("created_at"),
            "last_seen_at":  meta.get("last_seen_at"),
            "token_preview": _token_preview(tok),
        })
    out.sort(key=lambda d: d.get("created_at") or 0)
    return out


def reset_all() -> None:
    """Full lockdown: revoke every device and clear the master password.
    Returns the backend to first-run state."""
    revoke_all()
    clear_password()


def store_backend() -> str:
    return keystore.backend_name()
