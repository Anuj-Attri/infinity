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


async def resolve_account(request: Any = None) -> Account:
    """Resolve the caller to exactly one account, or raise.

    THIS IS THE SWAP POINT. When Supabase Auth lands (M1) this verifies the
    session JWT, looks the row up in OUR `accounts` table by `external_auth_id`
    (the provider's id lives in that column and nowhere else, so the provider
    stays swappable — Supabase now, WorkOS when SSO/SCIM is needed), and returns
    it. Everything downstream already takes an Account, so nothing else changes.

    Order per DIRECTION: authenticate -> resolve account -> check entitlement ->
    run.
    """
    # --- M1 replaces this block -------------------------------------------
    if _DEV_ENABLED:
        return DEV_ACCOUNT
    raise NotAuthenticated("No verified session on this request.")


async def require_account(request: Any = None) -> Account:
    """FastAPI-dependency form. Add the entitlement gate here in M6 so that a
    single dependency covers authenticate + resolve + entitle for every route
    that costs money."""
    return await resolve_account(request)
