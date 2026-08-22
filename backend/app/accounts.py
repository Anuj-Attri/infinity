"""Account ownership — the one seam every piece of state hangs off.

STANDING INVARIANT (DIRECTION.md):
    Every piece of state belongs to exactly one account. No global singletons.
    Any function that reads or writes state takes an explicit account.
    If you cannot name the owner of a row, the schema is wrong.

DIFFERENCE FROM MARK 3 — THIS ONE FAILS CLOSED.
Mark 3 returned a single hardcoded local account, which was correct for a
single-user desktop app and fatal for a hosted one. Here, an unauthenticated
request raises. The dev escape hatch exists so M0/M1 can be built before auth
lands, but it is gated on an explicit env var that must never be set in
production — a missing session must never silently resolve to "somebody".

SCOPES (DIRECTION.md "THREE SCOPES") — knowledge is personal | department |
company, the OneDrive mental model, because every buyer already understands it.
Personal scope is sealed under a key an org admin key cannot open: the privacy
guarantee is KEY CUSTODY, not physical location.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Scope(str, Enum):
    """Which audience a piece of knowledge belongs to.

    Retrieval runs against the scopes the caller is entitled to and NOTHING
    else. Never widen a scope to "fix" empty results — an empty result is a
    correct answer, and product law #3 says we say so.
    """

    PERSONAL = "personal"
    DEPARTMENT = "department"
    COMPANY = "company"


class NotAuthenticated(Exception):
    """No verified session. Callers turn this into a 401 — never into a guess."""


@dataclass(frozen=True)
class Account:
    """Who is asking. Passed explicitly; never read from module state."""

    id: str
    org_id: str
    email: str | None = None
    # Departments this account belongs to. Drives Scope.DEPARTMENT retrieval.
    department_ids: tuple[str, ...] = ()
    # Set by the entitlement check (M6). Deny-by-default: any turn that costs
    # money confirms this first, inside the auth dependency chain — never via a
    # cron-flipped flag, because cron fails silently and gives the product away.
    entitled: bool = True

    @property
    def scopes(self) -> tuple[Scope, ...]:
        """Scopes this account may read. Department scope is only in play when
        the account actually belongs to a department."""
        base = [Scope.PERSONAL, Scope.COMPANY]
        if self.department_ids:
            base.append(Scope.DEPARTMENT)
        return tuple(base)

    def may_read(self, scope: Scope) -> bool:
        return scope in self.scopes


# Dev-only account for M0/M1, before Supabase Auth is wired. Gated so it cannot
# reach production: MARK4_DEV_ACCOUNT must be explicitly set to "1".
_DEV_ENABLED = os.environ.get("MARK4_DEV_ACCOUNT") == "1"
DEV_ACCOUNT = Account(
    id="00000000-0000-0000-0000-000000000001",
    org_id="00000000-0000-0000-0000-0000000000a1",
    email="dev@localhost",
)


# The ONE account this deployment serves while authentication is a single master
# password. This is not a placeholder for "anybody" — it is the honest name for
# the situation: there is exactly one tenant, and its state should be filed under
# an owner rather than under nothing at all.
#
# It is deliberately a FIXED id. When Supabase Auth lands, existing rows already
# carry an owner, so the migration is "map this id to the first real account"
# rather than "invent ownership for a pile of unowned data".
SINGLE_TENANT = Account(
    id="00000000-0000-0000-0000-00000000000f",
    org_id="00000000-0000-0000-0000-0000000000af",
    email=None,
)


async def resolve_account(request: Any = None) -> Account:
    """Resolve the caller to exactly one account, or raise.

    THIS IS THE SWAP POINT. When Supabase Auth lands (M3) this verifies the
    session JWT, looks the row up in OUR `accounts` table by `external_auth_id`
    (the provider's id lives in that column and nowhere else, so the provider
    stays swappable — Supabase now, WorkOS when SSO/SCIM is needed), and returns
    it. Everything downstream already takes an Account, so nothing else changes.

    Order per DIRECTION: authenticate -> resolve account -> check entitlement ->
    run.

    UNTIL THEN, a request with a VERIFIED master-password session resolves to
    SINGLE_TENANT. That is not the same as guessing: the session was checked, and
    this deployment has exactly one tenant, so naming it is more honest than
    pretending there is no owner. A request with NO verified session still
    raises — a missing session must never resolve to somebody.
    """
    # --- M3 replaces this block -------------------------------------------
    if _DEV_ENABLED:
        return DEV_ACCOUNT

    if request is not None:
        from . import auth as auth_layer
        token = ""
        try:
            raw = request.headers.get("authorization") or ""
            if raw.lower().startswith("bearer "):
                token = raw[7:].strip()
        except Exception:
            token = ""
        if token and auth_layer.verify_token(token) is not None:
            return SINGLE_TENANT

    raise NotAuthenticated("No verified session on this request.")


async def require_account(request: Any = None) -> Account:
    """authenticate -> resolve -> ENTITLE, in one dependency.

    The entitlement check lives here rather than in each route because a gate
    that has to be remembered at every callsite is a gate that will be forgotten
    at one of them, and the one that is forgotten is the expensive one.
    """
    account = await resolve_account(request)
    from .entitlements import check
    check(account.id)
    return account
