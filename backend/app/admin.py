"""Operator diagnostics — one place that answers "what is actually working?".

This exists because the alternative is reading container logs. A hosted service
degrades quietly: an unset API key, an embedder that cannot reach anything, a
keystore that silently stopped encrypting. Each of those individually looks like
"the app is a bit broken" from the outside. This endpoint names them.

TWO RULES, both absolute:

1. **Never return a secret's VALUE.** Only whether it is set. A diagnostics page
   that prints keys is a diagnostics page that leaks keys the first time someone
   screenshots it for support.
2. **Never guess.** Every status is derived from something actually checked. A
   green light that is really "we didn't look" is worse than no light.

Auth: this router is NOT in main.py's _AUTH_EXEMPT set, so the app-level
require_auth dependency gates it like every other route.

TODO(M3): once accounts exist, this becomes per-account for spend/usage and
owner-only for the service-wide section.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/admin", tags=["admin"])

Status = Literal["ok", "degraded", "blocked", "off"]


class ConfigItem(BaseModel):
    name: str
    set: bool
    # What stops working without it. Written for someone who did not build this.
    enables: str
    required: bool = False


class Subsystem(BaseModel):
    id: str
    name: str
    status: Status
    detail: str
    # The single thing that would move this to "ok".
    unblock: str | None = None


class Diagnostics(BaseModel):
    service: dict[str, Any]
    config: list[ConfigItem]
    subsystems: list[Subsystem]
    connectors: dict[str, Any]
    auth: dict[str, Any]


def _is_set(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


# name -> (what it enables, is it required for the core product)
_CONFIG_KEYS: list[tuple[str, str, bool]] = [
    ("OPENROUTER_API_KEY", "Every model call. Without it, chat refuses.", True),
    ("ALLOWED_ORIGINS", "Browser access from a separately-hosted frontend. Unset = same-origin only.", False),
    ("SETUP_TOKEN", "First-run claim on a public deployment. Unset on a public URL = setup refused.", False),
    ("NEWS_API_KEY", "The News tile.", False),
    ("SENTRY_DSN", "Error reporting. Enable PII scrubbing BEFORE setting this.", False),
    ("DATABASE_URL", "Postgres. Per-account storage and retrieval (M3+).", False),
    ("SUPABASE_URL", "Auth and storage (M3+).", False),
    ("SUPABASE_SERVICE_ROLE_KEY", "Backend DB access. NOTE: bypasses RLS entirely.", False),
    ("VOYAGE_API_KEY", "Embeddings, and therefore Vault search and citations (M5).", False),
    ("DATA_DIR", "Where state is written. Unset = the container's own disk, which a redeploy WIPES.", False),
    ("MARK4_DEV_ACCOUNT", "DEV ONLY. Must be unset in production — it makes every request resolve to one account.", False),
    ("ALLOW_PLAINTEXT_KEYSTORE", "DEV ONLY. Lets provider keys be written unencrypted.", False),
]


def _subsystems() -> list[Subsystem]:
    """Every status here is checked, not assumed."""
    out: list[Subsystem] = []

    # --- models -------------------------------------------------------------
    has_or = _is_set("OPENROUTER_API_KEY")
    out.append(Subsystem(
        id="models", name="Model routing",
        status="ok" if has_or else "blocked",
        detail=("OpenRouter key present." if has_or else
                "No OpenRouter key. Every chat turn refuses — there is no local fallback lane."),
        unblock=None if has_or else "Set OPENROUTER_API_KEY in the deployment environment.",
    ))

    # --- keystore -----------------------------------------------------------
    try:
        from .providers import keystore
        backend = keystore.backend_name()
        real = backend != "identity"
    except Exception as e:  # pragma: no cover - import guard
        backend, real = f"unavailable ({e})", False
    out.append(Subsystem(
        id="keystore", name="Key storage",
        status="ok" if real else "degraded",
        detail=(f"Encrypting with {backend}." if real else
                "No encrypting backend on this platform (DPAPI is Windows-only). "
                "Provider-key writes are REFUSED rather than stored in the clear; "
                "the auth blob (a bcrypt hash) is still written."),
        unblock=None if real else "M2: KMS envelope encryption. Until then, keys belong in the environment.",
    ))

    # --- embeddings ---------------------------------------------------------
    out.append(Subsystem(
        id="embeddings", name="Embeddings",
        status="blocked",
        detail="No embedding provider is wired. Vault search, memory recall, "
               "skills semantic matching and session overview all raise rather "
               "than returning a zero vector.",
        unblock="M5: Voyage voyage-4 (1024-dim) against Postgres/pgvector.",
    ))

    # --- retrieval / tenancy ------------------------------------------------
    out.append(Subsystem(
        id="retrieval", name="Retrieval with permissions",
        status="blocked",
        detail="retrieve() raises NotImplementedError by design — the acceptance "
               "test exists before the thing that satisfies it.",
        unblock="M5: pgvector search behind VISIBILITY_PREDICATE, with RLS live.",
    ))

    # --- accounts -----------------------------------------------------------
    dev_account = _is_set("MARK4_DEV_ACCOUNT")
    out.append(Subsystem(
        id="accounts", name="Per-account ownership",
        status="degraded" if dev_account else "blocked",
        detail=("MARK4_DEV_ACCOUNT is set — every request resolves to ONE "
                "hardcoded account. This must never be set in production."
                if dev_account else
                "No account layer yet. Auth is the single-tenant master password."),
        unblock="M3: Supabase Auth, orgs/accounts/memberships, entitlement chain.",
    ))

    # --- skills -------------------------------------------------------------
    try:
        from . import skills_engine
        n = len(skills_engine.list_all())
    except Exception as e:
        n = 0
        out.append(Subsystem(id="skills", name="Skills", status="degraded",
                             detail=f"Skill index unavailable: {e}", unblock=None))
    else:
        out.append(Subsystem(
            id="skills", name="Skills",
            status="ok" if n else "degraded",
            detail=f"{n} skill{'' if n == 1 else 's'} indexed.",
            unblock=None if n else "Check that backend/skills reached the image (.dockerignore).",
        ))

    # --- vault --------------------------------------------------------------
    out.append(Subsystem(
        id="vault", name="Vault",
        status="degraded",
        detail="Ingestion works; SEARCH does not, because it needs embeddings. "
               "Storage is the container's ephemeral disk, so it does not "
               "survive a redeploy. Live counts are on the Vault tile.",
        unblock="M5 for search; M4 moves storage to Postgres with owner columns.",
    ))

    # --- persistence --------------------------------------------------------
    from .paths import data_dir, is_ephemeral
    eph = is_ephemeral()
    out.append(Subsystem(
        id="persistence", name="Persistence",
        status="degraded" if eph else "ok",
        detail=(f"SQLite at {data_dir()} on the container's OWN disk. A redeploy "
                f"wipes it — including the master password, so the instance drops "
                f"back to first-run setup after every deploy."
                if eph else
                f"SQLite at {data_dir()} (DATA_DIR is set, so this should be a "
                f"mounted volume that survives a redeploy)."),
        unblock=("Mount a volume and set DATA_DIR to it. M4 then moves this to "
                 "Postgres with org_id/owner_account_id on every table."
                 if eph else
                 "M4: Postgres with org_id/owner_account_id on every table."),
    ))

    return out


@router.get("/diagnostics", response_model=Diagnostics)
async def diagnostics() -> Diagnostics:
    """What is working, what is not, and the one thing that would fix each."""
    from . import auth as auth_layer

    # Connectors — chat providers.
    try:
        from .providers import chat as chat_providers
        prov = chat_providers.status()
    except Exception as e:
        prov = {"error": str(e)}

    # Connectors — MCP.
    try:
        from . import mcp_client
        servers = [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "transport": s.get("transport"),
                "trusted": bool(s.get("trusted")),
                "builtin": bool(s.get("builtin")),
                "tools": s.get("exposed", s.get("tools", 0)),
            }
            for s in (mcp_client.list_servers() or [])
        ]
        mcp = {"enabled": mcp_client.is_enabled(), "servers": servers}
    except Exception as e:
        mcp = {"error": str(e)}

    try:
        devices = len(auth_layer.list_devices())
    except Exception:
        devices = 0

    return Diagnostics(
        service={
            "name": "infinity",
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            # A container reports a Linux platform; a dev box does not. Worth
            # showing, because several behaviours differ by exactly this.
            "containerised": os.path.exists("/.dockerenv") or sys.platform.startswith("linux"),
            "started_at": time.time(),
        },
        config=[
            ConfigItem(name=n, set=_is_set(n), enables=enables, required=req)
            for (n, enables, req) in _CONFIG_KEYS
        ],
        subsystems=_subsystems(),
        connectors={"chat_providers": prov, "mcp": mcp},
        auth={
            "password_set": auth_layer.is_password_set(),
            "store_backend": auth_layer.store_backend(),
            "devices": devices,
        },
    )
