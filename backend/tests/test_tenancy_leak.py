"""THE LEAK TEST — account B must never retrieve account A's document.

This file is written to FAIL. That is its job, and M3's definition of done is
"this file passes."

DIRECTION.md calls it "the single most valuable artifact for an investor
conversation." For a product whose entire pitch is access control, the untested
surface IS the product — so the target gets written before the thing that
satisfies it.

Product law #2: permissions are enforced per-user AT RETRIEVAL. If you cannot
see the document, the model never saw it.

WHEN THESE GO GREEN, DELETE THE xfail MARKERS. They are `strict=True`, so an
unexpected PASS is reported as a failure — the suite tells you the moment
tenancy actually lands instead of letting a stale marker hide it.

⚠ These must eventually run against the REAL database with RLS enabled. Passing
against an in-memory stub proves only the application predicate, and the whole
point of two layers is that either one alone fails open.

Run: pytest backend/tests/test_tenancy_leak.py -v
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from app import retrieval
from app.accounts import Account, Scope

# Two accounts in the SAME company — this is the demo the product is built
# toward: same org, same question, one cited answer and one refusal.
ALICE = Account(id="acct-alice", org_id="org-acme", email="alice@acme.test")
BOB = Account(id="acct-bob", org_id="org-acme", email="bob@acme.test")
# A third account in a DIFFERENT company. A cross-account read inside one org is
# a permission bug; across orgs it is a breach.
MALLORY = Account(id="acct-mallory", org_id="org-other", email="mallory@other.test")

SECRET = "The comp band for senior engineers at Acme is 180000 to 220000 USD."
NEEDLE = "180000"
QUESTION = "what's our comp band for senior engineers?"


def _run(coro):
    """Run one coroutine without depending on a pytest-asyncio config."""
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# 1. The seam must exist at all. Checkable TODAY.
# --------------------------------------------------------------------------

def test_retrieve_requires_an_account_keyword_only():
    """Retrieval must be account-scoped IN ITS SIGNATURE, keyword-only.

    If a caller can forget to say who is asking, eventually one will, and the
    failure is silent: the wrong person gets a correct-looking cited answer.
    This one should PASS from day one — the contract is already written.
    """
    sig = inspect.signature(retrieval.retrieve)
    assert "account" in sig.parameters, f"retrieve must take an account: {list(sig.parameters)}"
    p = sig.parameters["account"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY, "account must be keyword-only"
    assert p.default is inspect.Parameter.empty, "account must have NO default"


# --------------------------------------------------------------------------
# 2. The leak itself.
# --------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="M3: pgvector retrieval not implemented yet.")
def test_account_b_cannot_retrieve_account_a_document():
    """Bob must not retrieve Alice's personal document through ANY query.

    Not through the vault, not through a chat turn, not through a tool call,
    not through a citation. This passes before a stranger may sign up.
    """
    _run(retrieval.ingest_text(SECRET, account=ALICE, scope=Scope.PERSONAL))
    hits = _run(retrieval.retrieve(QUESTION, account=BOB))
    leaked = [h for h in hits if NEEDLE in h.content]
    assert not leaked, f"LEAK: Bob retrieved Alice's personal document: {leaked}"


@pytest.mark.xfail(strict=True, reason="M3: cross-org isolation not implemented yet.")
def test_cross_org_account_retrieves_nothing():
    """Mallory is in another company. She sees nothing of Acme's, ever."""
    _run(retrieval.ingest_text(SECRET, account=ALICE, scope=Scope.COMPANY))
    hits = _run(retrieval.retrieve(QUESTION, account=MALLORY))
    assert not hits, f"BREACH: cross-org retrieval returned {len(hits)} chunks"


@pytest.mark.xfail(strict=True, reason="M3: personal-scope key custody not implemented.")
def test_personal_scope_is_not_readable_by_the_org():
    """An org admin key must not open a personal-scope row.

    This is the cryptographic half of the pitch. If it cannot be made to pass,
    "your employer cannot read your personal notes" comes out of the marketing.
    """
    _run(retrieval.ingest_text(SECRET, account=ALICE, scope=Scope.PERSONAL))
    hits = _run(retrieval.retrieve(QUESTION, account=BOB, scopes=(Scope.COMPANY,)))
    leaked = [h for h in hits if NEEDLE in h.content]
    assert not leaked, f"LEAK: personal scope readable at company scope: {leaked}"


# --------------------------------------------------------------------------
# 3. The guard that keeps the leak tests honest.
# --------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="M3: depends on the same retrieval as above.")
def test_owner_can_still_retrieve_their_own_document():
    """Alice CAN read her own document.

    Without this, every test above passes trivially the day retrieval breaks and
    returns nothing. A leak suite that is green because search is dead is worse
    than no suite — it is a false all-clear on the one thing being sold.
    """
    _run(retrieval.ingest_text(SECRET, account=ALICE, scope=Scope.PERSONAL))
    hits = _run(retrieval.retrieve(QUESTION, account=ALICE))
    assert [h for h in hits if NEEDLE in h.content], "Alice cannot read her own doc."


# --------------------------------------------------------------------------
# 4. Laws #1 and #3, as tests rather than prose.
# --------------------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason="M4: citations not wired yet.")
def test_every_answer_carries_a_source():
    """Law #1: an answer without a source is a bug."""
    from app import orchestrator

    result = _run(orchestrator.run_turn(account=ALICE, message=QUESTION))
    assert getattr(result, "citations", None), "No citations — violates law #1."


@pytest.mark.xfail(strict=True, reason="M4: refusal path not wired yet.")
def test_no_retrieval_means_refusal_not_fabrication():
    """Law #3: refusal beats fabrication.

    Bob has no documents for this question. The correct answer is "I don't have
    access to that" — NOT a plausible number from the model's general knowledge.
    This is the half of the two-account demo that makes the other half matter.
    """
    from app import orchestrator

    result = _run(orchestrator.run_turn(account=BOB, message=QUESTION))
    text = str(getattr(result, "text", "")).lower()
    assert NEEDLE not in text, "FABRICATION: model invented the comp band."
    assert any(p in text for p in ("don't have access", "do not have access",
                                   "don't know", "do not know", "no access")), \
        f"Expected a refusal, got: {text[:200]!r}"
