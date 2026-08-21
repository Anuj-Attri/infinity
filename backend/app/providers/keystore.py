"""Encrypted-at-rest BYOK key store.

Windows path uses DPAPI (CryptProtectData / CryptUnprotectData) bound to
the CURRENT USER + machine. The encrypted blob is useless if exfiltrated
(different user/machine = decrypt fails). No new Python dependency —
ctypes calls crypt32.dll directly.

Storage layout — .data/chat_keys.json on disk:

    {
        "anthropic": "<base64 DPAPI ciphertext of the UTF-8 key bytes>",
        "openai":    "<base64 ...>",
        ...
    }

Anything that decrypts cleanly = a usable key. Anything that doesn't =
the user must reconnect via Settings. Failure to decrypt is NEVER fatal
to the process and never leaks the (still-encrypted) blob to logs.

Non-Windows: the implementation falls back to identity encoding with a
clear runtime warning, matching the project's "Windows-only for now"
posture. Swap in a libsecret/keychain backend later if we ever ship
non-Windows builds.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- backend ---

class _CryptoBackend(Protocol):
    name: str
    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, data: bytes) -> bytes: ...


class _IdentityBackend:
    """Last-resort no-op. Logged loudly at use time. Used only if DPAPI
    init fails (non-Windows or a broken host)."""
    name = "identity"
    def protect(self, data: bytes) -> bytes:   return data
    def unprotect(self, data: bytes) -> bytes: return data


class _WindowsDPAPIBackend:
    """ctypes wrapper around crypt32 DPAPI. CurrentUser scope — no
    cross-user, no cross-machine."""
    name = "dpapi"

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [
                ("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char)),
            ]

        self._ctypes = ctypes
        self._DATA_BLOB = DATA_BLOB
        self._crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # signatures
        self._crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR,
            ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(DATA_BLOB),
        ]
        self._crypt32.CryptProtectData.restype = wintypes.BOOL
        self._crypt32.CryptUnprotectData.argtypes = self._crypt32.CryptProtectData.argtypes
        self._crypt32.CryptUnprotectData.restype = wintypes.BOOL
        self._kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel32.LocalFree.restype = ctypes.c_void_p

    def _make_blob(self, data: bytes):
        ctypes = self._ctypes
        buf = ctypes.create_string_buffer(data, len(data))
        blob = self._DATA_BLOB(
            len(data),
            ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)),
        )
        # Keep `buf` alive by returning it alongside the blob — the
        # caller binds both names locally so neither is GC'd before the
        # Win32 call returns.
        return blob, buf

    def protect(self, data: bytes) -> bytes:
        ctypes = self._ctypes
        in_blob, _keep = self._make_blob(data)
        out_blob = self._DATA_BLOB()
        ok = self._crypt32.CryptProtectData(
            ctypes.byref(in_blob), None, None, None, None, 0,
            ctypes.byref(out_blob),
        )
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(f"CryptProtectData failed (GetLastError={err})")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            self._kernel32.LocalFree(out_blob.pbData)

    def unprotect(self, data: bytes) -> bytes:
        ctypes = self._ctypes
        in_blob, _keep = self._make_blob(data)
        out_blob = self._DATA_BLOB()
        ok = self._crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0,
            ctypes.byref(out_blob),
        )
        if not ok:
            err = ctypes.get_last_error()
            raise OSError(f"CryptUnprotectData failed (GetLastError={err})")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            self._kernel32.LocalFree(out_blob.pbData)


def _pick_backend() -> _CryptoBackend:
    if sys.platform.startswith("win"):
        try:
            be = _WindowsDPAPIBackend()
            # smoke test — encrypt + decrypt a sentinel so init-time
            # breakage surfaces here, not at first-use.
            assert be.unprotect(be.protect(b"x")) == b"x"
            return be
        except Exception as e:
            log.warning("DPAPI init failed (%s); falling back to identity store. "
                        "Keys will be stored as-is on disk — DO NOT use premium "
                        "BYOK keys until this is fixed.", e)
    else:
        log.warning("Non-Windows host: DPAPI unavailable, using identity store. "
                    "Premium BYOK keys are stored as-is on disk.")
    return _IdentityBackend()


_BACKEND: _CryptoBackend = _pick_backend()


def backend_name() -> str:
    return _BACKEND.name


# ---------------------------------------------------------------- blob ---
# Generic single-value encrypt/decrypt for callers that manage their own
# file layout (e.g. app/auth.py stores a whole JSON document as one
# encrypted blob, not the per-key dict the BYOK stores use). Same DPAPI
# backend, same posture: ciphertext on disk is useless off this user+machine.

class PlaintextKeystoreRefused(RuntimeError):
    """Raised rather than writing a secret in the clear."""


# On Linux the DPAPI backend does not exist and _pick_backend() returns the
# IDENTITY backend, which does not encrypt anything — it base64s the plaintext
# and calls it ciphertext. Mark 3 logged a warning and carried on, which was
# survivable when the only reader was one person's own laptop.
#
# Hosted, that same path writes master-password hashes, device tokens and
# provider API keys in the clear onto a shared container filesystem, and the
# only signal is a WARNING line in a log nobody reads. `/auth/status` on the
# deployed service currently reports store_backend = "identity", so this is not
# hypothetical.
#
# So the WRITE path fails closed. Reads still work (there is nothing to lose by
# reading a value that was never protected), and the service still boots — but
# it cannot be talked into persisting a secret it cannot protect.
#
# The escape hatch is explicit and named, for local Linux development where
# there is nothing real to lose. Real key custody is M2: envelope encryption
# with a KMS-held key, at which point this whole module is replaced.
_ALLOW_PLAINTEXT = os.environ.get("ALLOW_PLAINTEXT_KEYSTORE") == "1"


def _assert_can_protect() -> None:
    if _BACKEND.name == "identity" and not _ALLOW_PLAINTEXT:
        raise PlaintextKeystoreRefused(
            "Refusing to store a secret: no real keystore is available on this "
            "platform, so this value would be written to disk unencrypted. "
            "Key custody lands at M2 (KMS envelope encryption). For local "
            "development with nothing real at stake, set ALLOW_PLAINTEXT_KEYSTORE=1."
        )


def encrypt_blob(plaintext: str) -> str:
    """Encrypt a UTF-8 string -> base64 ciphertext (ascii).

    Raises PlaintextKeystoreRefused when no backend can actually encrypt.
    """
    _assert_can_protect()
    ct = _BACKEND.protect(plaintext.encode("utf-8"))
    return base64.b64encode(ct).decode("ascii")


def decrypt_blob(b64: str) -> str | None:
    """Decrypt base64 ciphertext -> UTF-8 string. Returns None on any
    failure (wrong user/machine, corruption) — never raises, never logs
    the blob."""
    try:
        ct = base64.b64decode(b64.encode("ascii"))
        return _BACKEND.unprotect(ct).decode("utf-8")
    except Exception:
        return None


# ---------------------------------------------------------------- store ---

@dataclass
class KeyStoreReport:
    """Side-channel for /chat-providers/status callers that want to see
    which (if any) entries failed to decrypt — used to surface a
    'needs reconnecting' hint without leaking the (still-encrypted) blob."""
    needs_reconnect: list[str]


def _data_dir() -> Path:
    return Path(__file__).resolve().parents[3] / ".data"


_CHAT_PATH     = _data_dir() / "chat_keys.json"
_DISPATCH_PATH = _data_dir() / "dispatch_keys.json"


def _looks_like_plaintext_key(s: str) -> bool:
    """Heuristic for legacy migration. Real provider keys are ASCII,
    contain no whitespace, and are >= 16 chars. DPAPI ciphertext is
    base64 of random binary so it qualifies under those rules too —
    we additionally require a known prefix to avoid mis-decoding."""
    if not isinstance(s, str) or len(s) < 16:
        return False
    if any(ch.isspace() for ch in s):
        return False
    return s.startswith(("sk-", "AIza", "sk-ant-", "xai-", "claude-"))


def _load_keys_from(path: Path) -> tuple[dict[str, str], KeyStoreReport]:
    """Generic read + decrypt for a given key-store file."""
    report = KeyStoreReport(needs_reconnect=[])
    if not path.exists():
        return {}, report
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("%s unreadable (%s); treating as empty.", path.name, e)
        return {}, report
    if not isinstance(raw, dict):
        return {}, report

    out: dict[str, str] = {}
    migrate = False
    for name, val in raw.items():
        if not isinstance(val, str) or not val:
            continue
        plain: str | None = None
        try:
            blob = base64.b64decode(val.encode("ascii"))
            plain_bytes = _BACKEND.unprotect(blob)
            plain = plain_bytes.decode("utf-8")
        except Exception:
            if _looks_like_plaintext_key(val):
                log.info("migrating plaintext key for provider=%s to encrypted store", name)
                plain = val
                migrate = True
            else:
                report.needs_reconnect.append(name)
                continue
        out[name] = plain

    if migrate:
        try:
            _save_keys_to(out, path)
        except Exception as e:
            log.warning("post-migration save failed: %s", e)

    return out, report


def _save_keys_to(d: dict[str, str], path: Path) -> None:
    """Generic encrypt + persist for a given key-store file.

    Raises PlaintextKeystoreRefused when no backend can actually encrypt — the
    BYOK path is the one that would otherwise write a live provider API key to
    a shared container disk in the clear.
    """
    _assert_can_protect()
    path.parent.mkdir(parents=True, exist_ok=True)
    enc: dict[str, str] = {}
    for name, value in d.items():
        if not isinstance(value, str) or not value:
            continue
        try:
            ct = _BACKEND.protect(value.encode("utf-8"))
            enc[name] = base64.b64encode(ct).decode("ascii")
        except Exception as e:
            log.warning("failed to encrypt key for provider=%s: %s", name, e)
            continue
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(enc, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# Public API — chat keys (chat_keys.json)

def load_keys() -> tuple[dict[str, str], KeyStoreReport]:
    """Read + decrypt chat keys. Returns (usable_keys, report)."""
    return _load_keys_from(_CHAT_PATH)


def save_keys(d: dict[str, str]) -> None:
    """Encrypt + persist chat keys."""
    _save_keys_to(d, _CHAT_PATH)


# Public API — dispatch keys (dispatch_keys.json)

def load_dispatch_keys() -> tuple[dict[str, str], KeyStoreReport]:
    """Read + decrypt dispatch (sub-agent) keys."""
    return _load_keys_from(_DISPATCH_PATH)


def save_dispatch_keys(d: dict[str, str]) -> None:
    """Encrypt + persist dispatch (sub-agent) keys."""
    _save_keys_to(d, _DISPATCH_PATH)
