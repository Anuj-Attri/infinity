"""Retrieval — this module IS the product.

Product law #2 (non-negotiable): permissions are enforced per-user AT RETRIEVAL.
If the caller cannot see the document, the model never saw it.

That "at retrieval" is the whole design. Filtering after generation is redaction,
not permission: it leaks through summaries, embeddings and citations. The Copilot
failure DIRECTION cites — salary data surfaced through misconfigured SharePoint
permissions — is exactly the bug that happens when the model reads first and the
filter runs second.

THERE IS ONE RETRIEVAL PATH. No unscoped variant exists, not for admin, not for
debugging, not "temporarily". If a caller needs different data, it passes a
different Account.

TWO LAYERS, ALWAYS — either one alone fails open:
  1. Application predicate (this module) — in the query. Fast, testable, and
     what `tests/test_tenancy_leak.py` exercises.
  2. RLS policy (migrations) — the same rule as Postgres policy, so a single
     forgotten filter cannot become a breach.

⚠ THE FOOTGUN THAT MAKES LAYER 2 IMAGINARY: Supabase's `service_role` key
BYPASSES RLS entirely, and a FastAPI backend is exactly the thing that connects
with `service_role`. You can write perfect policies, deploy, and enforce nothing
— while the dashboard cheerfully reports "RLS enabled". Either forward the
user's JWT so policies evaluate per-request, or treat RLS strictly as a backstop
and make the application predicate mandatory. Never assume RLS is running just
because it is switched on.
"""

from __future__ import annotations

from dataclasses import dataclass

from .accounts import Account, Scope


@dataclass(frozen=True)
class Chunk:
    """A retrieved passage. `source_url` and `document_title` are what make an
    answer citable — product law #1 says an answer without a source is a bug, so
    they travel WITH the text rather than being looked up afterwards."""

    id: str
    document_id: str
    document_title: str
    source_url: str | None
    content: str
    scope: Scope
    score: float


# The authorisation predicate, in one place, in words. The SQL in the query and
# the RLS policy in the migration are both implementations OF THIS. If they ever
# disagree, this comment is right and the code is wrong.
#
#   org_id = :account_org
#   AND (
#         scope = 'company'
#      OR (scope = 'personal'   AND owner_account_id = :account_id)
#      OR (scope = 'department' AND department_id = ANY(:account_department_ids))
#   )
#
# Note what is absent: no "OR is_admin", no bypass flag. An org admin reading a
# user's personal scope would break the key-custody claim the product is sold on.
VISIBILITY_PREDICATE = """
    org_id = %(account_org)s
    AND (
          scope = 'company'
       OR (scope = 'personal'   AND owner_account_id = %(account_id)s)
       OR (scope = 'department' AND department_id = ANY(%(department_ids)s))
    )
"""


def visibility_params(account: Account) -> dict[str, object]:
    """Bind parameters for VISIBILITY_PREDICATE. Deriving these from the Account
    — rather than from anything on the request — is what keeps a caller from
    widening their own scope."""
    return {
        "account_org": account.org_id,
        "account_id": account.id,
        "department_ids": list(account.department_ids),
    }


async def retrieve(
    question: str,
    *,
    account: Account,
    k: int = 8,
    scopes: tuple[Scope, ...] | None = None,
) -> list[Chunk]:
    """Return the k best chunks THIS ACCOUNT is entitled to read.

    `account` is keyword-only and required: it must be impossible to call this
    without naming who is asking, because the failure mode of forgetting is
    silent — the wrong person gets a correct-looking, correctly-cited answer.

    `scopes` may NARROW the search (e.g. company-only), never widen it; anything
    outside `account.scopes` is dropped.

    Empty is a legitimate answer. Never widen scope to fill a result set — an
    empty retrieval is what triggers the refusal that product law #3 requires.
    """
    allowed = tuple(s for s in (scopes or account.scopes) if account.may_read(s))
    if not allowed:
        return []

    raise NotImplementedError(
        "M3: implement pgvector search over `chunks` using VISIBILITY_PREDICATE. "
        "Until then tests/test_tenancy_leak.py fails, which is correct — the "
        "target exists before the thing that satisfies it."
    )
